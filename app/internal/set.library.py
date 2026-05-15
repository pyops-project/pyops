# Library: shared helpers for the set_* tasks in the internal set.
#
# Loaded via `#!{::set}` from each set_*.task.py. Executed in the task's
# global namespace, so the `config` and `appspace` globals injected by the
# job runner for `setname == "internal"` are available here.
#
# Purpose: lock helpers and subprocess wrappers — stdlib only, no pip
# dependencies, no imports from app.lib.* (scriptlets must stay
# self-contained per pyops conventions). The lock files MUST be the same
# physical paths that app.lib.git.repo_lock and
# app.lib.sets._bootstrap_lock_path use, so internal-task work serialises
# correctly against runner-driven job-time installs on the same set.
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager


def _project_root():
    # appspace is <project_root>/app/ — set in job_runner.setup_job_globals
    # when setname == "internal".
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
    update_deps flow — internal-set tasks compete on the same path."""
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
