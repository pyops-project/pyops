import os
import sys
import subprocess
import fcntl
from pathlib import Path
import shutil
from urllib.parse import urlparse

from .git import repo_lock, _call, _check
from .files import conf_runner, project_root

def _set_paths(name):
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    pkg_base = runner_conf.get("pkg", "data/pkg")
    return (
        os.path.join(project_root, venv_base, name),
        os.path.join(project_root, pkg_base, name),
    )

def ensure_set_dirs(name):
    """Create venv/pkg directories synchronously. Cheap, does not bootstrap the venv."""
    if not name:
        raise ValueError("Set name cannot be empty")
    venv_path, pkg_path = _set_paths(name)
    os.makedirs(venv_path, exist_ok=True)
    os.makedirs(pkg_path, exist_ok=True)

def _bootstrap_lock_path(name):
    """Lock file path for the set bootstrap. MUST live outside venv_path —
    `python -m venv venv_path` resets the directory contents, which would unlink
    a lock file inside it and let concurrent callers race past the flock."""
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    lock_dir = os.path.join(project_root, venv_base, ".locks")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f"{name}.lock")

def ensure_set_environment(name):
    """
    Ensure per-set venv and pkg directories exist, and refresh runtime deps
    to their currently-resolvable versions on devpi.

    Called on every job spawn (`runner.py` before Popen) plus from the
    HTTP create/clone BackgroundTask. The bootstrap lock serializes all
    pip-install / venv-creation activity for this set against itself and
    against `remove_set_environment`. Pyops's graceful shutdown waits for
    in-flight pip installs to finish before exiting (gunicorn graceful_timeout
    + runner's kill_now check before each spawn) — pip is never killed
    mid-flight under controlled shutdown, so the venv stays consistent.
    """
    ensure_set_dirs(name)
    venv_path, _ = _set_paths(name)
    with open(_bootstrap_lock_path(name), 'w') as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        # Spawn pip/venv in their own session so a SIGTERM to pyops's process
        # group (killpg from systemd KillMode=control-group, the test conftest,
        # gunicorn graceful shutdown propagating, etc.) does NOT reach pip
        # directly. The caller (gunicorn worker BG task / runner main loop)
        # still blocks in subprocess.check_call until pip exits cleanly, so
        # the in-flight install completes and the venv stays consistent.
        if not os.path.exists(os.path.join(venv_path, 'bin', 'python')):
            subprocess.check_call(
                [sys.executable, '-m', 'venv', venv_path],
                start_new_session=True,
            )
        pip_path = os.path.join(venv_path, 'bin', 'pip')
        # Two-step: pip cannot upgrade itself in the same invocation that
        # installs other packages — its certifi vendor path becomes invalid
        # mid-flight ("Could not find a suitable TLS CA certificate bundle").
        subprocess.check_call(
            [pip_path, 'install', '--upgrade', 'pip', 'setuptools', 'wheel'],
            start_new_session=True,
        )
        subprocess.check_call(
            [pip_path, 'install', '--upgrade', 'pyzmq', 'msgpack', 'psutil', 'debugpy', 'requests'],
            start_new_session=True,
        )

def remove_set_environment(name):
    """
    Remove per-set venv and pkg directories.

    Acquires the bootstrap lock first — otherwise a concurrent
    `ensure_set_environment` (HTTP background task or runner lazy bootstrap)
    might have pip mid-install when we rmtree the venv, leaving pip with
    `[Errno 2] No such file or directory: '.../bin/'` and a corrupt half-deleted
    state. Waiting is the right semantics; the e2e test's 5s UI timeout doesn't
    represent a real-world expectation.
    """
    if not name:
        raise ValueError("Set name cannot be empty")
    venv_path, pkg_path = _set_paths(name)
    if os.path.exists(venv_path):
        with open(_bootstrap_lock_path(name), 'w') as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            shutil.rmtree(venv_path)
    if os.path.exists(pkg_path):
        shutil.rmtree(pkg_path)

def list_sets(git_dir):
    """
    List all sets (Git repos) in data/git/.
    Returns list of dicts: [{"name": str, "branch": str}, ...]
    """
    sets_dir = Path(git_dir)
    if not sets_dir.exists():
        return []
    
    sets = []
    for item in sets_dir.iterdir():
        if item.is_dir():
            git_subdir = item / ".git"
            if git_subdir.exists():
                try:
                    with repo_lock(git_dir, item.name):
                        branch = _check(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(item))
                    sets.append({"name": item.name, "branch": branch})
                except subprocess.CalledProcessError:
                    branch = "unknown"
                    sets.append({"name": item.name, "branch": branch})
    return sets

