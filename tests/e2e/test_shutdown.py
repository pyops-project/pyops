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

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import time

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


def _setup_set_on_disk(data_dir, name):
    """Materialise a user set on disk (git init + initial commit). Mirrors
    set_create.task.py for fixtures that need a set without going through
    the data-service spawn cost."""
    repo = data_dir / "git" / name
    repo.mkdir(parents=True, exist_ok=False)
    env = {"PATH": os.environ["PATH"], "HOME": str(data_dir.parent)}
    run = lambda cmd: subprocess.check_call(cmd, cwd=str(repo), env=env)
    run(["git", "init", "-q"])
    run(["git", "config", "user.email", "shutdown@example.com"])
    run(["git", "config", "user.name", "shutdown-test"])
    (repo / ".gitignore").write_text("*.bck\n")
    (repo / "README.md").write_text(f"# {name}\n")
    run(["git", "add", ".gitignore", "README.md"])
    run(["git", "commit", "-q", "-m", "init"])


def test_shutdown_waits_for_in_flight_pip_install(tmp_path):
    """SIGTERM mid-pip-install: pyops must let pip finish before exiting,
    otherwise the venv ends up missing runtime deps. We trigger a user-set
    venv bootstrap by enqueueing a job into a fresh set — the runner calls
    `ensure_set_environment(setname)` before Popen, which pip-installs
    CORE_DEPS into a brand-new venv. SIGTERM lands mid-flight; the contract
    says pip must finish before pyops exits.
    """
    proc, data_dir, log_file, log_path = _start_server(tmp_path)
    set_name = "shutdown_pip_test"
    try:
        _setup_set_on_disk(data_dir, set_name)
        assert requests.put(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noop", "type": "task"}, timeout=10,
        ).status_code == 200
        assert requests.post(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noop", "code": 'print("x")\n'}, timeout=10,
        ).status_code == 200
        # Enqueue the job → runner picks it up, calls ensure_set_environment,
        # pip install starts. We don't wait for the job; we want to SIGTERM
        # while pip is mid-install (cold venv = real pip work on aarch64).
        r = requests.put(
            f"{BASE_URL}/scriptlet/job/item",
            json={"set": set_name, "name": "noop", "kind": "task",
                  "drop_done": False, "max_retries": 0, "param": ""},
            timeout=10,
        )
        assert r.status_code == 200, f"enqueue failed: {r.text}"

        # Give pip a head-start so SIGTERM actually catches it in flight.
        time.sleep(2.0)

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
    # the bootstrap was abandoned mid-install.
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
    proc, data_dir, log_file, log_path = _start_server(tmp_path)
    set_name = "shutdown_noclaim_test"
    try:
        # Set + scriptlet on disk; the inventory POST commits the code.
        _setup_set_on_disk(data_dir, set_name)
        assert requests.put(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noclaim", "type": "task"}, timeout=10,
        ).status_code == 200
        assert requests.post(
            f"{BASE_URL}/scriptlet/inventory/item",
            json={"set": set_name, "name": "noclaim", "code": 'print("ran")\n'}, timeout=10,
        ).status_code == 200

        # Prime the per-set venv via an initial job so the second enqueue
        # (whose claim we're testing) isn't racing pip install with SIGTERM.
        r = requests.put(
            f"{BASE_URL}/scriptlet/job/item",
            json={"set": set_name, "name": "noclaim", "kind": "task",
                  "drop_done": False, "max_retries": 0, "param": ""},
            timeout=10,
        )
        prime_job_id = r.json()["job"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            jr = requests.get(f"{BASE_URL}/scriptlet/job/item?id={prime_job_id}", timeout=10)
            if jr.status_code == 200 and jr.json()["state"] in {"done", "failed"}:
                break
            time.sleep(0.5)
        else:
            pytest.fail("prime job did not finish in time")

        # Now enqueue the real job under test, then immediately SIGTERM.
        r = requests.put(
            f"{BASE_URL}/scriptlet/job/item",
            json={"set": set_name, "name": "noclaim", "kind": "task",
                  "drop_done": False, "max_retries": 0, "param": ""},
            timeout=10,
        )
        assert r.status_code == 200, f"enqueue failed: {r.text}"
        job_id = r.json()["job"]

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=SERVER_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
            pytest.fail(
                f"server did not exit within {SERVER_SHUTDOWN_TIMEOUT}s of SIGTERM"
            )

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
