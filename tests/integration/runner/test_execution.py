import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY
import os
import sys
from multiprocessing import Queue
import logging

from app.runner import run, process_imports, process_install_lines
from app.lib import pyops

@pytest.fixture
def mock_glob():
    return {
        "userspace": "/mock/userspace",
        "setspace": "/mock/setspace",
        "workspace": "/mock/workspace",
        "control": Mock(),
        "listener": None
    }

@pytest.fixture
def sample_param():
    return "!pip install requests\nprint('param')"

@pytest.fixture
def sample_code():
    return "!{other::lib}\nprint('code')"

@pytest.fixture
def sample_libs():
    return ["lib code"]

class TestExecution:
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.runner.exec')
    def test_run_task_success(self, mock_exec, mock_listener, tmp_path):
        q = Mock()
        mock_listener.return_value = None  # For non-service
        data_base = str(tmp_path / "data")

        run("id", "owner", "set", "name", "task", "param", "code", [], q, data_base, "bind", "/venv", "/pkg")

        mock_exec.assert_has_calls([
            call("param", ANY),
            call("code", ANY)
        ])
        q.put.assert_called_once_with({"id": "id", "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('app.runner.exec')
    def test_run_service_with_listener(self, mock_exec, mock_listener, tmp_path):
        q = Mock()
        mock_listener_instance = Mock()
        mock_listener.return_value = mock_listener_instance
        data_base = str(tmp_path / "data")

        run("id", "owner", "set", "name", "service", "", "code", [], q, data_base, "bind", "/venv", "/pkg")

        mock_listener.assert_called_once_with("id")
        mock_exec.assert_called_once_with("code", ANY)
        q.put.assert_called_once_with({"id": "id", "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('app.runner.exec')
    def test_run_failed_exception(self, mock_exec, mock_listener, tmp_path):
        q = Mock()
        mock_listener.return_value = None
        mock_exec.side_effect = Exception("exec error")
        data_base = str(tmp_path / "data")

        run("id", "owner", "set", "name", "task", "", "code", [], q, data_base, "bind", "/venv", "/pkg")

        mock_exec.assert_called_once_with("code", ANY)
        q.put.assert_called_once_with({"id": "id", "state": "failed", "detail": ANY})

    @patch('app.runner.loadLib')  # loadLib is in app.runner
    def test_process_imports_success(self, mock_loadLib):
        mock_loadLib.return_value = "loaded lib"
        libs = []
        result = process_imports("!{::lib}\nrest", "current_set", False, libs)
        assert "# importing current_set::lib" in result
        assert len(libs) == 1
        assert libs[0] == "loaded lib"
        mock_loadLib.assert_called_once_with("current_set", "lib", False)

    @patch('app.runner.loadLib')
    def test_process_imports_failure(self, mock_loadLib):
        mock_loadLib.side_effect = Exception("load fail")
        libs = []
        result = process_imports("!{::lib}\nrest", "current_set", False, libs)
        assert "!{::lib}" in result  # Kept original
        assert len(libs) == 0
        mock_loadLib.assert_called_once_with("current_set", "lib", False)

    @patch('app.runner.loadLib')
    def test_process_imports_no_match(self, mock_loadLib):
        libs = []
        result = process_imports("no !\nrest", "current_set", False, libs)
        assert "no !" in result
        assert len(libs) == 0
        mock_loadLib.assert_not_called()

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_pip(self, mock_exists, mock_run):
        mock_run.side_effect = [
            Mock(returncode=0, stdout=""),  # list no requests
            Mock(returncode=0)  # install
        ]
        mock_exists.return_value = True  # venv exists
        lines = ["!requests"]
        venv_path = "/venv"
        pkg_path = "/pkg"
        id = "id"
        result = process_install_lines(lines, venv_path, pkg_path, id)
        assert "# !requests" in result[0]
        pip_path = os.path.join(venv_path, 'bin', 'pip')
        mock_run.assert_has_calls([
            call([pip_path, 'list', '--format=freeze'], capture_output=True, text=True, cwd=venv_path),
            call([pip_path, 'install', '-q', 'requests'], capture_output=True, text=True)
        ])

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_clone(self, mock_exists, mock_run):
        mock_run.return_value = Mock(returncode=0)  # clone success
        mock_exists.return_value = False  # repo_dir not exist
        lines = ["! https://repo.git"]
        venv_path = "/venv"
        pkg_path = "/pkg"
        id = "id"
        result = process_install_lines(lines, venv_path, pkg_path, id)
        assert "# ! https://repo.git" in result[0]
        mock_run.assert_called_once_with(['git', 'clone', 'https://repo.git', '/pkg/repo'], capture_output=True, text=True, cwd='/pkg')

    @patch('subprocess.run')
    def test_process_install_lines_git_error(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stderr="clone fail")
        lines = ["! https://fail.git"]
        with pytest.raises(Exception, match="git clone https://fail.git failed: clone fail"):
            process_install_lines(lines, "/venv", "/pkg", "id")

    @patch('subprocess.run')
    def test_process_install_lines_no_bang(self, mock_run):
        lines = ["normal line"]
        result = process_install_lines(lines, "/venv", "/pkg", "id")
        assert result[0] == "normal line"
        mock_run.assert_not_called()

class TestLoggingCapture:
    @pytest.fixture
    def sample_logging_code(self):
        return """
import logging
print('stdout print')
logging.info('INFO message')
logging.warning('WARNING message')
logging.error('ERROR message')
"""

    @pytest.fixture
    def sample_empty_code(self):
        return ""

    @pytest.fixture
    def sample_fail_code(self):
        return "raise ValueError('test error')"

    @pytest.fixture
    def mock_queue(self):
        q = Mock()
        q.put = Mock()
        return q

    def get_log_contents(self, data_base, owner, set_name, job_id):
        workspace = os.path.join(data_base, owner, set_name, job_id)
        stdout_path = os.path.join(workspace, "stdout.log")
        stderr_path = os.path.join(workspace, "stderr.log")
        stdout_content = ""
        if os.path.exists(stdout_path):
            with open(stdout_path, 'r') as f:
                stdout_content = f.read()
        stderr_content = ""
        if os.path.exists(stderr_path):
            with open(stderr_path, 'r') as f:
                stderr_content = f.read()
        return stdout_content, stderr_content

    @patch('app.runner.pyops.ServiceListener')
    def test_run_task_logging_success(self, mock_listener, tmp_path, sample_logging_code, mock_queue):
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "task", "", sample_logging_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    def test_run_service_logging(self, mock_listener, tmp_path, sample_logging_code, mock_queue):
        mock_listener.return_value = Mock()  # For service
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "service", "", sample_logging_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    def test_run_debug_logging(self, mock_listener, tmp_path, sample_logging_code, mock_queue):
        mock_listener.return_value = Mock()  # For debug
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "debug", "", sample_logging_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    def test_run_fail_logging(self, mock_listener, tmp_path, sample_fail_code, mock_queue):
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "task", "", sample_fail_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert stdout_content == ""
        assert "Error executing job id" in stderr_content
        assert "ValueError: test error" in stderr_content
        detail = mock_queue.put.call_args[0][0]["detail"]
        assert "ValueError" in str(detail)
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "failed", "detail": ANY})

    @patch('app.runner.pyops.ServiceListener')
    def test_run_empty_output(self, mock_listener, tmp_path, sample_empty_code, mock_queue):
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "task", "", sample_empty_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert stdout_content == ""
        assert stderr_content == ""
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})
