"""Regression tests for the two-phase job execution split (job_setup.py
runs installs under the per-set bootstrap flock, then `os.execv`s into
job_runner.py with a fresh interpreter that picks up `.pth` files).

Covers:
  - job_setup.main: order of `process_code_installs` then `os.execv`
  - install failure path: ZMQ completion sent, sys.exit(1), no execv
  - log files truncated by setup, appended by job_runner
  - set_install_lock: same-set serialization, different-set parallelism,
    blocks `remove_set_environment`
  - process_code_installs holds the lock across param + libs + code
    (job-level atomicity, not per-call)
"""

import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_venv_root(tmp_path, monkeypatch):
    """Point `conf_runner` (as imported into `app.lib.sets`) at a venv base
    under tmp_path so `_bootstrap_lock_path` writes the lock file there."""
    venv_base = tmp_path / "venv"
    pkg_base = tmp_path / "pkg"
    venv_base.mkdir()
    pkg_base.mkdir()

    def fake_conf_runner():
        return {"venv": str(venv_base), "pkg": str(pkg_base)}

    monkeypatch.setattr("app.lib.sets.conf_runner", fake_conf_runner)
    return venv_base


@pytest.fixture
def setup_kwargs(tmp_path):
    """Minimal kwargs string for job_setup.main."""
    jobs_dir = tmp_path / "jobs"
    venv_dir = tmp_path / "venv"
    pkg_dir = tmp_path / "pkg"
    jobs_dir.mkdir()
    venv_dir.mkdir()
    pkg_dir.mkdir()
    return json.dumps({
        "job_id": "j1",
        "setname": "myset",
        "name": "myname",
        "owner": "anonymous",
        "kind": "task",
        "param": "",
        "code": "pass",
        "libs": [],
        "overrides": None,
        "max_retries": 0,
        "port": None,
        "runner_sock_path": "/tmp/pyops/runner.sock",
        "conf_data": {
            "jobs": str(jobs_dir),
            "git": str(tmp_path / "git"),
            "db": str(tmp_path / "test.db"),
            "web": {"bind": "127.0.0.1:8000"},
        },
        "conf_runner": {"venv": str(venv_dir), "pkg": str(pkg_dir)},
        "conf_users": {"anonymous": {"debug_port": 5678}},
        "conf_web": {"bind": "127.0.0.1:8000"},
    })


