# Library: shared helpers for the set_* tasks in the pyops set.
#
# Loaded via `#!{::set}` from each set_*.task.py. Executed in the task's
# global namespace, so the `config`, `appspace`, `validate_set_name` and
# `CORE_DEPS` globals injected by the job runner for `setname == "pyops"`
# are available here. Every set_* task runs `validate_set_name` on its set
# name before any helper below builds a path from it.
#
# Purpose: lock helpers and subprocess wrappers — stdlib only, no pip
# dependencies, no imports from app.lib.* (scriptlets must stay
# self-contained per pyops conventions). The lock files MUST be the same
# physical paths that app.lib.git.repo_lock and
# app.lib.sets._bootstrap_lock_path use, so pyops-set task work serialises
# correctly against runner-driven job-time installs on the same set.
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager


def _project_root():
    # appspace is <project_root>/app/ — set in job_runner.setup_job_globals
    # when setname == "pyops".
    return os.path.dirname(appspace)


def _git_base():
    base = config["data"]["git"]
    return base if os.path.isabs(base) else os.path.join(_project_root(), base)


def _venv_base():
    base = config["runner"]["venv"]
    return base if os.path.isabs(base) else os.path.join(_project_root(), base)


def _pkg_base():
    base = config["runner"]["pkg"]
    return base if os.path.isabs(base) else os.path.join(_project_root(), base)


def _set_dir(set_name):
    return os.path.join(_git_base(), set_name)


def _venv_dir(set_name):
    return os.path.join(_venv_base(), set_name)


def _pkg_dir(set_name):
    return os.path.join(_pkg_base(), set_name)


@contextmanager
def repo_lock(set_name):
    """Advisory git-repo lock for `set_name`, identical to
    `app.lib.git.repo_lock`: persistent lockfile at
    `<git_base>/{set_name}.lock`, exclusive flock held across the block."""
    git_base = _git_base()
    os.makedirs(git_base, exist_ok=True)
    fd = open(os.path.join(git_base, f"{set_name}.lock"), "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


@contextmanager
def install_lock(set_name):
    """Advisory venv / install lock for `set_name`, identical to
    `app.lib.sets._bootstrap_lock_path` + `set_install_lock`: persistent
    lockfile at `<venv_base>/.locks/{set_name}.lock`, exclusive flock.
    Held by `ensure_set_environment`, `process_code_installs`, and the
    update_deps flow — pyops-set tasks compete on the same path."""
    lock_dir = os.path.join(_venv_base(), ".locks")
    os.makedirs(lock_dir, exist_ok=True)
    fd = open(os.path.join(lock_dir, f"{set_name}.lock"), "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def run_cmd(cmd, cwd=None, check=True):
    """Run `cmd` with stdout/stderr inherited from the current process —
    the job runner has already redirected those to the job's
    stdout.log / stderr.log, so subprocess output is captured there and
    polled live by the UI via /scriptlet/job/item.

    `start_new_session=True` mirrors app.lib.sets so a SIGTERM to the
    pyops process group doesn't reach pip/git mid-flight; the controlled
    shutdown path waits for the subprocess to exit cleanly."""
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        stdout=sys.stdout, stderr=sys.stderr, text=True,
        start_new_session=True,
    )


def status(label):
    """Header line in the task transcript, matches the visual style the
    Pyops update.log uses so the operator's tail looks consistent."""
    print(f"\n=== {label} ===", flush=True)


def normalize_origin_url(set_dir):
    """Strip embedded HTTPS user:pass@ credentials from `origin`'s URL.

    Pre-3.3.0 `set_clone` left credentials baked into the remote URL
    (`https://user:token@host/path.git`), which silently defeats
    credential rotation: editing `.git/credentials` later has no
    effect because git resolves the password from the URL first.

    Called from `set_commit_drafts` and `set_pull` so any set still
    carrying the embedded form migrates to the clean URL on the next
    push or pull. Idempotent: no-op when origin is already clean,
    not HTTPS, or no remote at all. Credentials remain available
    through the `credential.helper` configured by `set_clone`.

    Returns the new (or unchanged) origin URL, or `None` when no
    remote is configured."""
    import re
    import urllib.parse

    try:
        current = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=set_dir, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None  # no origin configured

    if not re.match(r"^https?://[^/]+:[^/]*@", current):
        return current  # already credential-free, or non-HTTPS

    parsed = urllib.parse.urlparse(current)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    clean = parsed._replace(netloc=host).geturl()
    run_cmd(["git", "remote", "set-url", "origin", clean], cwd=set_dir)
    print(f"normalised origin URL (stripped embedded credentials): {clean}", flush=True)
    return clean
