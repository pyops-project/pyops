import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY
import os
import sys
import datetime as real_datetime
from multiprocessing import Queue
import logging


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
    def test_run_task_success(self, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        q = Mock()
        mock_listener.return_value = None  # For non-service
        data_base = str(tmp_path / "data")

        run("id", "owner", "set", "name", "task", "print('param executed')", "print('code executed')", [], q, data_base, "bind", "/venv", "/pkg", overrides=None)

        q.put.assert_called_once_with({"id": "id", "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_task_retry_success_on_third(self, mock_sleep, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        q = Mock()
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        mock_sleep.return_value = None  # No real delay

        # Mock exec to fail first 2 attempts, succeed on 3rd
        exec_mock = Mock()
        with patch('builtins.exec', side_effect=[Exception("fail1"), Exception("fail2"), None]):
            run("id", "owner", "set", "name", "task", "", "print('success')", [], q, data_base, "bind", "/venv", "/pkg", overrides=None)

        assert mock_sleep.call_count == 2  # Sleeps after first two fails
        q.put.assert_called_once_with({"id": "id", "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_task_retry_fail_all(self, mock_sleep, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        q = Mock()
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        mock_sleep.return_value = None

        # Mock exec to always fail
        with patch('builtins.exec', side_effect=Exception("fail")):
            run("id", "owner", "set", "name", "task", "", "raise Exception", [], q, data_base, "bind", "/venv", "/pkg", overrides=None)

        assert mock_sleep.call_count == 3  # Sleeps after each of 3 fails
        call_args = q.put.call_args[0][0]
        assert call_args["state"] == "failed"
        assert "Failed after 3 retries" in call_args["detail"]

    @patch('app.runner.pyops.ServiceListener')
    def test_run_service_with_listener(self, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        q = Mock()
        mock_listener_instance = Mock()
        mock_listener.return_value = mock_listener_instance
        data_base = str(tmp_path / "data")

        run("id", "owner", "set", "name", "service", "", "print('code executed')", [], q, data_base, "bind", "/venv", "/pkg")

        mock_listener.assert_called_once_with("id", current_user="owner")
        q.put.assert_called_once_with({"id": "id", "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_service_no_retry(self, mock_sleep, mock_listener, tmp_path):
        from app.runner import run
        q = Mock()
        mock_listener.return_value = Mock()
        data_base = str(tmp_path / "data")
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("service fail")):
            run("id", "owner", "set", "name", "service", "", "raise Exception", [], q, data_base, "bind", "/venv", "/pkg")

        assert mock_sleep.call_count == 0  # No retries for service
        q.put.assert_called_once_with({"id": "id", "state": "failed", "detail": ANY})

    @patch('subprocess.run')
    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_debug_no_retry(self, mock_run, mock_sleep, mock_listener, tmp_path):
        mock_run.return_value = Mock(returncode=0, stdout="pip==24.2\n")
        from app.runner import run
        q = Mock()
        mock_listener.return_value = Mock()
        data_base = str(tmp_path / "data")
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("debug fail")):
            run("id", "owner", "set", "name", "debug", "", "raise Exception", [], q, data_base, "bind", "/venv", "/pkg")

        assert mock_sleep.call_count == 0  # No retries for debug
        q.put.assert_called_once_with({"id": "id", "state": "failed", "detail": ANY})

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_failed_exception(self, mock_sleep, mock_listener, tmp_path):
        from app.runner import run
        q = Mock()
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        mock_sleep.return_value = None

        run("id", "owner", "set", "name", "task", "", "raise Exception('exec error')", [], q, data_base, "bind", "/venv", "/pkg")

        assert mock_sleep.call_count == 3
        call_args = q.put.call_args[0][0]
        assert call_args["state"] == "failed"
        assert "Failed after 3 retries" in call_args["detail"]

    @patch('app.runner.loadLib')  # loadLib is in app.runner
    def test_process_imports_success(self, mock_loadLib, mock_conf):
        from app.runner import process_imports
        mock_loadLib.return_value = "loaded lib"
        libs = []
        result = process_imports("#!{::lib}\nrest", "current_set", False, libs)
        assert "# importing current_set::lib" in result
        assert len(libs) == 1
        assert libs[0]["code"] == "loaded lib"
        mock_loadLib.assert_called_once_with("current_set", "lib", False)

    @patch('app.runner.loadLib')
    def test_process_imports_failure(self, mock_loadLib, mock_conf):
        from app.runner import process_imports
        mock_loadLib.side_effect = Exception("load fail")
        libs = []
        result = process_imports("#!{::lib}\nrest", "current_set", False, libs)
        assert "#!{::lib}" in result  # Kept original
        assert len(libs) == 0
        mock_loadLib.assert_called_once_with("current_set", "lib", False)

    @patch('app.runner.loadLib')
    def test_process_imports_no_match(self, mock_loadLib, mock_conf):
        from app.runner import process_imports
        libs = []
        result = process_imports("no !\nrest", "current_set", False, libs)
        assert "no !" in result
        assert len(libs) == 0
        mock_loadLib.assert_not_called()

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_pip(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.runner import process_install_lines
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
    def test_process_install_lines_git_clone(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.runner import process_install_lines
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
    def test_process_install_lines_git_error(self, mock_run, mock_conf):
        from app.runner import process_install_lines
        mock_run.return_value = Mock(returncode=1, stderr="clone fail")
        lines = ["! https://fail.git"]
        with pytest.raises(Exception, match="git clone https://fail.git failed: clone fail"):
            process_install_lines(lines, "/venv", "/pkg", "id")

    @patch('subprocess.run')
    def test_process_install_lines_no_bang(self, mock_run, mock_conf):
        from app.runner import process_install_lines
        lines = ["normal line"]
        result = process_install_lines(lines, "/venv", "/pkg", "id")
        assert result[0] == "normal line"
        mock_run.assert_not_called()

    def test_shutdown_updates_running_jobs_to_cancelled(self, mock_conf, mock_db):
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from datetime import datetime
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        # Create running job
        engine = get_db_engine()
        with Session(engine) as session:
            job = Job(id="test_id", kind="service", state="running", pid=123, detail=None, end=None)
            job.set = "test_set"
            job.name = "test_name"
            job.owner = "test_owner"
            session.add(job)
            session.commit()

        # Mock now
        now = datetime.now()

        # Execute shutdown logic
        engine = get_db_engine()
        with Session(engine) as session:
            stmt = select(Job).where(Job.state == "running")
            running_jobs = session.scalars(stmt).all()
            for job in running_jobs:
                job.state = "cancelled"
                job.detail = "cancelled due to shutdown"
                job.end = now
                job.pid = None
            session.commit()

        # Verify
        with Session(engine) as session:
            result = session.get(Job, "test_id")
            assert result.state == "cancelled"
            assert result.detail == "cancelled due to shutdown"
            assert result.end == now
            assert result.pid is None

    @patch('app.lib.runner.enqueue')
    @patch('app.lib.runner.find')
    def test_schedule_reenqueues_service_excluding_cancelled(self, mock_find, mock_enqueue, mock_conf, mock_db, mock_croniter):
        from app.runner import schedule
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule

        # Setup mocks
        mock_find.side_effect = [[], []]

        # Create schedule
        engine = get_db_engine()
        with Session(engine) as session:
            sched = Schedule(
                id=1,
                set="set",
                name="name",
                owner="owner",
                active=True,
                drop_done=True,
                schedule="",
                param="param",
                last=None
            )
            session.add(sched)
            session.commit()

        # Call schedule
        schedule()

        # Verify find calls
        mock_find.assert_has_calls([
            call("owner", "set", "name", "service", ["todo", "dequeued", "running", "cancel", "done"]),
            call("owner", "set", "name", "service", ["failed"])
        ])
        # Verify enqueue
        mock_enqueue.assert_called_once_with("set", "name", "owner", "service", True, "param")
        # Check last updated
        with Session(engine) as session:
            result = session.get(Schedule, 1)
            assert result.last is not None

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
    def test_run_task_logging_success(self, mock_listener, tmp_path, sample_logging_code, mock_queue, mock_conf):
        from app.runner import run
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
    def test_run_service_logging(self, mock_listener, tmp_path, sample_logging_code, mock_queue, mock_conf):
        from app.runner import run
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

    @patch('builtins.compile')
    @patch('app.runner.pyops.ServiceListener')
    @patch('debugpy.listen')
    @patch('debugpy.wait_for_client')
    @patch('subprocess.run')
    def test_run_debug_inject(self, mock_run, mock_wait, mock_listen, mock_listener, mock_compile, tmp_path, mock_queue):
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        from app.runner import run
        mock_listener.return_value = Mock()  # For debug
        mock_listen.return_value = None
        mock_wait.return_value = None  # Don't block
        mock_compile.return_value = Mock()  # Mock code object
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        code = "print('test')"

        run(job_id, owner, set_name, "name", "debug", "", code, [], mock_queue, data_base, "bind", "/venv", "/pkg", port=5678)

        # Check compile called for debug bridge lib, debugpy_install lib, and code
        assert mock_compile.call_count >= 3
        # Find bridge lib compile
        bridge_calls = [call for call in mock_compile.call_args_list if "set::debug_bridge.py" in call[0][1]]
        assert len(bridge_calls) == 1
        bridge_code = bridge_calls[0][0][0]
        assert "import debugpy" in bridge_code
        assert "debugpy.listen(('127.0.0.1', 5678))" in bridge_code
        assert "debugpy.wait_for_client()" in bridge_code
        # Find code compile
        code_calls = [call for call in mock_compile.call_args_list if "set::name.py" in call[0][1]]
        assert len(code_calls) == 1
        code_compiled = code_calls[0][0][0]
        assert "print('test')" in code_compiled

    @patch.dict('sys.modules', {'debugpy': Mock()})
    @patch('app.runner.pyops.ServiceListener')
    @patch('debugpy.listen')
    @patch('debugpy.wait_for_client')
    @patch('subprocess.run')
    def test_run_debug_logging(self, mock_run, mock_wait, mock_listen, mock_listener, tmp_path, sample_logging_code, mock_queue, mock_conf):
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        from app.runner import run
        mock_listener.return_value = Mock()  # For debug
        mock_listen.return_value = None
        mock_wait.return_value = None  # Don't block
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        run(job_id, owner, set_name, "name", "debug", "", sample_logging_code, [], mock_queue, data_base, "bind", "/venv", "/pkg", port=5678)

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_fail_logging(self, mock_sleep, mock_listener, tmp_path, sample_fail_code, mock_queue, mock_conf):
        from app.runner import run
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        mock_sleep.return_value = None

        run(job_id, owner, set_name, "name", "task", "", sample_fail_code, [], mock_queue, data_base, "bind", "/venv", "/pkg")

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert stdout_content == ""
        assert "Failed after 3 retries" in stderr_content
        assert "ValueError: test error" in stderr_content
        detail = mock_queue.put.call_args[0][0]["detail"]
        assert "Failed after 3 retries" in detail
        assert "ValueError" in str(detail)
        assert mock_sleep.call_count == 3
        mock_queue.put.assert_called_once_with({"id": job_id, "state": "failed", "detail": ANY})

    @patch('app.runner.pyops.ServiceListener')
    def test_run_empty_output(self, mock_listener, tmp_path, sample_empty_code, mock_queue, mock_conf):
        from app.runner import run
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


    @patch('app.runner.os.chdir')
    @patch('app.runner.pyops.ServiceListener')
    def test_run_task_sets_cwd(self, mock_listener, mock_chdir, tmp_path, mock_conf):
        from app.runner import run
        mock_listener.return_value = None
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        workspace = os.path.join(data_base, owner, set_name, job_id)

        q = Mock()
        run(job_id, owner, set_name, "name", "task", "", "print('code')", [], q, data_base, "bind", "/venv", "/pkg")

        mock_chdir.assert_called_once_with(workspace)
        q.put.assert_called_once_with({"id": job_id, "state": "done", "detail": None})


    def test_run_task_glob_spaces_and_cwd(self, tmp_path, mock_conf):
        from app.runner import run
        from app.lib import files
        import os

        project_root = str(tmp_path)
        mock_conf_data = {"git": "data/git", "jobs": "data/jobs"}
        mock_conf_runner = {"pkg": "data/pkg"}

        with patch.object(files, 'project_root', project_root), \
             patch('app.lib.files.conf_data', return_value=mock_conf_data), \
             patch('app.lib.files.conf_runner', return_value=mock_conf_runner), \
             patch('app.runner.pyops.ServiceListener', return_value=None):

            data_base = str(tmp_path / "data")
            owner = "owner"
            set_name = "set"
            job_id = "id"
            workspace = os.path.join(data_base, owner, set_name, job_id)
            expected_gitspace = os.path.join(project_root, "data/git")
            expected_pkgspace = os.path.join(project_root, "data/pkg")
            expected_jobsspace = os.path.join(project_root, "data/jobs")

            code = """
import os
print("CWD: " + os.getcwd())
print("gitspace: " + globals()['gitspace'])
print("pkgspace: " + globals()['pkgspace'])
print("jobsspace: " + globals()['jobsspace'])
"""
            q = Queue()
            run(job_id, owner, set_name, "name", "task", "", code, [], q, data_base, "bind", "/venv", "/pkg")

            # Read stdout
            stdout_content, _ = self.get_log_contents(data_base, owner, set_name, job_id)
            assert workspace in stdout_content  # CWD is workspace
            assert expected_gitspace in stdout_content
            assert expected_pkgspace in stdout_content
            assert expected_jobsspace in stdout_content
            q.get()  # Consume the done message

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_param(self, mock_sleep, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        from multiprocessing import Queue
        mock_listener.return_value = None
        mock_sleep.return_value = None
        data_base = str(tmp_path / "data")
        set_ = "myset"
        name = "myname"
        param = "raise ValueError('param err')"
        q = Queue()
        run("id", "owner", set_, name, "task", param, "pass", [], q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "param err" in msg["detail"]
        assert f"{set_}::param.py" in msg["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_code(self, mock_sleep, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        from multiprocessing import Queue
        mock_listener.return_value = None
        mock_sleep.return_value = None
        data_base = str(tmp_path / "data")
        set_ = "myset"
        name = "myname"
        code = "raise ValueError('code err')"
        q = Queue()
        run("id", "owner", set_, name, "task", "", code, [], q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "code err" in msg["detail"]
        assert f"{set_}::{name}.py" in msg["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.runner.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_lib(self, mock_sleep, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        from multiprocessing import Queue
        mock_listener.return_value = None
        mock_sleep.return_value = None
        data_base = str(tmp_path / "data")
        set_ = "myset"
        name = "myname"
        lib_code = "raise ValueError('lib err')"
        lib_filename = f"{set_}::mylib.py"
        libs = [{"code": lib_code, "filename": lib_filename}]
        q = Queue()
        run("id", "owner", set_, name, "task", "", "pass", libs, q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "lib err" in msg["detail"]
        assert lib_filename in msg["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.runner.pyops.ServiceListener')
    def test_run_error_in_lib_service(self, mock_listener, tmp_path, mock_conf):
        from app.runner import run
        from multiprocessing import Queue
        mock_listener.return_value = Mock()
        data_base = str(tmp_path / "data")
        set_ = "myset"
        name = "myname"
        lib_code = "raise ValueError('lib err service')"
        lib_filename = f"{set_}::mylib.py"
        libs = [{"code": lib_code, "filename": lib_filename}]
        q = Queue()
        run("id", "owner", set_, name, "service", "", "pass", libs, q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "lib err service" in msg["detail"]
        assert lib_filename in msg["detail"]
