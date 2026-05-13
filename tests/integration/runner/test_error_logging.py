"""Tests for pre-execution error logging in job_runner.py.

This test module ensures that errors occurring before actual job execution are properly logged
and reported, preventing silent failures during setup phases (workspace creation, environment setup,
dependency installation, etc.).
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
import sys
import json
import tempfile


class TestPreExecutionErrorLogging:
    """Tests for error logging during pre-execution phases."""

    @pytest.fixture
    def mock_conf(self, tmp_path):
        """Standard config fixture for job_runner tests."""
        from app.lib import files
        test_db = tmp_path / "test.db"
        test_jobs = tmp_path / "data" / "jobs"
        test_git = tmp_path / "data" / "git"
        test_venv = tmp_path / "data" / "venv"
        test_pkg = tmp_path / "data" / "pkg"
        test_jobs.mkdir(parents=True, exist_ok=True)
        test_git.mkdir(parents=True, exist_ok=True)
        mock_raw = {
            "data": {
                "db": str(test_db),
                "jobs": str(test_jobs),
                "git": str(test_git),
            },
            "runner": {
                "venv": str(test_venv),
                "pkg": str(test_pkg),
            }
        }
        conf_path = tmp_path / "test_pyops.json"
        with open(conf_path, "w") as f:
            json.dump(mock_raw, f)
        files.Config.init(str(conf_path))
        yield

    @pytest.fixture
    def job_env(self, tmp_path):
        """Job environment fixture for testing."""
        data_base = tmp_path / "data"
        data_base.mkdir(parents=True, exist_ok=True)
        git_base = data_base / "git"
        venv_base = tmp_path / "venv"
        pkg_base = tmp_path / "pkg"
        return {
            "conf_data": {
                "jobs": str(data_base),
                "git": str(git_base),
                "db": str(tmp_path / "test.db"),
                "web": {"bind": "127.0.0.1:8000"}
            },
            "conf_runner": {
                "venv": str(venv_base),
                "pkg": str(pkg_base),
            },
            "conf_users": {"anonymous": {"debug_port": 5678}},
            "conf_web": {"bind": "127.0.0.1:8000"}
        }

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    def test_run_task_workspace_setup_failure(self, mock_makedirs, mock_send, job_env):
        """Test that workspace setup failures are logged and reported."""
        from app.job_runner import main as run

        # Simulate makedirs raising an exception (e.g., permission denied)
        mock_makedirs.side_effect = PermissionError("Permission denied: '/data/jobs/owner'")

        run(
            "id", "owner", "set", "name", "task", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, None,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        # Verify error was logged and reported
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing task job id" in call_args["detail"]
        assert "Permission denied" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    def test_run_task_environment_setup_failure(self, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Test that execution environment setup failures are logged and reported."""
        from app.job_runner import main as run

        # Simulate open() failing (e.g., disk full)
        mock_open.side_effect = OSError("Disk full")

        run(
            "id", "owner", "set", "name", "task", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, None,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        # Verify error was logged and reported
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing task job id" in call_args["detail"]
        assert "Disk full" in call_args["detail"]

    # NOTE: install-processing failures are tested against `job_setup.py` in
    # tests/unit/test_job_setup.py — installs no longer run inside
    # `job_runner.main`; they're done in the setup phase before os.execv.

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    def test_run_draft_workspace_setup_failure(self, mock_makedirs, mock_send, job_env):
        """Test that draft job workspace setup failures are logged and reported."""
        from app.job_runner import main as run

        mock_makedirs.side_effect = PermissionError("Permission denied")

        run(
            "id", "owner", "set", "name", "draft", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, None,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing draft job id" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    def test_run_service_workspace_setup_failure(self, mock_makedirs, mock_send, job_env):
        """Test that service job workspace setup failures are logged and reported."""
        from app.job_runner import main as run

        mock_makedirs.side_effect = PermissionError("Permission denied")

        run(
            "id", "owner", "set", "name", "service", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, None,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing service job id" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    def test_run_debug_workspace_setup_failure(self, mock_makedirs, mock_send, job_env):
        """Test that debug job workspace setup failures are logged and reported."""
        from app.job_runner import main as run

        mock_makedirs.side_effect = PermissionError("Permission denied")

        run(
            "id", "owner", "set", "name", "debug", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, 5678,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing debug job id" in call_args["detail"]


class TestDebugJobSpecificErrors:
    """Tests for debug job specific error handling."""

    @pytest.fixture
    def mock_conf(self, tmp_path):
        from app.lib import files
        test_db = tmp_path / "test.db"
        test_jobs = tmp_path / "data" / "jobs"
        test_git = tmp_path / "data" / "git"
        test_venv = tmp_path / "data" / "venv"
        test_pkg = tmp_path / "data" / "pkg"
        test_jobs.mkdir(parents=True, exist_ok=True)
        test_git.mkdir(parents=True, exist_ok=True)
        mock_raw = {
            "data": {"db": str(test_db), "jobs": str(test_jobs), "git": str(test_git)},
            "runner": {"venv": str(test_venv), "pkg": str(test_pkg)}
        }
        conf_path = tmp_path / "test_pyops.json"
        with open(conf_path, "w") as f:
            json.dump(mock_raw, f)
        files.Config.init(str(conf_path))
        yield

    @pytest.fixture
    def job_env(self, tmp_path):
        data_base = tmp_path / "data"
        data_base.mkdir(parents=True, exist_ok=True)
        git_base = data_base / "git"
        venv_base = tmp_path / "venv"
        pkg_base = tmp_path / "pkg"
        return {
            "conf_data": {"jobs": str(data_base), "git": str(git_base), "db": str(tmp_path / "test.db"), "web": {"bind": "127.0.0.1:8000"}},
            "conf_runner": {"venv": str(venv_base), "pkg": str(pkg_base)},
            "conf_users": {"anonymous": {"debug_port": 5678}},
            "conf_web": {"bind": "127.0.0.1:8000"}
        }

    @patch('app.lib.files.conf_users', return_value={'owner': {'debug_port': 5678}})
    @patch('app.lib.job.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    @patch('subprocess.run')
    def test_run_debug_pip_install_failure(self, mock_run, mock_open, mock_chdir, mock_makedirs, mock_send, mock_conf_users, job_env):
        """Test that debugpy installation failures are logged and reported."""
        from app.job_runner import main as run
        from subprocess import CalledProcessError

        # Simulate debugpy install failure
        mock_run.side_effect = CalledProcessError(1, "pip", output="", stderr="no module named debugpy")

        with patch.dict('sys.modules', {'debugpy': Mock()}):
            run(
                "id", "owner", "set", "name", "debug", "", "pass", [],
                "/tmp/pyops/runner.sock", 0, None, 5678,
                job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
            )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Install processing failed for debug job id" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    @patch('subprocess.run')
    def test_run_debug_adapter_execution_failure(self, mock_run, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Test that debugpy adapter execution failures are logged and reported."""
        from app.job_runner import main as run

        # Simulate exec() of debug code failing (e.g., syntax error in generated code)
        def fail_exec(*args, **kwargs):
            raise SyntaxError("Syntax error in generated debug code")

        mock_run.return_value = Mock(returncode=0, stdout="")

        with patch.dict('sys.modules', {'debugpy': Mock()}):
            # Patch the exec function to simulate failure during debug code execution
            with patch('builtins.exec', side_effect=fail_exec):
                run(
                    "id", "owner", "set", "name", "debug", "", "pass", [],
                    "/tmp/pyops/runner.sock", 0, None, 5678,
                    job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
                )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Error executing debug job id" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    @patch('subprocess.run')
    def test_run_debug_invalid_override_json(self, mock_run, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Test that invalid override JSON is logged and reported."""
        from app.job_runner import main as run

        # Simulate debugpy install success but invalid override JSON
        mock_run.return_value = Mock(returncode=0, stdout="")

        with patch.dict('sys.modules', {'debugpy': Mock()}):
            run(
                "id", "owner", "set", "name", "debug", "", "pass", [],
                "/tmp/pyops/runner.sock", 0, '{"invalid": json}', 5678,  # Invalid JSON
                job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
            )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        # The error message should contain either override parsing error or adapter execution error
        # depending on which phase fails first
        assert "Error" in call_args["detail"] and "job id" in call_args["detail"]


class TestNoSilentFailures:
    """Tests to ensure errors cannot silently happen."""

    @pytest.fixture
    def mock_conf(self, tmp_path):
        from app.lib import files
        test_db = tmp_path / "test.db"
        test_jobs = tmp_path / "data" / "jobs"
        test_git = tmp_path / "data" / "git"
        test_venv = tmp_path / "data" / "venv"
        test_pkg = tmp_path / "data" / "pkg"
        test_jobs.mkdir(parents=True, exist_ok=True)
        test_git.mkdir(parents=True, exist_ok=True)
        mock_raw = {
            "data": {"db": str(test_db), "jobs": str(test_jobs), "git": str(test_git)},
            "runner": {"venv": str(test_venv), "pkg": str(test_pkg)}
        }
        conf_path = tmp_path / "test_pyops.json"
        with open(conf_path, "w") as f:
            json.dump(mock_raw, f)
        files.Config.init(str(conf_path))
        yield

    @pytest.fixture
    def job_env(self, tmp_path):
        data_base = tmp_path / "data"
        data_base.mkdir(parents=True, exist_ok=True)
        git_base = data_base / "git"
        venv_base = tmp_path / "venv"
        pkg_base = tmp_path / "pkg"
        return {
            "conf_data": {"jobs": str(data_base), "git": str(git_base), "db": str(tmp_path / "test.db"), "web": {"bind": "127.0.0.1:8000"}},
            "conf_runner": {"venv": str(venv_base), "pkg": str(pkg_base)},
            "conf_users": {"anonymous": {"debug_port": 5678}},
            "conf_web": {"bind": "127.0.0.1:8000"}
        }

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    def test_workspace_error_always_reports_failure(self, mock_makedirs, mock_send, job_env):
        """Ensure workspace errors always result in failure report, never silent."""
        from app.job_runner import main as run

        # Simulate various workspace setup failures
        for error_type in [PermissionError, OSError, FileNotFoundError]:
            mock_makedirs.side_effect = error_type(f"Simulated {error_type.__name__}")
            
            mock_send.reset_mock()
            run(
                "id", "owner", "set", "name", "task", "", "pass", [],
                "/tmp/pyops/runner.sock", 0, None, None,
                job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
            )

            # Verify send_completion was called with failure state
            mock_send.assert_called_once()
            call_args = mock_send.call_args[0][1]
            assert call_args["state"] == "failed", f"Expected 'failed' state for {error_type.__name__}, got {call_args['state']}"
            assert "detail" in call_args, f"Expected 'detail' field for {error_type.__name__}"

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    def test_environment_error_always_reports_failure(self, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Ensure environment setup errors always result in failure report."""
        from app.job_runner import main as run

        # Simulate disk full error
        mock_open.side_effect = OSError("Disk full")

        run(
            "id", "owner", "set", "name", "task", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, None,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "detail" in call_args

    # install-processing failure coverage lives in tests/unit/test_job_setup.py

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    @patch('subprocess.run')
    def test_debug_install_error_always_reports_failure(self, mock_run, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Ensure debugpy install errors always result in failure report."""
        from app.job_runner import main as run
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "pip", output="", stderr="debugpy not found")

        with patch.dict('sys.modules', {'debugpy': Mock()}):
            run(
                "id", "owner", "set", "name", "debug", "", "pass", [],
                "/tmp/pyops/runner.sock", 0, None, 5678,
                job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
            )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "detail" in call_args

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.os.makedirs')
    @patch('app.job_runner.os.chdir')
    @patch('builtins.open')
    @patch('subprocess.run')
    def test_debug_override_error_always_reports_failure(self, mock_run, mock_open, mock_chdir, mock_makedirs, mock_send, job_env):
        """Ensure override parsing errors always result in failure report."""
        from app.job_runner import main as run

        mock_run.return_value = Mock(returncode=0, stdout="")

        with patch.dict('sys.modules', {'debugpy': Mock()}):
            run(
                "id", "owner", "set", "name", "debug", "", "pass", [],
                "/tmp/pyops/runner.sock", 0, '{"broken": json}', 5678,
                job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
            )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "detail" in call_args



class TestFrameworkErrorLogging:
    """Tests that framework errors log to main stderr, not job stderr.log."""

    @pytest.fixture
    def job_env(self, tmp_path):
        data_base = tmp_path / "data"
        data_base.mkdir(parents=True, exist_ok=True)
        git_base = data_base / "git"
        venv_base = tmp_path / "venv"
        pkg_base = tmp_path / "pkg"
        return {
            "conf_data": {
                "jobs": str(data_base),
                "git": str(git_base),
                "db": str(tmp_path / "test.db"),
                "web": {"bind": "127.0.0.1:8000"}
            },
            "conf_runner": {
                "venv": str(venv_base),
                "pkg": str(pkg_base),
            },
            "conf_users": {"anonymous": {"debug_port": 5678}},
            "conf_web": {"bind": "127.0.0.1:8000"}
        }

    @patch("app.job_runner.job_lib.send_completion")
    @patch("app.job_runner.job_lib.terminate_debugpy_adapter", side_effect=Exception("Config not initialized"))
    def test_debug_terminate_error_logs_to_main_stderr(self, mock_terminate, mock_send, job_env):
        """Test that terminate_debugpy_adapter errors log to main stderr before redirect."""
        from app.job_runner import main as run

        run(
            "id", "owner", "set", "name", "debug", "", "pass", [],
            "/tmp/pyops/runner.sock", 0, None, 5678,
            job_env["conf_data"], job_env["conf_runner"], job_env["conf_users"], job_env["conf_web"]
        )

        # Verify error was logged and reported
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Config not initialized" in call_args["detail"]
