"""Integration tests for `app.lib.sets.ensure_set_environment` cross-call
serialization via the fcntl bootstrap lock.

The lock is the contract that lets us promise:
  - Two `ensure_set_environment` calls for the same set never run pip install
    concurrently — without it the venv would be torn apart by overlapping
    `python -m venv` / `pip install --upgrade pip` (pip can't replace itself
    while another invocation is reading the same files).
  - The set_remove task (app/internal/set_remove.task.py) waits for any
    in-flight bootstrap before rmtree by taking the same fcntl flock on
    the same lock file — coverage for that lives end-to-end against the
    real controller path.
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

    # Per-job ensure_set_environment runs two slow steps the first time:
    #   1. python -m venv (creates bin/python via the fake check_call below)
    #   2. pip install <CORE_DEPS> — single combined call, no --upgrade
    # The second caller sees bin/python and skips python -m venv but still
    # runs the single pip install step (1 entry).
    assert len(enter_times) == 3, f"expected 3 slow steps total, got {len(enter_times)}"

    # Serialization invariant: every step must finish before the next one starts.
    pairs = sorted(zip(enter_times, exit_times))
    for (e1, x1), (e2, _) in zip(pairs, pairs[1:]):
        assert e2 >= x1 - 1e-3, f"step starting at {e2} overlapped previous one ending at {x1}"


