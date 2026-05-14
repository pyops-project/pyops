import json
import logging
import os
import re
import sys
import subprocess
import fcntl
from contextlib import contextmanager
from pathlib import Path
import shutil
from urllib.parse import urlparse

from .git import repo_lock, _call, _check
from .files import conf_data, conf_runner, project_root

logger = logging.getLogger(__name__)

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

@contextmanager
def set_install_lock(name):
    """Hold the same exclusive flock that `ensure_set_environment` and
    `remove_set_environment` use. Wrap any per-set venv/pkg mutation
    (directive-driven `pip install` and `git clone/pull`) in this so that:

    - two jobs in the same set cannot race on `pip install` into shared
      `site-packages` (pip has no venv-wide lock, only its cache lock)
    - two jobs in the same set cannot both `git clone` the same `#!<url>`
      directive's target dir
    - an HTTP `remove_set` cannot rmtree the venv while a job's directive
      install is mid-flight

    Different sets remain parallel because each has its own lock file.
    """
    with open(_bootstrap_lock_path(name), 'w') as fd:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

def _bootstrap_lock_path(name):
    """Lock file path for the set bootstrap. MUST live outside venv_path —
    `python -m venv venv_path` resets the directory contents, which would unlink
    a lock file inside it and let concurrent callers race past the flock."""
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    lock_dir = os.path.join(project_root, venv_base, ".locks")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f"{name}.lock")

CORE_DEPS = ('pyzmq', 'msgpack', 'psutil', 'debugpy', 'requests')


def ensure_set_environment(name):
    """
    Ensure per-set venv exists and pyops-internal runtime deps are present.

    Called on every job spawn (`runner.py` before Popen) plus from the HTTP
    create/clone BackgroundTask. The bootstrap lock serializes all
    pip-install / venv-creation activity for this set against itself and
    against `remove_set_environment`. Pyops's graceful shutdown waits for
    in-flight pip installs to finish before exiting (gunicorn graceful_timeout
    + runner's kill_now check before each spawn) — pip is never killed
    mid-flight under controlled shutdown, so the venv stays consistent.

    *Per-job, not upgrade.* Plain `pip install <pkg>`: if a package is
    already installed at any version, pip says "already satisfied" and
    returns in milliseconds — no index lookup, no network round-trip. This
    makes second+ job spawns into the same venv near-free. The explicit
    `update_set_environment` (operator-triggered "Update Dependencies"
    flow) is what runs `pip install --upgrade …` to refresh.
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
        subprocess.check_call(
            [pip_path, 'install', *CORE_DEPS],
            start_new_session=True,
        )

# ---------------------------------------------------------------------------
# Update Dependencies — operator-triggered set-level refresh
# ---------------------------------------------------------------------------


_PIP_PKG_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9_.\-]*)(.*)$')


def _scriptlet_files(set_name: str) -> list[str]:
    """Return absolute paths to all `<name>.<type>.py` scriptlet files in a
    set's git repo. We walk only the top level — pyops's repo layout is flat
    per set (no nested scriptlets). Hidden files (dot-prefixed) are skipped
    so the `.update_warnings.json` artifact we write doesn't get scanned."""
    git_base = conf_data().get("git", os.path.join(project_root, "data/git"))
    set_dir = os.path.join(project_root, git_base, set_name) if not os.path.isabs(git_base) else os.path.join(git_base, set_name)
    if not os.path.isdir(set_dir):
        return []
    out = []
    for entry in sorted(os.listdir(set_dir)):
        if entry.startswith('.'):
            continue
        full = os.path.join(set_dir, entry)
        if os.path.isfile(full) and entry.endswith('.py'):
            out.append(full)
    return out


