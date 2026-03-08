import os
import sys
import subprocess
from pathlib import Path
import shutil
from urllib.parse import urlparse

from .git import repo_lock, _call, _check
from .files import conf_runner, project_root

def ensure_set_environment(name):
    """
    Ensure per-set venv and pkg directories exist and venv is initialized.
    Creates directories and initializes venv if not present.
    """
    if not name:
        raise ValueError("Set name cannot be empty")
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    pkg_base = runner_conf.get("pkg", "data/pkg")
    venv_path = os.path.join(project_root, venv_base, name)
    pkg_path = os.path.join(project_root, pkg_base, name)
    os.makedirs(venv_path, exist_ok=True)
    if not os.path.exists(os.path.join(venv_path, 'bin', 'python')):
        subprocess.check_call([sys.executable, '-m', 'venv', venv_path])
        pip_path = os.path.join(venv_path, 'bin', 'pip')
        subprocess.check_call([pip_path, 'install', '--upgrade', 'pip', 'setuptools', 'wheel'])
        subprocess.check_call([pip_path, 'install', 'pyzmq', 'msgpack', 'psutil', 'debugpy', 'requests'])
    os.makedirs(pkg_path, exist_ok=True)

def remove_set_environment(name):
    """
    Remove per-set venv and pkg directories.
    """
    if not name:
        raise ValueError("Set name cannot be empty")
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    pkg_base = runner_conf.get("pkg", "data/pkg")
    venv_path = os.path.join(project_root, venv_base, name)
    pkg_path = os.path.join(project_root, pkg_base, name)
    if os.path.exists(venv_path):
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

        ensure_set_environment(name)

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

    ensure_set_environment(name)

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
