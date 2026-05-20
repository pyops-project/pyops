"""Per-set venv/pkg lifecycle for the runner-driven job spawn path.

What lives here after the move of user-facing sets management into
`app/pyops/`:

  - the bootstrap lock helpers (`set_install_lock`, `_bootstrap_lock_path`)
    serialise pip / git / venv mutations within a set so two concurrent
    jobs (or a job versus the operator-triggered set_update_deps task)
    cannot race on shared site-packages
  - `ensure_set_environment` creates the per-set venv + installs CORE_DEPS
    on every job spawn — invoked from `runner.py` before each Popen and
    from the runner startup hook for the pyops set

Set CRUD (clone / create / pull / switch / remove) and Update Dependencies
now live as task scriptlets in `app/pyops/` and run as normal pyops
jobs; they take the SAME bootstrap and repo locks via `fcntl.flock` on
the exact same lock files, so the lock semantics are preserved across the
split.
"""
import fcntl
import logging
import os
import subprocess
import sys
from contextlib import contextmanager

from .files import conf_runner, project_root

logger = logging.getLogger(__name__)


CORE_DEPS = ('pyzmq', 'msgpack', 'psutil', 'requests')


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
    `python -m venv venv_path` resets the directory contents, which would
    unlink a lock file inside it and let concurrent callers race past the
    flock."""
    runner_conf = conf_runner()
    venv_base = runner_conf.get("venv", "data/venv")
    lock_dir = os.path.join(project_root, venv_base, ".locks")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f"{name}.lock")


@contextmanager
def set_install_lock(name):
    """Hold the same exclusive flock that `ensure_set_environment` uses.
    Wrap any per-set venv/pkg mutation (directive-driven `pip install` and
    `git clone/pull`) in this so that:

    - two jobs in the same set cannot race on `pip install` into shared
      `site-packages` (pip has no venv-wide lock, only its cache lock)
    - two jobs in the same set cannot both `git clone` the same `#!<url>`
      directive's target dir
    - the operator-triggered `set_remove` / `set_update_deps` tasks (in
      app/pyops/) take the same flock and serialise against in-flight
      job-time installs

    Different sets remain parallel because each has its own lock file.
    """
    with open(_bootstrap_lock_path(name), 'w') as fd:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


def ensure_set_environment(name):
    """Ensure per-set venv exists and pyops-managed runtime deps are
    present.

    Called on every job spawn (`runner.py` before Popen) plus from the
    runner startup hook for the pyops set. The bootstrap lock
    serialises all pip-install / venv-creation activity for this set
    against itself and against the operator-triggered tasks in
    app/pyops/ that hold the same flock. Pyops's graceful shutdown
    waits for in-flight pip installs to finish before exiting
    (gunicorn graceful_timeout + runner's kill_now check before each
    spawn), so pip is never killed mid-flight under controlled shutdown
    and the venv stays consistent.

    *Per-job, not upgrade.* Plain `pip install <pkg>`: if a package is
    already installed at any version, pip says "already satisfied" and
    returns in milliseconds — no index lookup, no network round-trip.
    The set_update_deps task is what runs `pip install --upgrade …` to
    refresh dependencies on demand.
    """
    ensure_set_dirs(name)
    venv_path, _ = _set_paths(name)
    with open(_bootstrap_lock_path(name), 'w') as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        # Spawn pip/venv in their own session so a SIGTERM to pyops's
        # process group (killpg from systemd KillMode=control-group, the
        # test conftest, gunicorn graceful shutdown propagating, etc.)
        # does NOT reach pip directly. The caller still blocks in
        # subprocess.check_call until pip exits cleanly.
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