def clone_set(git_dir, url, username=None, password=None, git_name=None, git_email=None):
    """
    Clone a new set repo into data/git/{name}, set local git config and credentials if provided.
    Name is inferred from the repository URL.
    Raises ValueError if name exists or clone fails.
    """
    # Infer name from URL
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    if not path_parts:
        raise ValueError("Invalid repository URL: cannot determine repository name")
    repo_name = path_parts[-1]
    if repo_name.endswith('.git'):
        repo_name = repo_name[:-4]
    if not repo_name:
        raise ValueError("Invalid repository URL: repository name is empty")

    name = repo_name
    sets_dir = Path(git_dir) / name
    if sets_dir.exists():
        raise ValueError(f"Set '{name}' already exists.")

    # Embed credentials in URL if provided
    clone_url = url
    if username and password:
        parsed = urlparse(url)
        if parsed.scheme == "https":
            clone_url = parsed._replace(netloc=f"{username}:{password}@{parsed.netloc}").geturl()
    
    # Clone and configure under lock
    with repo_lock(git_dir, name):
        _call(["git", "clone", clone_url, name], git_dir)
        sets_dir = Path(git_dir) / name  # Recompute after clone
        
        # Store credentials if provided
        if username and password:
            parsed = urlparse(url)
            cred_line = f"https://{username}:{password}@{parsed.netloc}"
            credentials_path = sets_dir / ".git" / "credentials"
            with open(credentials_path, "w") as f:
                f.write(cred_line + "\n")
            _call(["git", "config", "credential.helper", "store --file .git/credentials"], str(sets_dir))
        
        # Set local git config
        if git_name:
            _call(["git", "config", "user.name", git_name], str(sets_dir))
        if git_email:
            _call(["git", "config", "user.email", git_email], str(sets_dir))

        ensure_set_dirs(name)

    return name

def remove_set(git_dir, name):
    """
    Remove the set directory data/git/{name}.
    Raises ValueError if not found.
    """
    sets_dir = Path(git_dir) / name
    if not sets_dir.exists():
        raise ValueError(f"Set '{name}' not found.")

    with repo_lock(git_dir, name):
        remove_set_environment(name)
        shutil.rmtree(sets_dir)
    return True

def create_set(git_dir, name):
    """
    Create a new empty set directory and initialize Git repo.
    Raises ValueError if name exists.
    """
    sets_dir = Path(git_dir) / name
    if sets_dir.exists():
        raise ValueError(f"Set '{name}' already exists.")

    sets_dir.mkdir(parents=True)

    # Initialize git repo
    from . import git
    git.get_repo_path(name)

    ensure_set_dirs(name)

    return True

def switch_branch(git_dir, name, branch):
    """
    Switch the checked-out branch of the set.
    If branch doesn't exist locally or remotely, creates it from current HEAD.
    If remote exists, pushes the new branch with tracking.
    Raises ValueError if set not found or operations fail.
    """
    sets_dir = Path(git_dir) / name
    if not sets_dir.exists():
        raise ValueError(f"Set '{name}' not found.")

    with repo_lock(git_dir, name):
        # Check if branch exists locally
        try:
            _check(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], str(sets_dir))
            # Branch exists locally, just checkout
            _call(["git", "checkout", branch], str(sets_dir))
            return True
        except subprocess.CalledProcessError:
            pass  # Branch doesn't exist locally

        # Check if branch exists remotely
        try:
            remotes = _check(["git", "remote"], str(sets_dir)).split()
            if "origin" in remotes:
                remote_refs = _check(["git", "ls-remote", "--heads", "origin", branch], str(sets_dir))
                if remote_refs.strip():
                    # Branch exists remotely, fetch and checkout
                    _call(["git", "fetch", "origin"], str(sets_dir))
                    _call(["git", "checkout", "-b", branch, f"origin/{branch}"], str(sets_dir))
                    return True
        except subprocess.CalledProcessError:
            pass  # No remote or branch not remote

        # Branch doesn't exist locally or remotely, create it from current HEAD
        _call(["git", "checkout", "-b", branch], str(sets_dir))

        # Push if remote exists
        try:
            _check(["git", "remote", "get-url", "origin"], str(sets_dir))
            _call(["git", "push", "-u", "origin", branch], str(sets_dir))
        except subprocess.CalledProcessError:
            pass  # No remote or push failed, continue
    return True

def pull_set(git_dir, name):
    """
    Pull the latest changes from the remote repository for the set.
    Raises ValueError if set not found.
    """
    sets_dir = Path(git_dir) / name
    if not sets_dir.exists():
        raise ValueError(f"Set '{name}' not found.")

    from . import git
    repo = git.get_repo_path(name)
    git.pull(repo)
    return True