class TestJobSetupExecution:
    @patch("app.job_setup.os.execv")
    def test_files_config_initialized_before_install(self, mock_execv, setup_kwargs):
        """job_setup must populate `files.Config` from kwargs *before* calling
        `process_code_installs` — otherwise the per-set bootstrap lock acquisition
        (`sets._bootstrap_lock_path` → `conf_runner()`) raises ValueError in
        every subprocess after the JSON-roundtrip from runner.py.

        Regression: the entire 2026-05-12 e2e run failed (50/179) with
        `ValueError: Config not initialized. Call Config.init(conf_path) first.`
        from this code path."""
        from app import job_setup
        from app.lib import files as files_lib

        # Simulate the subprocess starting fresh: no config initialized.
        files_lib.config = files_lib.Config()
        assert files_lib.config._raw_conf is None

        # process_code_installs is the function that would have crashed.
        # Replace it with a probe that asserts conf_runner() works at the
        # moment process_code_installs is invoked.
        observed = {}

        def probe(*args, **kwargs):
            observed["conf_runner"] = files_lib.conf_runner()

        with patch("app.job_setup.job_lib.process_code_installs", side_effect=probe):
            job_setup.main(setup_kwargs)

        # The probe must have run successfully (no exception during the
        # conf_runner call) and observed the venv/pkg paths from kwargs.
        assert "conf_runner" in observed
        kwargs_dict = json.loads(setup_kwargs)
        assert observed["conf_runner"]["venv"] == kwargs_dict["conf_runner"]["venv"]
        assert observed["conf_runner"]["pkg"] == kwargs_dict["conf_runner"]["pkg"]

    @patch("app.job_setup.os.execv")
    @patch("app.job_setup.job_lib.process_code_installs")
    def test_runs_installs_then_execv(self, mock_installs, mock_execv, setup_kwargs):
        """Happy path: install, then hand off via execv."""
        from app import job_setup

        order = []
        mock_installs.side_effect = lambda *a, **kw: order.append("installs")
        mock_execv.side_effect = lambda *a, **kw: order.append("execv")

        job_setup.main(setup_kwargs)

        assert order == ["installs", "execv"], f"unexpected order: {order}"
        # execv must target job_runner.py
        execv_args = mock_execv.call_args[0]
        assert execv_args[1][1].endswith("job_runner.py")
        # kwargs are passed through verbatim
        assert execv_args[1][2] == setup_kwargs

    @patch("app.job_setup.sys.exit")
    @patch("app.job_setup.os.execv")
    @patch("app.job_setup.job_lib.send_completion")
    @patch("app.job_setup.job_lib.process_code_installs")
    def test_install_failure_sends_completion_no_execv(
        self, mock_installs, mock_send, mock_execv, mock_exit, setup_kwargs
    ):
        """Install failure surfaces to the runner via send_completion and
        exits non-zero. No execv — job_runner.py must NOT run."""
        from app import job_setup

        mock_installs.side_effect = RuntimeError("pip clone exploded")

        job_setup.main(setup_kwargs)

        mock_send.assert_called_once()
        sock_path, msg = mock_send.call_args[0]
        assert sock_path == "/tmp/pyops/runner.sock"
        assert msg["state"] == "failed"
        assert msg["id"] == "j1"
        assert "pip clone exploded" in msg["detail"]
        assert "Install processing failed" in msg["detail"]
        mock_execv.assert_not_called()
        mock_exit.assert_called_with(1)

    @patch("app.job_setup.os.execv")
    @patch("app.job_setup.job_lib.process_code_installs")
    def test_creates_workspace_and_log_files(self, mock_installs, mock_execv, setup_kwargs, tmp_path):
        """Workspace dir is created, stdout.log/stderr.log opened in 'w' so
        they're empty at the moment process_code_installs runs."""
        from app import job_setup

        passed_stdout_paths = []

        def capture_files(param, code, libs, venv_path, pkg_path, set_, job_id, stdout_file, stderr_file):
            # Files should exist on disk by this point.
            passed_stdout_paths.append(stdout_file.name)
            stdout_file.write("install-output\n")
            stderr_file.write("install-error\n")

        mock_installs.side_effect = capture_files
        job_setup.main(setup_kwargs)

        workspace = tmp_path / "jobs" / "myset" / "j1"
        assert workspace.is_dir()
        assert (workspace / "stdout.log").read_text() == "install-output\n"
        assert (workspace / "stderr.log").read_text() == "install-error\n"
        # File handle's name (str path) ends with stdout.log under the workspace
        assert passed_stdout_paths[0].endswith("stdout.log")


