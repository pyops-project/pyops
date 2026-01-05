import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY, AsyncMock
import os
import sys
import datetime as real_datetime
from multiprocessing import Queue
import logging
import asyncio
import json


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
            run("id", "owner", "set", "name", "debug", "", "raise Exception", [], q, data_base, "bind", "/venv", "/pkg", port=5678)

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
        assert result == "#!{::lib}\nrest"
        assert len(libs) == 1
        assert libs[0]["code"] == "loaded lib"
        mock_loadLib.assert_called_once_with("current_set", "lib", False)

    @patch('app.runner.loadLib')
    def test_process_imports_failure(self, mock_loadLib, mock_conf):
        from app.runner import process_imports
        mock_loadLib.side_effect = Exception("load fail")
        libs = []
        result = process_imports("#!{::lib}\nrest", "current_set", False, libs)
        assert result == "#!{::lib}\nrest"  # Kept original
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
        lines = ["#!requests"]
        venv_path = "/venv"
        pkg_path = "/pkg"
        id = "id"
        process_install_lines(lines, venv_path, pkg_path, id)
        pip_path = os.path.join(venv_path, 'bin', 'pip')
        mock_run.assert_has_calls([
            call([pip_path, 'list', '--format=freeze'], capture_output=True, text=True, cwd=venv_path),
            call([pip_path, 'install', '-q', 'requests'], capture_output=True, text=True)
        ])

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_skip_lib_directive(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.runner import process_install_lines
        mock_exists.return_value = True  # venv exists
        lines = ["#!{test::lib}"]
        venv_path = "/venv"
        pkg_path = "/pkg"
        id = "id"
        process_install_lines(lines, venv_path, pkg_path, id)
        # Should not call pip commands for lib directives
        pip_calls = [call for call in mock_run.call_args_list if 'pip' in str(call)]
        assert len(pip_calls) == 0

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_clone(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.runner import process_install_lines
        mock_run.return_value = Mock(returncode=0)  # clone success
        mock_exists.return_value = False  # repo_dir not exist
        lines = ["#! https://repo.git"]
        venv_path = "/venv"
        pkg_path = "/pkg"
        id = "id"
        process_install_lines(lines, venv_path, pkg_path, id)
        mock_run.assert_called_once_with(['git', 'clone', 'https://repo.git', '/pkg/repo'], capture_output=True, text=True, cwd='/pkg')

    @patch('subprocess.run')
    def test_process_install_lines_git_error(self, mock_run, mock_conf):
        from app.runner import process_install_lines
        mock_run.return_value = Mock(returncode=1, stderr="clone fail")
        lines = ["#! https://fail.git"]
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

    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
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
    @patch('app.runner.terminate_debugpy_adapter')
    @patch('app.runner.pyops.ServiceListener')
    @patch('debugpy.listen')
    @patch('debugpy.wait_for_client')
    @patch('subprocess.run')
    def test_run_debug_inject(self, mock_run, mock_wait, mock_listen, mock_listener, mock_terminate, mock_compile, tmp_path, mock_queue):
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        from app.runner import run
        mock_listener.return_value = Mock()  # For debug
        mock_listen.return_value = None
        mock_wait.return_value = None  # Don't block
        mock_compile.return_value = compile("pass", "dummy", "exec")  # Mock code object
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        code = "print('test')"

        run(job_id, owner, set_name, "name", "debug", "#>AWAIT_DEBUG", code, [], mock_queue, data_base, "bind", "/venv", "/pkg", port=5678)

        # Check compile called for debug bridge lib, debugpy_install lib, and code
        assert mock_compile.call_count >= 3
        # Find debugpy lib compile (now in .debug/src/ subdirectories)
        bridge_calls = [call for call in mock_compile.call_args_list if ".debug/src/system/library/debugpy.py" in call[0][1]]
        assert len(bridge_calls) == 1
        bridge_code = bridge_calls[0][0][0]
        assert "import debugpy" in bridge_code
        assert "debugpy.listen(('0.0.0.0', 5678))" in bridge_code
        assert "debugpy.wait_for_client()" in bridge_code
        # Find code compile (now in .debug/src/ subdirectories)
        code_calls = [call for call in mock_compile.call_args_list if ".debug/src/set/task/name.py" in call[0][1]]
        assert len(code_calls) == 1
        code_compiled = code_calls[0][0][0]
        assert "print('test')" in code_compiled
        # Verify terminate_debugpy_adapter was called for debug job
        mock_terminate.assert_called_once_with("owner")

    @pytest.mark.parametrize("param, expect_wait", [
        ("#>AWAIT_DEBUG", True),
        ("", False),
    ])
    @patch('subprocess.run')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.runner.terminate_debugpy_adapter')
    @patch('builtins.compile')
    def test_run_debug_await_directive(self, mock_compile, mock_terminate, mock_listener, mock_run, param, expect_wait, tmp_path, mock_queue):
        mock_compile.return_value = compile("pass", "dummy", "exec")
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        mock_listener.return_value = Mock()
        from app.runner import run
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        code = "print('test')"
        run(job_id, owner, set_name, "name", "debug", param, code, [], mock_queue, data_base, "bind", "/venv", "/pkg", port=5678)
        # Find debugpy bridge compile call
        bridge_calls = [call for call in mock_compile.call_args_list if ".debug/src/system/library/debugpy.py" in call[0][1]]
        assert len(bridge_calls) == 1
        bridge_code = bridge_calls[0][0][0]
        assert "debugpy.listen((" in bridge_code
        if expect_wait:
            assert "debugpy.wait_for_client()" in bridge_code
        else:
            assert "debugpy.wait_for_client()" not in bridge_code
        mock_terminate.assert_called_once_with("owner")

    @patch.dict('sys.modules', {'debugpy': Mock()})
    @patch('app.runner.pyops.ServiceListener')
    @patch('debugpy.listen')
    @patch('debugpy.wait_for_client')
    @patch('subprocess.run')
    def test_run_debug_logging(self, mock_run, mock_wait, mock_listen, mock_listener, tmp_path, sample_logging_code, mock_queue, mock_conf):
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        from app.runner import run
        mock_listener.return_value = Mock()  # For debug
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


    @patch('os.chdir')
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
        assert f"{set_}/task/param.py" in msg["detail"]
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
        assert f"{set_}/task/{name}.py" in msg["detail"]
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
        lib_filename = f"{set_}::mylib"
        libs = [{"code": lib_code, "filename": lib_filename}]
        q = Queue()
        run("id", "owner", set_, name, "task", "", "pass", libs, q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "lib err" in msg["detail"]
        assert f"{set_}/library/mylib.py" in msg["detail"]
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
        lib_filename = f"{set_}::mylib"
        libs = [{"code": lib_code, "filename": lib_filename}]
        q = Queue()
        run("id", "owner", set_, name, "service", "", "pass", libs, q, data_base, "bind", "/venv", "/pkg")
        msg = q.get()
        assert msg["state"] == "failed"
        assert "lib err service" in msg["detail"]
        assert f"{set_}/library/mylib.py" in msg["detail"]

    @patch('subprocess.run')
    @patch('debugpy.wait_for_client')
    @patch('debugpy.listen')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.lib.files.conf_users')
    def test_run_debug_creates_files(self, mock_conf_users, mock_listener, mock_listen, mock_wait, mock_run, tmp_path, mock_conf):
        from app.runner import run
        from multiprocessing import Queue
        mock_listener.return_value = Mock()
        mock_conf_users.return_value = {"owner": {"debug_port": 5678}}
        mock_listen.return_value = None
        mock_wait.return_value = None
        mock_run.return_value = Mock(returncode=0, stdout="")
        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        param = "print('param code')"
        code = "print('main code')"
        libs = [{"code": "print('lib code')", "filename": "set::mylib"}]

        q = Queue()
        run(job_id, owner, set_name, "name", "debug", param, code, libs, q, data_base, "bind", "/venv", "/pkg", port=5678)

        msg = q.get()
        assert msg["state"] == "done"

        # Check that files were created in .debug/src/ subdirectories
        workspace = os.path.join(data_base, owner, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")

        # Check param file
        param_file = os.path.join(debug_src_dir, set_name, "task", "param.py")
        assert os.path.exists(param_file)
        with open(param_file, 'r') as f:
            assert "print('param code')" in f.read()

        # Check main code file
        code_file = os.path.join(debug_src_dir, set_name, "task", "name.py")
        assert os.path.exists(code_file)
        with open(code_file, 'r') as f:
            assert "print('main code')" in f.read()

        # Check lib file
        lib_file = os.path.join(debug_src_dir, set_name, "library", "mylib.py")
        assert os.path.exists(lib_file)
        with open(lib_file, 'r') as f:
            assert "print('lib code')" in f.read()

    @patch('subprocess.run')
    @patch('debugpy.wait_for_client')
    @patch('debugpy.listen')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.lib.files.conf_users')
    def test_run_debug_overrides_with_pip_install(self, mock_conf_users, mock_listener, mock_listen, mock_wait, mock_run, tmp_path, mock_conf):
        """Regression test: Ensure !pip directives in override code are processed."""
        from app.runner import run
        from multiprocessing import Queue

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_listener.return_value = Mock()
        mock_conf_users.return_value = {"owner": {"debug_port": 5678}}
        mock_listen.return_value = None
        mock_wait.return_value = None

        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        # Override code contains #!pip directive
        overrides = {"set::name": "#!pip install requests\nprint('overridden code')"}
        overrides_json = json.dumps(overrides)

        q = Queue()
        run(job_id, owner, set_name, "name", "debug", "", "print('original code')", [], q, data_base, "bind", "/venv", "/pkg", overrides=overrides_json, port=5678)

        msg = q.get()
        assert msg["state"] == "done"

        # Verify pip install was called (mock_run was called at least once)
        assert mock_run.call_count > 0

        # Check that the overridden code was written
        workspace = os.path.join(data_base, owner, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        code_file = os.path.join(debug_src_dir, set_name, "task", "name.py")
        assert os.path.exists(code_file)
        with open(code_file, 'r') as f:
            content = f.read()
            assert "#!pip install requests" in content
            assert "print('overridden code')" in content

    @patch('subprocess.run')
    @patch('debugpy.wait_for_client')
    @patch('debugpy.listen')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.lib.files.conf_users')
    def test_debug_overrides_ignore_unreferenced(self, mock_conf_users, mock_listener, mock_listen, mock_wait, mock_run, tmp_path, mock_conf):
        """Post-fix regression: Unreferenced overrides ignored (no exec/file)."""
        from app.runner import run
        from multiprocessing import Queue

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_listener.return_value = Mock()
        mock_conf_users.return_value = {"owner": {"debug_port": 5678}}
        mock_listen.return_value = None
        mock_wait.return_value = None

        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        overrides = {
            "set::name": "print('overridden code')",
            "set::newlib": "print('unused new lib')"  # Unreferenced
        }
        overrides_json = json.dumps(overrides)

        q = Queue()
        run(job_id, owner, set_name, "name", "debug", "", "print('task')", [], q, data_base, "bind", "/venv", "/pkg", overrides=overrides_json, port=5678)

        msg = q.get()
        assert msg["state"] == "done"

        workspace = os.path.join(data_base, owner, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        new_lib_file = os.path.join(debug_src_dir, set_name, "library", "newlib.py")
        assert not os.path.exists(new_lib_file)  # Ignored!

        code_file = os.path.join(debug_src_dir, set_name, "task", "name.py")
        with open(code_file, 'r') as f:
            assert "print('overridden code')" in f.read()


class TestMainLoopDebugIntegration:
    """Integration tests for main loop debug job adapter waiting functionality."""

    @patch('app.runner.main')  # Patch the main function itself
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.run')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('app.runner.has_debugpy_adapter')
    @patch('app.runner.terminate_job_processes', return_value=None)
    @patch('app.runner.time.sleep')
    @patch('multiprocessing.Process')
    def test_main_loop_debug_adapter_success(self, mock_process_class, mock_sleep, mock_terminate, mock_has_adapter,
                                           mock_finish_job, mock_assign_job, mock_run, mock_load_job, mock_get_next,
                                           mock_main, mock_conf, mock_db):
        """Test debug job assignment after adapter appears within timeout."""
        from app.runner import main

        # Setup mocks
        mock_get_next.return_value = "debug_job_id"
        mock_load_job.return_value = ("owner", "set", "name", "debug", "param", "code", "overrides")
        mock_process = Mock()
        mock_process.is_alive.return_value = True
        mock_process.pid = 12345
        mock_process_class.return_value = mock_process

        # Adapter appears on 3rd check (after 0.2s)
        mock_has_adapter.side_effect = [False, False, True]
        mock_sleep.return_value = None

        # Mock the main function to simulate one iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            # Simulate one iteration of the main loop
            job_id = mock_get_next()
            if job_id is not None:
                owner, set, name, kind, param, code, overrides = mock_load_job()
                port = 5678  # Mock port
                libs = []
                p = mock_process_class()
                p.start()

                if kind == "debug":
                    adapter_up = False
                    for attempt in range(50):  # 5 seconds max
                        if not p.is_alive():
                            break
                        if mock_has_adapter(port):
                            adapter_up = True
                            break
                        mock_sleep(0.1)

                    if not adapter_up:
                        if p.is_alive():
                            mock_terminate(p.pid, True, owner)
                            mock_finish_job(job_id, "failed", "Debug adapter failed to start within timeout")
                            return
                        else:
                            return

                # Adapter is up or not a debug job - proceed normally
                mock_assign_job(job_id, p.pid)

        mock_main.side_effect = mock_main_impl

        # Run main - it should process one job and exit
        main("data", "bind", "/venv", "/pkg", {})

        # Verify adapter was checked
        assert mock_has_adapter.call_count == 3
        # Verify assignJob was called after adapter appeared
        mock_assign_job.assert_called_once_with("debug_job_id", 12345)
        # Verify no termination for timeout
        mock_terminate.assert_not_called()
        mock_finish_job.assert_not_called()

    @patch('app.runner.main')  # Patch the main function itself
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.run')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('app.runner.has_debugpy_adapter')
    @patch('app.runner.terminate_job_processes')
    @patch('app.runner.time.sleep')
    @patch('multiprocessing.Process')
    def test_main_loop_debug_adapter_timeout(self, mock_process_class, mock_sleep, mock_terminate, mock_has_adapter,
                                           mock_finish_job, mock_assign_job, mock_run, mock_load_job, mock_get_next,
                                           mock_main, mock_conf, mock_db):
        """Test debug job termination when adapter doesn't appear within timeout."""
        from app.runner import main

        # Setup mocks
        mock_get_next.return_value = "debug_job_id"
        mock_load_job.return_value = ("owner", "set", "name", "debug", "param", "code", "overrides")
        mock_process = Mock()
        mock_process.is_alive.side_effect = [True] * 51  # Alive during checks and after
        mock_process.pid = 12345
        mock_process_class.return_value = mock_process

        # Adapter never appears
        mock_has_adapter.return_value = False
        mock_sleep.return_value = None
        mock_terminate.return_value = None

        # Mock the main function to simulate one iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            # Simulate one iteration of the main loop
            job_id = mock_get_next()
            if job_id is not None:
                owner, set, name, kind, param, code, overrides = mock_load_job()
                port = 5678  # Mock port
                libs = []
                p = mock_process_class()
                p.start()

                if kind == "debug":
                    adapter_up = False
                    for attempt in range(50):  # 5 seconds max
                        if not p.is_alive():
                            break
                        if mock_has_adapter(port):
                            adapter_up = True
                            break
                        mock_sleep(0.1)

                    if not adapter_up:
                        if p.is_alive():
                            mock_terminate(p.pid, True, owner)
                            mock_finish_job(job_id, "failed", "Debug adapter failed to start within timeout")
                            return
                        else:
                            return

                # Adapter is up or not a debug job - proceed normally
                mock_assign_job(job_id, p.pid)

        mock_main.side_effect = mock_main_impl

        # Run main - it should process one job and exit
        main("data", "bind", "/venv", "/pkg", {})

        # Verify adapter was checked 50 times (5 seconds)
        assert mock_has_adapter.call_count == 50
        # Verify process was terminated
        mock_terminate.assert_called_once_with(12345, True, "owner")
        # Verify job marked as failed
        mock_finish_job.assert_called_once_with("debug_job_id", "failed", "Debug adapter failed to start within timeout")
        # Verify assignJob was NOT called
        mock_assign_job.assert_not_called()

    @patch('app.runner.main')  # Patch the main function itself
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.run')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('app.runner.has_debugpy_adapter')
    @patch('app.runner.terminate_job_processes')
    @patch('app.runner.time.sleep')
    @patch('multiprocessing.Process')
    def test_main_loop_debug_process_dies_before_adapter(self, mock_process_class, mock_sleep, mock_terminate, mock_has_adapter,
                                                       mock_finish_job, mock_assign_job, mock_run, mock_load_job, mock_get_next,
                                                       mock_main, mock_conf, mock_db):
        """Test debug job handling when process dies before adapter appears."""
        from app.runner import main

        # Setup mocks
        def get_next_side_effect():
            if not hasattr(get_next_side_effect, 'called'):
                get_next_side_effect.called = True
                return "debug_job_id"
            return None
        mock_get_next.side_effect = get_next_side_effect
        mock_load_job.return_value = ("owner", "set", "name", "debug", "param", "code", "overrides")
        mock_process = Mock()
        def is_alive_side_effect():
            if not hasattr(is_alive_side_effect, 'calls'):
                is_alive_side_effect.calls = 0
            is_alive_side_effect.calls += 1
            return is_alive_side_effect.calls <= 2  # Alive for first 2 calls, dead after
        mock_process.is_alive.side_effect = is_alive_side_effect
        mock_process.pid = 12345
        mock_process_class.return_value = mock_process

        # Adapter never appears
        mock_has_adapter.return_value = False
        mock_sleep.return_value = None

        # Mock the main function to simulate one iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            # Simulate one iteration of the main loop
            job_id = mock_get_next()
            if job_id is not None:
                owner, set, name, kind, param, code, overrides = mock_load_job()
                port = 5678  # Mock port
                libs = []
                p = mock_process_class()
                p.start()

                if kind == "debug":
                    adapter_up = False
                    for attempt in range(50):  # 5 seconds max
                        if not p.is_alive():
                            break
                        if mock_has_adapter(port):
                            adapter_up = True
                            break
                        mock_sleep(0.1)

                    if not adapter_up:
                        if p.is_alive():
                            mock_terminate(p.pid, True, owner)
                            mock_finish_job(job_id, "failed", "Debug adapter failed to start within timeout")
                            return
                        else:
                            return

                # Adapter is up or not a debug job - proceed normally
                mock_assign_job(job_id, p.pid)

        mock_main.side_effect = mock_main_impl

        # Run main - it should process one job and exit
        main("data", "bind", "/venv", "/pkg", {})

        # Verify adapter was checked at least once
        assert mock_has_adapter.call_count >= 1
        # Verify no termination (process already dead)
        mock_terminate.assert_not_called()
        # Verify no job finish (let normal cleanup handle)
        mock_finish_job.assert_not_called()
        # Verify assignJob was NOT called
        mock_assign_job.assert_not_called()

    @patch('app.runner.main')  # Patch the main function itself
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.run')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('multiprocessing.Process')
    def test_main_loop_non_debug_job_no_waiting(self, mock_process_class, mock_finish_job, mock_assign_job, mock_run,
                                               mock_load_job, mock_get_next, mock_main, mock_conf, mock_db):
        """Test that non-debug jobs don't wait for adapter."""
        from app.runner import main

        # Setup mocks for non-debug job
        mock_get_next.return_value = "task_job_id"
        mock_load_job.return_value = ("owner", "set", "name", "task", "param", "code", "overrides")
        mock_process = Mock()
        mock_process.pid = 12345
        mock_process_class.return_value = mock_process

        # Mock the main function to simulate one iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            # Simulate one iteration of the main loop
            job_id = mock_get_next()
            if job_id is not None:
                owner, set, name, kind, param, code, overrides = mock_load_job()
                port = 5678  # Mock port
                libs = []
                p = mock_process_class()
                p.start()

                if kind == "debug":
                    adapter_up = False
                    for attempt in range(50):  # 5 seconds max
                        if not p.is_alive():
                            break
                        if mock_has_adapter(port):
                            adapter_up = True
                            break
                        mock_sleep(0.1)

                    if not adapter_up:
                        if p.is_alive():
                            mock_terminate(p.pid, True, owner)
                            mock_finish_job(job_id, "failed", "Debug adapter failed to start within timeout")
                            return
                        else:
                            return

                # Adapter is up or not a debug job - proceed normally
                mock_assign_job(job_id, p.pid)

        mock_main.side_effect = mock_main_impl

        # Run main - it should process one job and exit
        main("data", "bind", "/venv", "/pkg", {})

        # Verify assignJob was called immediately (no adapter waiting)
        mock_assign_job.assert_called_once_with("task_job_id", 12345)
        # Verify no finishJob calls
        mock_finish_job.assert_not_called()


class TestDebugOverridesResolution(TestLoggingCapture):
    """New regression tests for override resolution (post-fix)."""

    @patch('subprocess.run')
    @patch('debugpy.wait_for_client')
    @patch('debugpy.listen')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.lib.files.conf_users')
    @patch('app.runner.loadLib', side_effect=Exception("git not found"))
    def test_debug_directive_new_override(self, mock_loadLib, mock_conf_users, mock_listener, mock_listen, mock_wait, mock_run, tmp_path, mock_conf):
        """Directive references new override lib (no git)."""
        from app.runner import run, process_imports
        from multiprocessing import Queue

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_conf_users.return_value = {"owner": {"debug_port": 5678}}

        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "test"
        job_id = "id"
        code = "#!{test::newlib}\nprint('task')"

        overrides = {"test::newlib": "print('overridden newlib')"}
        overrides_json = json.dumps(overrides)
        override_dict = json.loads(overrides_json)

        libs = []
        code = process_imports(code, set_name, "debug", libs, override_dict)

        q = Queue()
        run(job_id, owner, set_name, "task", "debug", "", code, libs, q, data_base, "bind", "/venv", "/pkg", overrides=overrides_json, port=5678)

        msg = q.get()
        assert msg["state"] == "done"

        # Check stdout has newlib
        stdout_path = os.path.join(data_base, owner, set_name, job_id, "stdout.log")
        with open(stdout_path, 'r') as f:
            stdout = f.read()
            assert "overridden newlib" in stdout
            assert "task" in stdout

        # Check file created
        workspace = os.path.join(data_base, owner, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        new_lib_file = os.path.join(debug_src_dir, set_name, "library", "newlib.py")
        assert os.path.exists(new_lib_file)
        with open(new_lib_file, 'r') as f:
            assert "print('overridden newlib')" in f.read()

    @patch('subprocess.run')
    @patch('debugpy.wait_for_client')
    @patch('debugpy.listen')
    @patch('app.runner.pyops.ServiceListener')
    @patch('app.lib.files.conf_users')
    def test_debug_recursive_directive(self, mock_conf_users, mock_listener, mock_listen, mock_wait, mock_run, tmp_path, mock_conf):
        """Lib directive resolves sub-lib."""
        from app.runner import run, process_imports
        from multiprocessing import Queue

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_conf_users.return_value = {"owner": {"debug_port": 5678}}

        data_base = str(tmp_path / "data")
        owner = "owner"
        set_name = "test"
        job_id = "id"
        code = "#!{test::libA}\nprint('task')"

        overrides = {
            "test::libA": "#!{test::libB}\nprint('libA')",
            "test::libB": "print('libB')"
        }
        overrides_json = json.dumps(overrides)
        override_dict = json.loads(overrides_json)

        libs = []
        code = process_imports(code, set_name, "debug", libs, override_dict)

        q = Queue()
        run(job_id, owner, set_name, "task", "debug", "", code, libs, q, data_base, "bind", "/venv", "/pkg", overrides=overrides_json, port=5678)

        msg = q.get()
        assert msg["state"] == "done"

        stdout_path = os.path.join(data_base, owner, set_name, job_id, "stdout.log")
        with open(stdout_path, 'r') as f:
            stdout = f.read()
            assert "libB" in stdout
            assert "libA" in stdout
            assert "task" in stdout

        # Check files
        workspace = os.path.join(data_base, owner, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        libA_file = os.path.join(debug_src_dir, set_name, "library", "libA.py")
        libB_file = os.path.join(debug_src_dir, set_name, "library", "libB.py")
        assert os.path.exists(libA_file)
        assert os.path.exists(libB_file)
