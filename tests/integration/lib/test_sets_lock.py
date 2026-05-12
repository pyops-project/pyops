"""Integration tests for `app.lib.sets.ensure_set_environment` cross-call
serialization via the fcntl bootstrap lock.

The lock is the contract that lets us promise:
  - Two `ensure_set_environment` calls for the same set never run pip install
    concurrently — without it the venv would be torn apart by overlapping
    `python -m venv` / `pip install --upgrade pip` (pip can't replace itself
    while another invocation is reading the same files).
  - `remove_set_environment` waits for any in-flight bootstrap before rmtree,
    so we never delete `bin/` from under a running pip.
"""

import os
import time
import threading

import pytest

from app.lib import files


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    venv_base = tmp_path / 'venv'
    pkg_base = tmp_path / 'pkg'
    venv_base.mkdir()
    pkg_base.mkdir()
    monkeypatch.setattr(files, 'conf_runner', lambda: {
        'venv': str(venv_base), 'pkg': str(pkg_base),
    })
    monkeypatch.setattr(files, 'project_root', '/')
    return {'venv_base': venv_base, 'pkg_base': pkg_base}


def test_ensure_set_environment_serializes_concurrent_callers(lock_env, monkeypatch):
    """Two concurrent `ensure_set_environment` for the same set must serialize:
    the second caller's pip install does not start until the first finishes.
    """
    from app.lib import sets

    # Stub the heavy work with timed markers so we can prove serialization
    # without actually running python -m venv / pip install.
    enter_times = []
    exit_times = []
    lock_for_log = threading.Lock()

    def fake_check_call(cmd, *args, **kwargs):
        # Only the slow bootstrap commands matter for the serialization check.
        if not (len(cmd) >= 2 and ('venv' in cmd or 'install' in cmd)):
            return 0
        with lock_for_log:
            enter_times.append(time.monotonic())
        time.sleep(0.4)  # simulate pip install duration
        with lock_for_log:
            exit_times.append(time.monotonic())
        # python -m venv must actually create bin/python so the next steps don't
        # try to recreate it; emulate by touching the file.
        if 'venv' in cmd:
            venv_path = cmd[-1]
            os.makedirs(os.path.join(venv_path, 'bin'), exist_ok=True)
            open(os.path.join(venv_path, 'bin', 'python'), 'w').close()
        return 0

    monkeypatch.setattr(sets.subprocess, 'check_call', fake_check_call)

    threads = [
        threading.Thread(target=sets.ensure_set_environment, args=('serialized_set',)),
        threading.Thread(target=sets.ensure_set_environment, args=('serialized_set',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Each ensure_set_environment runs three slow steps: python -m venv (only
    # first time, since fake check_call creates bin/python),
    # pip install pip+setuptools+wheel, pip install pyzmq+...
    # The first caller does all three (3 entries). The second sees bin/python and
    # skips python -m venv but still runs both pip install steps (2 entries).
    assert len(enter_times) == 5, f"expected 5 slow steps total, got {len(enter_times)}"

    # Serialization invariant: every step must finish before the next one starts.
    pairs = sorted(zip(enter_times, exit_times))
    for (e1, x1), (e2, _) in zip(pairs, pairs[1:]):
        assert e2 >= x1 - 1e-3, f"step starting at {e2} overlapped previous one ending at {x1}"


def test_remove_set_environment_waits_for_in_flight_bootstrap(lock_env, monkeypatch):
    """remove_set_environment must wait for an in-flight ensure_set_environment
    before rmtree-ing the venv — otherwise pip would explode with
    `[Errno 2] No such file or directory: '.../bin/'` mid-install.
    """
    from app.lib import sets

    bootstrap_done = threading.Event()
    rmtree_started_before_bootstrap = threading.Event()

    def fake_check_call(cmd, *args, **kwargs):
        if len(cmd) >= 2 and ('venv' in cmd or 'install' in cmd):
            time.sleep(0.5)
            if 'venv' in cmd:
                venv_path = cmd[-1]
                os.makedirs(os.path.join(venv_path, 'bin'), exist_ok=True)
                open(os.path.join(venv_path, 'bin', 'python'), 'w').close()
            if 'install' in cmd and 'pyzmq' in cmd:
                bootstrap_done.set()
        return 0

    monkeypatch.setattr(sets.subprocess, 'check_call', fake_check_call)

    def boot():
        sets.ensure_set_environment('removeme_set')

    def remove():
        # Race: start remove while bootstrap is mid-flight.
        time.sleep(0.05)
        if not bootstrap_done.is_set():
            rmtree_started_before_bootstrap.set()
        sets.remove_set_environment('removeme_set')

    t_boot = threading.Thread(target=boot)
    t_remove = threading.Thread(target=remove)
    t_boot.start()
    t_remove.start()
    t_boot.join(timeout=10)
    t_remove.join(timeout=10)

    # remove_started before bootstrap finished — proving the lock waited rather
    # than letting rmtree race against pip.
    assert rmtree_started_before_bootstrap.is_set(), \
        "remove must have started before bootstrap completed (otherwise we're not testing the race)"
    # Bootstrap got to the end (sentinel-equivalent: 'pyzmq' install started)
    # despite the concurrent remove — the lock made remove wait.
    assert bootstrap_done.is_set(), \
        "ensure_set_environment must have completed pyzmq install before remove ran rmtree"
    # Final state: venv is gone (remove ran AFTER bootstrap).
    assert not (lock_env['venv_base'] / 'removeme_set').exists()