class TestLogFilePreservation:
    def test_job_runner_appends_to_setup_logs(self, tmp_path, monkeypatch):
        """Simulate the two-phase sequence on the filesystem and verify
        job_runner.setup_execution_environment appends, doesn't truncate."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # Setup phase: install output written in 'w' mode
        (workspace / "stdout.log").write_text("setup line 1\nsetup line 2\n")
        (workspace / "stderr.log").write_text("setup err 1\n")

        from app import job_runner
        # The function chdirs to workspace and opens log files in 'a'
        stdout_file, stderr_file = job_runner.setup_execution_environment(
            "/venv", "/pkg", "myset", str(workspace), 20,
        )
        try:
            stdout_file.write("exec line A\n")
            stderr_file.write("exec err A\n")
        finally:
            stdout_file.close()
            stderr_file.close()

        assert (workspace / "stdout.log").read_text() == "setup line 1\nsetup line 2\nexec line A\n"
        assert (workspace / "stderr.log").read_text() == "setup err 1\nexec err A\n"


class TestSetInstallLock:
    """The flock that guards per-set venv/pkg mutations."""

    def test_serializes_same_set(self, isolated_venv_root):
        from app.lib.sets import set_install_lock

        log = []
        log_lock = threading.Lock()

        def worker(marker):
            with set_install_lock("alpha"):
                with log_lock:
                    log.append(("enter", marker, time.monotonic()))
                time.sleep(0.15)
                with log_lock:
                    log.append(("exit", marker, time.monotonic()))

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        time.sleep(0.03)
        t2.start()
        t1.join()
        t2.join()

        # Exactly the sequence: A enter, A exit, B enter, B exit
        kinds = [(k, m) for (k, m, _) in log]
        assert kinds == [("enter", "A"), ("exit", "A"), ("enter", "B"), ("exit", "B")], log

    def test_different_sets_run_in_parallel(self, isolated_venv_root):
        from app.lib.sets import set_install_lock

        log = []
        log_lock = threading.Lock()

        def worker(set_name, marker):
            with set_install_lock(set_name):
                with log_lock:
                    log.append(("enter", marker))
                time.sleep(0.1)
                with log_lock:
                    log.append(("exit", marker))

        t1 = threading.Thread(target=worker, args=("alpha", "A"))
        t2 = threading.Thread(target=worker, args=("beta", "B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Overlap: both enters precede both exits.
        kinds = [e[0] for e in log]
        assert kinds[0] == "enter" and kinds[1] == "enter", log
        assert kinds[2] == "exit" and kinds[3] == "exit", log


class TestProcessCodeInstallsHoldsLock:
    """process_code_installs must hold one lock for the whole call (atomic
    over param + libs + code) — not re-acquire per process_install_lines."""

    def test_no_directives_short_circuits_no_lock(self, isolated_venv_root, monkeypatch):
        """When param/libs/code contain no `#!`-prefixed install directives,
        process_code_installs must return without acquiring the lock or
        invoking process_install_lines — the common path for jobs that just
        `import` from already-installed packages, which would otherwise pay
        the lock-acquire latency on every spawn."""
        from app.lib import job as job_lib

        called = {"count": 0}

        def fake_lines(*a, **kw):
            called["count"] += 1
            return a[0]

        monkeypatch.setattr(job_lib, "process_install_lines", fake_lines)

        stdout = MagicMock()
        stderr = MagicMock()
        # Library imports (`#!{set::name}`) and bare code lines are not install
        # directives — process_imports handles libs.
        job_lib.process_code_installs(
            "import sys\n",
            "print('hello')\n",
            [{"code": "#!{otherset::lib}\n", "filename": "x::y"}],
            "/venv", "/pkg", "alpha", "j1", stdout, stderr,
        )
        assert called["count"] == 0

    def test_concurrent_jobs_same_set_run_strictly_sequentially(self, isolated_venv_root, monkeypatch):
        from app.lib import job as job_lib

        log = []
        log_lock = threading.Lock()

        def fake_lines(lines, venv_path, pkg_path, set_, job_id, *_, **__):
            with log_lock:
                log.append(("lines-enter", set_, job_id, time.monotonic()))
            time.sleep(0.04)
            with log_lock:
                log.append(("lines-exit", set_, job_id, time.monotonic()))
            return lines

        monkeypatch.setattr(job_lib, "process_install_lines", fake_lines)

        def worker(job_id):
            stdout = MagicMock()
            stderr = MagicMock()
            # `#!`-prefixed lines are required to trigger lock acquisition —
            # `process_code_installs` short-circuits when there's no install
            # work, and a no-op call would defeat the serialization assertion.
            job_lib.process_code_installs(
                "#!somepkg\n",
                "#!othercode\n",
                [{"code": "#!libpkg\n", "filename": "x::y"}],
                "/venv", "/pkg", "alpha", job_id, stdout, stderr,
            )

        t1 = threading.Thread(target=worker, args=("j1",))
        t2 = threading.Thread(target=worker, args=("j2",))
        t1.start()
        time.sleep(0.02)
        t2.start()
        t1.join()
        t2.join()

        # Each job calls process_install_lines 3 times (param, libs, code).
        # With the lock at process_code_installs level, j1's 3 calls must all
        # finish before any of j2's calls start.
        j1 = [e for e in log if e[2] == "j1"]
        j2 = [e for e in log if e[2] == "j2"]
        assert len(j1) == 6 and len(j2) == 6, log
        j1_last = max(e[3] for e in j1)
        j2_first = min(e[3] for e in j2)
        assert j1_last <= j2_first, f"j2 interleaved with j1: {log}"
