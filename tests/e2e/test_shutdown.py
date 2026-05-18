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


def _start_server(tmp_path, worker_count: int = 1):
    """Spawn a fresh gunicorn for this test and return (proc, data_dir)."""
    data_dir = tmp_path / "data"
    for sub in ("jobs", "git", "venv", "pkg"):
        (data_dir / sub).mkdir(parents=True)
    config = {
        "web": {"bind": f"127.0.0.1:{E2E_PORT}", "worker": worker_count, "timeout": 300},
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


def _commit_drafts_on_disk(data_dir, name, msg="setup"):
    """`git add . && git commit` in the set's repo, test-side. Pyops's
    inventory API no longer commits — the only sanctioned commit pathway
    in production is the `set_commit_drafts` task. For shutdown tests we
    can't enqueue that task (the runner under test is what we're trying
    to bring down), so we commit directly via subprocess."""
    repo = data_dir / "git" / name
    env = {"PATH": os.environ["PATH"], "HOME": str(data_dir.parent)}
    subprocess.check_call(
        ["git", "-C", str(repo), "add", "."],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "-q", "-m", msg],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


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
            f"{BASE_URL}/scriptlet/inventory/draft",
            json={"set": set_name, "name": "noop", "code": 'print("x")\n'}, timeout=10,
        ).status_code == 200
        _commit_drafts_on_disk(data_dir, set_name, "add noop")
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
    for required in ("pyzmq", "msgpack", "psutil", "requests"):
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
            f"{BASE_URL}/scriptlet/inventory/draft",
            json={"set": set_name, "name": "noclaim", "code": 'print("ran")\n'}, timeout=10,
        ).status_code == 200
        _commit_drafts_on_disk(data_dir, set_name, "add noclaim")

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
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
        if not log_file.closed:
            log_file.close()