def _parse_directives(text: str) -> list[dict]:
    """Pull `#!`-prefixed install directives out of a scriptlet's text.

    Mirrors the parser in `job.process_install_lines` so the "Update
    Dependencies" path sees the exact same directives a job would. Library
    imports (`#!{set::name}`) are skipped — those are handled by
    `process_imports`, not installs.

    Returns a list of dicts:
      - `{"kind": "pip", "pkg": <canonical-name>, "spec": <PEP 440 spec or empty>,
         "extra": <list of extra args after the pkg token>, "raw": <full args>}`
      - `{"kind": "git", "url": <url>, "branch": <str or None>}`
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith('#!'):
            continue
        rest = stripped[2:].lstrip()
        if not rest or rest.startswith('{') or '::' in rest:
            continue
        args = rest.split()
        arg1 = args[0]
        if '://' in arg1 or arg1.startswith('git@'):
            out.append({
                'kind': 'git',
                'url': arg1,
                'branch': args[1] if len(args) > 1 else None,
            })
        else:
            m = _PIP_PKG_RE.match(arg1)
            if not m:
                continue
            pkg_name, spec = m.group(1), m.group(2)
            canonical = pkg_name.lower().replace('_', '-')
            out.append({
                'kind': 'pip',
                'pkg': canonical,
                'spec': spec,
                'extra': args[1:],
                'raw': args,
            })
    return out


def _scan_set_directives(set_name: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Walk all scriptlets in `set_name`, extract directives.

    Returns three lists:
      - pip directives, each annotated with `source` (file basename) and
        `mtime` (filesystem mtime of the scriptlet — picks up unstaged edits
        the way git commit time would not)
      - git directives, same annotations
      - skipped/unparseable, with the source for diagnosis (currently unused
        but kept so the caller can surface them if useful)
    """
    pip_d, git_d, skipped = [], [], []
    for path in _scriptlet_files(set_name):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as exc:
            logger.warning("update_set_environment: cannot read %s: %s", path, exc)
            skipped.append({'source': os.path.basename(path), 'reason': str(exc)})
            continue
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = 0.0
        source = os.path.basename(path)
        for d in _parse_directives(text):
            d_with_meta = {**d, 'source': source, 'mtime': mtime}
            if d['kind'] == 'pip':
                pip_d.append(d_with_meta)
            else:
                git_d.append(d_with_meta)
    return pip_d, git_d, skipped


