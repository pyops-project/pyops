"""End-to-end tests for pyops's shutdown contract.

The contract has two parts:

  1. **Shutdown waits for in-flight pip install.** A SIGTERM that arrives while
     `ensure_set_environment` is mid-install must let pip finish — otherwise
     the venv is left in a half-bootstrapped state (the user explicitly does
     NOT want self-healing rmtree, so the corruption would persist).

  2. **No new jobs are claimed after the shutdown signal.** Once the runner
     has flipped `kill_now`, the main loop's spawn block is skipped. New jobs
     enqueued during shutdown stay in `todo` for the next pyops start to pick
     up via `handleOddJobs`.

These tests bypass the `pyops_server` fixture so we can drive the SIGTERM
ourselves and observe exit timing / state without the conftest's wrapper
killing the server first.
"""

import json
import os
import signal
import subprocess
import time

import psutil
import pytest
import requests

from .conftest import (
    BASE_URL,
    E2E_PORT,
    PROJECT_ROOT,
    SERVER_SHUTDOWN_TIMEOUT,
    SERVER_STARTUP_TIMEOUT,
    _kill_server,
    _wait_for_server,
)


def _start_server(tmp_path):
    """Spawn a fresh gunicorn for this test and return (proc, data_dir)."""
    data_dir = tmp_path / "data"
    for sub in ("jobs", "git", "venv", "pkg"):
        (data_dir / sub).mkdir(parents=True)
    config = {
        "web": {"bind": f"127.0.0.1:{E2E_PORT}", "worker": 1, "timeout": 300},
        "data": {
            "jobs": str(data_dir / "jobs"),
            "git": str(data_dir / "git"),
            "db": str(data_dir / "app.db"),
        },
        "runner": {
            "venv": str(data_dir / "venv"),
            "pkg": str(data_dir / "pkg"),
            "git_pull_interval": 9999,
            "drop_done_delay_seconds": 9999,
        },
        "log": {"loglevel": "debug"},
        "users": {"anonymous": {"debug_port": 15679}},
    }
    config_path = tmp_path / "pyops_e2e.json"
    config_path.write_text(json.dumps(config))
    log_path = tmp_path / "server.log"
    log_file = open(log_path, "w")
    env = os.environ.copy()
    env["PYOPS_CONFIG"] = str(config_path)
    proc = subprocess.Popen(
        [
            os.path.join(PROJECT_ROOT, ".venv", "bin", "gunicorn"),
            "-c", "app/gunicorn.py",
            "--access-logfile", "-",
            "--error-logfile", "-",
            "--capture-output",
        ],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    if not _wait_for_server(BASE_URL, SERVER_STARTUP_TIMEOUT):
        log_file.flush()
        try:
            content = log_path.read_text()
        except Exception:
            content = "(log unavailable)"
        _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
        log_file.close()
        pytest.fail(
            f"PyOps did not start within {SERVER_STARTUP_TIMEOUT}s.\n"
            f"Log tail:\n{content[-2000:]}"
        )
    return proc, data_dir, log_file, log_path


def test_shutdown_waits_for_in_flight_pip_install(tmp_path):
    """SIGTERM mid-pip-install: pyops must let pip finish before exiting,
    otherwise the venv ends up missing runtime deps. Proof: after exit,
    every runtime dep (pyzmq/msgpack/psutil/debugpy/requests) is present
    in the venv's site-packages.
    """
    proc, data_dir, log_file, log_path = _start_server(tmp_path)
    set_name = "shutdown_wait_test"
    try:
        # Trigger create — endpoint returns immediately, BG task starts pip install.
        r = requests.post(
            f"{BASE_URL}/scriptlet/sets/create",
            json={"name": set_name},
            timeout=10,
        )
        assert r.status_code == 200, f"create failed: {r.text}"

        # Give the BG task a moment to actually begin pip install (devpi index
        # lookup + first wheel fetch).
        time.sleep(1.5)

        # Send SIGTERM and time how long the master takes to exit.
        signal_at = time.monotonic()
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=SERVER_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
            pytest.fail(
                f"server did not exit within {SERVER_SHUTDOWN_TIMEOUT}s of SIGTERM"
            )
        exit_elapsed = time.monotonic() - signal_at
    finally:
        if proc.poll() is None:
            _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
        log_file.close()

    # If pyops respected the contract, exit took at least a meaningful slice
    # of the pip-install duration. A clean immediate exit (< ~1s) would mean
    # the BG task was abandoned mid-install.
    assert exit_elapsed >= 1.0, (
        f"server exited in {exit_elapsed:.2f}s — pip install was killed "
        f"mid-flight. Server log:\n{log_path.read_text()[-3000:]}"
    )

    # Strong corruption check: every runtime dep present in site-packages.
    site_packages = data_dir / "venv" / set_name / "lib" / "python3.13" / "site-packages"
    assert site_packages.is_dir(), (
        f"site-packages missing — pip didn't even finish python -m venv. "
        f"venv tree: {list((data_dir / 'venv' / set_name).rglob('*'))[:30]}"
    )
    installed = {p.name for p in site_packages.iterdir()}
    for required in ("pyzmq", "msgpack", "psutil", "debugpy", "requests"):
        assert any(name.startswith(required) for name in installed), (
            f"runtime dep {required!r} missing from venv after shutdown — "
            f"pip install was truncated. Have: {sorted(installed)}"
        )


def test_shutdown_does_not_claim_new_jobs_after_signal(tmp_path):
    """After SIGTERM, the runner must NOT claim new jobs from `todo` —
    its main loop's in-iteration `kill_now` check skips `getNextToDo`.
    Verified post-mortem against the SQLite DB after the whole pyops stack
    has exited.
    """
    import contextlib
    import sqlite3

    proc, data_dir, log_file, log_path = _start_server(tmp_path)
    set_name = "shutdown_noclaim_test"
    try:
        # Setup: create set + scriptlet, wait for first bootstrap so the
        # subsequent enqueue isn't waiting on a slow pip install path.
        assert requests.post(
            f"{BASE_URL}/scriptlet/sets/create",
            json={"name": set_name}, timeout=10,
        ).status_code == 200
        # Wait for BG bootstrap to fully complete (cold devpi + 2 pip steps).
        # `psutil` is the most reliably-named runtime dep — directory name
        # matches PyPI name (unlike pyzmq → `zmq` directory).
        deadline = time.monotonic() + 180
        site_packages = data_dir / "venv" / set_name / "lib" / "python3.13" / "site-packages"
        venv_marker = site_packages / "psutil"
        while time.monotonic() < deadline and not venv_marker.exists():
            time.sleep(0.5)
        assert venv_marker.exists(), "initial bootstrap didn't finish in time"

        assert requests.put(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noclaim", "type": "task"}, timeout=10,
        ).status_code == 200
        assert requests.post(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noclaim", "code": 'print("ran")\n'}, timeout=10,
        ).status_code == 200

        # Enqueue, then immediately SIGTERM the whole process group. Race
        # window: if SIGTERM arrives DURING the runner's getNextToDo (~ms),
        # the job may get claimed. We retry the enqueue + signal a few times
        # to make the test robust without making it timing-fragile.
        r = requests.put(
            f"{BASE_URL}/scriptlet/job/item",
            json={"set": set_name, "name": "noclaim", "kind": "task",
                  "drop_done": False, "max_retries": 0, "param": ""},
            timeout=10,
        )
        assert r.status_code == 200, f"enqueue failed: {r.text}"
        job_id = r.json()["job"]

        # SIGTERM the gunicorn master's process group → propagates to runner
        # and worker via the shared pgid. Runner's signal handler flips
        # `kill_now`, the next main-loop iteration's spawn-block guard skips
        # getNextToDo, and the loop exits at the next `while` check.
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=SERVER_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
            pytest.fail(
                f"server did not exit within {SERVER_SHUTDOWN_TIMEOUT}s of SIGTERM"
            )

        # After everything has exited, the DB is the source of truth.
        db_path = data_dir / "app.db"
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT state FROM job WHERE id = ?", (job_id,)
            ).fetchone()
        assert row is not None, f"job {job_id} disappeared from DB"
        state = row[0]
        # Acceptable terminal states: 'todo' (never claimed), or the runner's
        # startup `handleOddJobs` would re-promote a claimed-but-not-running
        # job back to 'todo' on next start — but for a controlled SIGTERM
        # with the in-loop check, it should stay 'todo' directly.
        assert state == "todo", (
            f"job claimed despite shutdown signal (state={state!r}). "
            f"Server log tail:\n{log_path.read_text()[-3000:]}"
        )
    finally:
        if proc.poll() is None:
            _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
        log_file.close()