def test_runsh_propagates_sigterm_through_bash_to_gunicorn(tmp_path):
    """run.sh must `exec gunicorn` so docker's SIGTERM (delivered to PID 1
    inside the container — the bash interpreter that started run.sh)
    reaches gunicorn at all.

    Without `exec`, bash holds gunicorn as a child and silently swallows
    SIGTERM (bash's default disposition for foreground children is to
    not forward). gunicorn keeps running, the runner never sees its
    shutdown signal, docker waits out stop_grace_period and SIGKILLs
    the whole container. Internal services then appear in the next boot
    as `failed` with "incomplete execution detected on startup".

    This test reproduces the bash-wrapped invocation pattern by spawning
    gunicorn through bash exactly the way the container's CMD does, then
    SIGTERMs the bash PID — same shape as `docker stop`. The data service
    must reach its `data service stopped` log line.
    """
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

    # Mirror the production container's CMD: bash starts gunicorn via the
    # repo's actual run.sh. After our fix run.sh does `exec gunicorn …`
    # so the gunicorn master inherits PID-1-ness. Before the fix, bash
    # stayed as PID 1 and ate the signal.
    log_path = tmp_path / "server.log"
    log_file = open(log_path, "w")
    env = os.environ.copy()
    env["PYOPS_CONFIG"] = str(config_path)
    # Use the project's venv-bin first so `gunicorn` resolves to the test venv.
    venv_bin = os.path.join(PROJECT_ROOT, ".venv", "bin")
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
    proc = subprocess.Popen(
        ["bash", os.path.join(PROJECT_ROOT, "run.sh")],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    try:
        if not _wait_for_server(BASE_URL, SERVER_STARTUP_TIMEOUT):
            log_file.flush()
            content = log_path.read_text()
            pytest.fail(
                f"PyOps did not start within {SERVER_STARTUP_TIMEOUT}s.\n"
                f"Log tail:\n{content[-2000:]}"
            )

        internal_jobs_dir = data_dir / "jobs" / "internal"
        deadline = time.monotonic() + 60
        data_job_dir = None
        while time.monotonic() < deadline:
            candidates = sorted(internal_jobs_dir.glob("system_internal_data_*")) if internal_jobs_dir.exists() else []
            if candidates:
                latest = candidates[-1]
                stdout_log = latest / "stdout.log"
                if stdout_log.exists() and "data service started" in stdout_log.read_text():
                    data_job_dir = latest
                    break
            time.sleep(0.5)
        assert data_job_dir is not None, "data service did not start in time"

        # SIGTERM the bash PID alone (not the pgid) — that's exactly
        # what `docker stop` does: it signals PID 1 inside the container,
        # nothing else. With `exec`, bash is gone after exec and gunicorn
        # is the PID we're targeting → cascade fires. Without `exec`,
        # bash is still PID 1 and absorbs the signal silently → nothing
        # downstream sees it → test times out, the failure message
        # matches the production observation.
        signal_at = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
            pytest.fail(
                f"PID 1 swallowed SIGTERM: server did not exit within 60s — "
                f"the bash wrapper is not forwarding the signal to gunicorn. "
                f"Make sure run.sh uses `exec gunicorn …`.\n"
                f"Log tail:\n{log_path.read_text()[-3000:]}"
            )
        exit_elapsed = time.monotonic() - signal_at
        log_file.close()

        # The data service's stdout must show the clean-shutdown line —
        # proves SIGTERM made it all the way through bash → gunicorn →
        # runner → service.
        stdout_content = (data_job_dir / "stdout.log").read_text()
        assert "data service stopped" in stdout_content, (
            f"SIGTERM did not propagate through the bash wrapper — data "
            f"service was killed before reaching its clean shutdown line.\n"
            f"stdout.log:\n{stdout_content}\n"
            f"Server log tail:\n{log_path.read_text()[-2000:]}"
        )
        assert exit_elapsed < 30, f"shutdown took {exit_elapsed:.1f}s"
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
        if not log_file.closed:
            log_file.close()


def _poll_in_background(stop_event):
    """Spawn a thread that hammers /scriptlet/job/list at ~10 req/s until
    `stop_event` is set, mirroring the UI's auto-refresh polling that's
    typically active in production at SIGTERM time. Returns the thread."""
    import threading
    def hammer():
        while not stop_event.is_set():
            try:
                requests.get(f"{BASE_URL}/scriptlet/job/list", timeout=2)
            except Exception:
                pass
            time.sleep(0.1)
    t = threading.Thread(target=hammer, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("worker_count", [1, 2, 4])
def test_shutdown_signals_internal_data_service_cleanly(worker_count, tmp_path):
    """Verifies the gunicorn → runner → service shutdown-signal cascade.

    Container-level chain: docker SIGTERM → gunicorn master → on_exit(server)
    → multiprocessing.Process.terminate() → runner subprocess →
    GracefulKiller flips kill_now → main loop exits → _shutdown_job_processes
    → SIGTERM each running job's process tree → ServiceListener's kill_now
    flips → `listen()` returns "TERM" → service's `main()` returns →
    `data service stopped` lands in the per-job stdout.log.

    If any link breaks the data service never reaches its "stopped" line,
    docker SIGKILLs the container at stop_grace_period, and the next
    startup's `handleOddJobs` flips a left-behind `running` row to
    `failed` — visible in production as accumulating
    `system_internal_data_*` `failed` rows with detail
    `"incomplete execution detected on startup"`.

    Regression: this test catches the cascade breaking. Parametrised over
    worker_count so the production-like worker=4 + background HTTP load
    scenario is covered alongside the idle worker=1 baseline.
    """
    import threading
    proc, data_dir, log_file, log_path = _start_server(tmp_path, worker_count=worker_count)
    stop_polling = threading.Event()
    poll_thread = None
    try:
        # Wait for the internal data service to spawn and log its startup
        # line. The runner's schedule loop enqueues it; the runner picks
        # it up; the script logs to data/jobs/internal/<id>/stdout.log.
        internal_jobs_dir = data_dir / "jobs" / "internal"
        deadline = time.monotonic() + 60
        data_job_dir = None
        while time.monotonic() < deadline:
            candidates = sorted(internal_jobs_dir.glob("system_internal_data_*")) if internal_jobs_dir.exists() else []
            if candidates:
                latest = candidates[-1]
                stdout_log = latest / "stdout.log"
                if stdout_log.exists() and "data service started" in stdout_log.read_text():
                    data_job_dir = latest
                    break
            time.sleep(0.5)
        if data_job_dir is None:
            pytest.fail(
                "internal data service did not log 'data service started' within 60s.\n"
                f"Server log tail:\n{log_path.read_text()[-2000:]}"
            )

        # Production-like: an active browser keeps polling /scriptlet/job/list
        # while the operator hits `docker compose down`. This puts in-flight
        # HTTP requests on the workers at the moment SIGTERM lands.
        poll_thread = _poll_in_background(stop_polling)
        time.sleep(0.3)  # let the polling get into a steady rhythm

        # SIGTERM the gunicorn master's process group (same as `docker stop`).
        signal_at = time.monotonic()
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
            pytest.fail(
                f"Server did not exit within 60s of SIGTERM — shutdown cascade "
                f"is blocked. Server log tail:\n{log_path.read_text()[-3000:]}"
            )
        finally:
            log_file.close()

        exit_elapsed = time.monotonic() - signal_at

        # The data service must have logged "stopped" — proves SIGTERM reached
        # it through the full chain. If only "started" is present the service
        # was killed mid-listen (SIGKILL'd at container teardown after
        # everything else hung).
        stdout_content = (data_job_dir / "stdout.log").read_text()
        assert "data service stopped" in stdout_content, (
            f"Data service never logged its clean shutdown line — the "
            f"runner-to-service SIGTERM cascade is broken.\n"
            f"stdout.log contents:\n{stdout_content}\n"
            f"Server log tail:\n{log_path.read_text()[-3000:]}"
        )

        # Exit should be quick (well under docker stop_grace_period). The
        # runner's own _shutdown_job_processes has a 15s SIGKILL escalation
        # so worst-case clean shutdown is ~15s; idle-state shutdown is <5s.
        assert exit_elapsed < 30, (
            f"Shutdown took {exit_elapsed:.1f}s — cascade is propagating but "
            f"slowly. Server log tail:\n{log_path.read_text()[-3000:]}"
        )

    finally:
        stop_polling.set()
        if poll_thread is not None:
            poll_thread.join(timeout=2)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
        if not log_file.closed:
            log_file.close()