def _resolve_pip_directives(pip_directives: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aggregate pip directives by canonical package name; resolve disagreement
    via newest-mtime-wins.

    Returns `(resolved, conflicts)`:
      - `resolved`: list of `{pkg, spec, extra, source, mtime}` — one per
        unique package, the survivor of any conflict resolution.
      - `conflicts`: list of `{pkg, winner, losers}` for every package whose
        scriptlets disagreed on the install spec. Goes to `/health` + log.

    The "spec" comparison is byte-exact on the entire args tuple (specifier +
    extra args like `--index-url …`). Any disagreement triggers the
    mtime rule — pip's own resolver only ever sees one consistent spec, so
    impossible-constraint situations like `>=2.0` AND `<1.5` are pre-empted
    in pyops rather than surfaced as a pip resolve failure mid-update.
    """
    by_pkg: dict[str, list[dict]] = {}
    for d in pip_directives:
        by_pkg.setdefault(d['pkg'], []).append(d)
    resolved = []
    conflicts = []
    for pkg, entries in by_pkg.items():
        # Group by exact (spec, extra) tuple — if all variants are the same,
        # no conflict and pip-resolver sees the unified spec.
        signature = lambda e: (e['spec'], tuple(e['extra']))
        signatures = {signature(e) for e in entries}
        if len(signatures) == 1:
            # All scriptlets agree (or only one scriptlet uses the package)
            resolved.append(entries[0])
            continue
        # Disagreement → mtime wins
        entries_sorted = sorted(entries, key=lambda e: e['mtime'], reverse=True)
        winner = entries_sorted[0]
        losers = [e for e in entries_sorted[1:] if signature(e) != signature(winner)]
        resolved.append(winner)
        conflicts.append({
            'pkg': pkg,
            'winner': {'spec': winner['spec'], 'extra': winner['extra'], 'source': winner['source'], 'mtime': winner['mtime']},
            'losers': [{'spec': l['spec'], 'extra': l['extra'], 'source': l['source'], 'mtime': l['mtime']} for l in losers],
        })
    return resolved, conflicts


def _resolve_git_directives(git_directives: list[dict]) -> tuple[list[dict], list[dict]]:
    """Same as `_resolve_pip_directives` but for git URLs. Conflict trigger
    is any branch disagreement (including `None` vs explicit branch)."""
    by_url: dict[str, list[dict]] = {}
    for d in git_directives:
        by_url.setdefault(d['url'], []).append(d)
    resolved = []
    conflicts = []
    for url, entries in by_url.items():
        branches = {e['branch'] for e in entries}
        if len(branches) == 1:
            resolved.append(entries[0])
            continue
        entries_sorted = sorted(entries, key=lambda e: e['mtime'], reverse=True)
        winner = entries_sorted[0]
        losers = [e for e in entries_sorted[1:] if e['branch'] != winner['branch']]
        resolved.append(winner)
        conflicts.append({
            'url': url,
            'winner': {'branch': winner['branch'], 'source': winner['source'], 'mtime': winner['mtime']},
            'losers': [{'branch': l['branch'], 'source': l['source'], 'mtime': l['mtime']} for l in losers],
        })
    return resolved, conflicts


def _warnings_path(set_name: str) -> str:
    """Per-set conflict-warnings file. Read by the monitoring router so
    `/health` can surface "this set has unresolved dep conflicts" to a
    polling alert system. Always rewritten on every update run — empty
    when there are no conflicts."""
    git_base = conf_data().get("git", os.path.join(project_root, "data/git"))
    set_dir = os.path.join(project_root, git_base, set_name) if not os.path.isabs(git_base) else os.path.join(git_base, set_name)
    return os.path.join(set_dir, '.update_warnings.json')


def update_set_environment(name: str, stdout_file=None, stderr_file=None) -> dict:
    """Operator-triggered "Update Dependencies" flow for a set.

    Single big step under the same `_bootstrap_lock_path` lock that
    `ensure_set_environment` / `remove_set_environment` / job-time installs
    use — so a click of the button serializes against running jobs and they
    don't race the venv mid-upgrade.

    Steps:
      1. Upgrade pyops core deps (`pip` itself, then `setuptools` + `wheel`,
         then CORE_DEPS) — same two-call dance as the original
         `ensure_set_environment` because pip can't upgrade itself in the
         same invocation that installs other packages.
      2. Walk all scriptlets, gather pip + git `#!` directives.
      3. Resolve conflicts via newest-mtime-wins (mtime = filesystem mtime
         so unstaged edits are honoured, per design).
      4. Build synthetic `#!`-lines from the resolved directives and feed
         to `process_install_lines(..., upgrade=True)` so the same code path
         that runs at job time does the actual installs — just in upgrade
         mode (so pip re-resolves against the index and git pulls run).
      5. Write `<set>/.update_warnings.json` with the conflict list so
         `/health` can surface non-empty conflicts.

    Returns a summary dict (also serialized to the warnings file). Caller
    (the HTTP endpoint) returns this to the operator as the API response.
    """
    if not name:
        raise ValueError("Set name cannot be empty")

    venv_path, _ = _set_paths(name)
    # Ensure venv + dirs exist first; this also creates them on a fresh
    # set that never ran a job. ensure_set_environment is internally locked.
    ensure_set_environment(name)

    # Imported locally to avoid a circular import at module load time
    # (lib.job imports lib.sets for set_install_lock).
    from . import job as job_lib

    pip_directives, git_directives, _skipped = _scan_set_directives(name)
    resolved_pip, pip_conflicts = _resolve_pip_directives(pip_directives)
    resolved_git, git_conflicts = _resolve_git_directives(git_directives)

    # Reconstruct synthetic `#!` lines from the winners — feed to the same
    # process_install_lines used at job time, but in upgrade mode.
    lines = []
    for d in resolved_pip:
        lines.append('#!' + ' '.join(d['raw']) + '\n')
    for d in resolved_git:
        url_part = d['url']
        branch_part = (' ' + d['branch']) if d['branch'] else ''
        lines.append('#!' + url_part + branch_part + '\n')

    # Log conflicts (and even the no-op outcome) so server logs have the
    # full record beyond what the UI button surfaces.
    for c in pip_conflicts:
        logger.warning(
            "update_set_environment[%s] pip conflict: pkg=%s — winner %r (from %s, mtime=%s), losers %s",
            name, c['pkg'],
            c['winner']['spec'] or '(no version)',
            c['winner']['source'], c['winner']['mtime'],
            [(l['spec'] or '(no version)', l['source']) for l in c['losers']],
        )
    for c in git_conflicts:
        logger.warning(
            "update_set_environment[%s] git conflict: url=%s — winner branch=%r (from %s, mtime=%s), losers %s",
            name, c['url'],
            c['winner']['branch'] or '(default branch)',
            c['winner']['source'], c['winner']['mtime'],
            [(l['branch'] or '(default branch)', l['source']) for l in c['losers']],
        )

    pip_path = os.path.join(venv_path, 'bin', 'pip')
    closeable_stdout = closeable_stderr = None
    if stdout_file is None or stderr_file is None:
        # Caller didn't provide log sinks (e.g. invoked from the HTTP
        # endpoint in-process). Send pip output to the standard log file
        # at `<set>/.update.log` so the operator can inspect after the fact.
        git_base = conf_data().get("git", os.path.join(project_root, "data/git"))
        set_dir = os.path.join(project_root, git_base, name) if not os.path.isabs(git_base) else os.path.join(git_base, name)
        os.makedirs(set_dir, exist_ok=True)
        log_path = os.path.join(set_dir, '.update.log')
        stdout_file = closeable_stdout = open(log_path, 'w', 1)
        stderr_file = closeable_stderr = stdout_file  # interleave stderr into the same log

    try:
        with set_install_lock(name):
            # Step 1: pyops core deps in upgrade mode. Two-step is required
            # because pip can't upgrade itself in the same invocation that
            # installs other packages (its certifi vendor path becomes
            # invalid mid-flight, see CORE_DEPS comment in
            # ensure_set_environment).
            subprocess.check_call(
                [pip_path, 'install', '--upgrade', 'pip', 'setuptools', 'wheel'],
                stdout=stdout_file, stderr=stderr_file, start_new_session=True,
            )
            subprocess.check_call(
                [pip_path, 'install', '--upgrade', *CORE_DEPS],
                stdout=stdout_file, stderr=stderr_file, start_new_session=True,
            )

            # Step 2: scriptlet-driven deps via `process_install_lines` in
            # upgrade mode. It is the lower-level helper that does NOT
            # acquire the lock itself (that's `process_code_installs`'s
            # responsibility), so calling it while we hold the lock here
            # is correct — no recursive flock attempt.
            pkg_base = conf_runner().get("pkg", "data/pkg")
            pkg_path = os.path.join(project_root, pkg_base) if not os.path.isabs(pkg_base) else pkg_base
            venv_base = conf_runner().get("venv", "data/venv")
            venv_root = os.path.join(project_root, venv_base) if not os.path.isabs(venv_base) else venv_base
            job_lib.process_install_lines(
                lines, venv_root, pkg_path, name, 'update_set_environment',
                stdout_file, stderr_file, upgrade=True,
            )
    finally:
        if closeable_stdout is not None:
            closeable_stdout.flush()
            closeable_stdout.close()

    summary = {
        'set': name,
        'conflicts': {
            'pip': pip_conflicts,
            'git': git_conflicts,
        },
        'installed': {
            'pip': [{'pkg': d['pkg'], 'spec': d['spec'], 'source': d['source']} for d in resolved_pip],
            'git': [{'url': d['url'], 'branch': d['branch'], 'source': d['source']} for d in resolved_git],
        },
    }
    # Always rewrite — empty conflicts overwrite a previous warning state.
    warnings_path = _warnings_path(name)
    try:
        with open(warnings_path, 'w') as f:
            json.dump(summary, f, indent=2)
    except OSError as exc:
        logger.warning("update_set_environment[%s] failed to write %s: %s", name, warnings_path, exc)

    return summary


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
