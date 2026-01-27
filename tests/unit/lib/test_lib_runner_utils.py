import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY
from sqlalchemy.orm import Session
from datetime import datetime
import os
import shutil
import signal
import psutil

class TestDBUtils:
    def test_enqueue(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        test_set = "test_set"
        test_name = "test_name"
        test_param = "test param"
        test_type = "task"
        result_id = runner.enqueue(test_set, test_name, "owner", test_type, True, test_param)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.query(Job).filter_by(id=result_id).first()
            assert fetched is not None
            assert fetched.set == test_set
            assert fetched.state == "todo"
            assert fetched.drop_done is True
            assert fetched.param == test_param

    def test_query(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", state="todo", owner="test", set="test", name="test", kind="task", param="{}")
            test_session.add(job)
            test_session.commit()
        result = runner.query("123")
        assert result.id == "123"
        assert result.state == "todo"

    def test_query_not_found(self, mock_db):
        from app.lib import runner
        result = runner.query("999")
        assert result is None

    def test_find(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create jobs
        with Session(get_db_engine()) as test_session:
            job1 = Job(id="123", owner="owner", set="set", name="name", kind="kind", state="todo", param="{}")
            job2 = Job(id="456", owner="owner", set="set", name="name", kind="kind", state="todo", param="{}")
            test_session.add_all([job1, job2])
            test_session.commit()
        result = runner.find("owner", "set", "name", "kind", ["todo"])
        assert sorted(result) == ["123", "456"]

    def test_find_empty_states(self, mock_db):
        from app.lib import runner
        # Edge: empty states returns no jobs (SQLAlchemy .in_([]) matches nothing)
        result = runner.find("owner", "set", "name", "kind", [])
        assert result == []

    def test_cancel(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", owner="test", set="test", name="test", kind="task", state="running", param="{}")
            test_session.add(job)
            test_session.commit()
        runner.cancel("123")
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "cancel"

    def test_remove(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", owner="test", set="test", name="test", kind="task", state="done", param="{}")
            test_session.add(job)
            test_session.commit()
        runner.remove("123")
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "remove"

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    @patch('os.remove')
    @patch('shutil.rmtree')
    @patch('os.listdir')
    def test_drop_success(self, mock_listdir, mock_rmtree, mock_remove, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.side_effect = [True, True, lambda p: p == '/tmp/userspace' or p == '/tmp/setspace']
        mock_listdir.side_effect = lambda p: [] if p.endswith('setspace') or p.endswith('userspace') else []
        mock_rmtree.return_value = None
        mock_remove.return_value = None
        result = runner.drop("123", "owner", "set")
        assert result is True
        mock_remove.assert_called_once_with('/tmp/pyops/123.sock')
        mock_rmtree.assert_has_calls([
            call('/tmp/mock/data/jobs/owner/set/123'),
            call('/tmp/mock/data/jobs/owner/set'),
            call('/tmp/mock/data/jobs/owner')
        ], any_order=False)

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    def test_drop_no_workspace(self, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = False  # No workspace exists
        result = runner.drop("123", "owner", "set")
        assert result is False

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    @patch('shutil.rmtree', side_effect=OSError("rm error"))
    def test_drop_error(self, mock_rmtree, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = True
        result = runner.drop("123", "owner", "set")
        assert result is False

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    def test_drop_no_exist(self, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        # Edge: no exist returns False
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = False
        result = runner.drop("123", "owner", "set")
        assert result is False

class TestLoadUtils:
    def test_load_job(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.task.py']):
            result = runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "loading"  # State set to loading by loadJob
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=True)
            assert result == ("owner", "set", "name", "task", "param", 'code', None, 0)

    def test_load_job_debug_unstaged(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Debug mode without override uses committed=False (unstaged)
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="debug", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'debug unstaged code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.task.py']):
            result = runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "loading"  # State set to loading by loadJob
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=False)
            assert result == ("owner", "set", "name", "debug", "param", 'debug unstaged code', None, 0)

    def test_load_job_debug(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        import json
        # Debug mode: unstaged git like draft, returns overrides, uses override for main if present
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="debug", param="param", state="dequeued", owner="owner")
            job.overrides = json.dumps({"set::name": "override"})
            test_session.add(job)
            test_session.commit()
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'debug_code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.task.py']):
            result = runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "loading"  # State set to loading by loadJob
            # Since overrides has set::name, read_content should not be called
            mock_read_content.assert_not_called()
            assert result == ("owner", "set", "name", "debug", "param", "override", '{"set::name": "override"}', 0)

    def test_load_lib(self, mock_git_ops):
        from app import runner
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'lib code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.py']):
            result = runner.loadLib("set", "name", "task")
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=True)
            assert result == 'lib code'

    def test_load_lib_no_file(self, mock_git_ops):
        from app import runner
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match=r"Scriptlet set::name not existing"):
                runner.loadLib("set", "name", "task")

    def test_load_job_no_file(self, mock_db, mock_git_ops):
        from app import runner
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match=r"Job 123 not existing"):
                runner.loadJob("123")
            # No job created, so query returns None implicitly

    def test_load_job_scriptlet_not_found(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # From runner/: Scriptlet file missing
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="test_set", name="test_name", kind="task", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match=r"Scriptlet test_set::test_name not existing"):
                runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "dequeued"  # State not updated on missing scriptlet

class TestProcessImports:
    @patch('app.runner.loadLib')
    def test_process_imports_valid_lib(self, mock_load_lib):
        from app.runner import process_imports
        mock_load_lib.return_value = 'lib_code'
        text = "#!{set::lib}\npass"
        libs = []
        process_imports(text, 'set', 'task', libs)
        assert libs == [{"code": "lib_code", "filename": "set::lib"}]
        mock_load_lib.assert_called_once_with('set', 'lib', 'task')

    @patch('app.runner.loadLib')
    def test_process_imports_empty_set(self, mock_load_lib):
        from app.runner import process_imports
        mock_load_lib.return_value = 'lib_code'
        text = "#!{::lib}\npass"
        libs = []
        process_imports(text, 'current_set', 'task', libs)
        assert libs == [{"code": "lib_code", "filename": "current_set::lib"}]
        mock_load_lib.assert_called_once_with('current_set', 'lib', 'task')

    def test_process_imports_invalid_format(self):
        from app.runner import process_imports
        text = "#!{invalid}\npass"
        libs = []
        result = process_imports(text, 'set', 'task', libs)
        assert result == "#!{invalid}\npass"
        assert libs == []

    def test_process_imports_no_match(self):
        from app.runner import process_imports
        text = "pass\n"
        libs = []
        result = process_imports(text, 'set', 'task', libs)
        assert result == "pass\n"
        assert libs == []

    @patch('app.runner.loadLib', side_effect=Exception('load error'))
    def test_process_imports_load_error(self, mock_load_lib):
        from app.runner import process_imports
        text = "#!{set::lib}\npass"
        libs = []
        with pytest.raises(Exception, match='load error'):
            process_imports(text, 'set', 'task', libs)

class TestProcessInstallLines:
    @patch('app.runner.ensure_set_environment')
    @patch('os.makedirs')
    @patch('app.runner.subprocess.run')
    @patch('app.runner.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_pip_install(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.runner import process_install_lines
        # Mock pip install succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!pip_module']
        process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert mock_run.call_count == 1
        mock_run.assert_called_once_with(['/venv/testset/bin/pip', 'install', '-q', 'pip_module'], capture_output=True, text=True)

    @patch('app.runner.ensure_set_environment')
    @patch('os.makedirs')
    @patch('app.runner.subprocess.run')
    @patch('app.runner.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_pip_install_with_args(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.runner import process_install_lines
        # Mock pip install with additional arguments succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!torch --index-url https://download.pytorch.org/whl/cu130']
        process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert mock_run.call_count == 1
        mock_run.assert_called_once_with(['/venv/testset/bin/pip', 'install', '-q', 'torch --index-url https://download.pytorch.org/whl/cu130'], capture_output=True, text=True)

    @patch('app.runner.ensure_set_environment')
    @patch('os.makedirs')
    @patch('app.runner.subprocess.run')
    @patch('app.runner.os.path.join', side_effect=lambda *args: '/'.join(args))
    @patch('app.runner.os.path.exists', return_value=False)
    def test_process_install_lines_git_clone(self, mock_exists, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.runner import process_install_lines
        # Mock git clone succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!https://github.com/user/repo.git']
        process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        # Check that git clone was called with correct args
        mock_run.assert_any_call(['git', 'clone', 'https://github.com/user/repo.git', '/pkg/testset/repo'], capture_output=True, text=True, cwd='/pkg/testset')

    @patch('app.runner.ensure_set_environment')
    @patch('os.makedirs')
    @patch('app.runner.subprocess.run')
    @patch('app.runner.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_no_bang(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.runner import process_install_lines
        lines = ['pass']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == ['pass']
        mock_run.assert_not_called()

    @patch('app.runner.ensure_set_environment')
    @patch('os.makedirs')
    @patch('app.runner.subprocess.run')
    @patch('app.runner.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_empty(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.runner import process_install_lines
        lines = ['!']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == ['!']
        mock_run.assert_not_called()

class TestKillProcTree:
    @patch('app.runner.os.kill')
    def test_kill_proc_tree_single_process(self, mock_os_kill):
        from app.runner import kill_proc_tree
        mock_proc = Mock()
        mock_proc.pid = 123
        processes = [mock_proc]
        kill_proc_tree(processes, signal.SIGTERM)
        mock_os_kill.assert_called_once_with(123, signal.SIGTERM)

    @patch('app.runner.os.kill')
    def test_kill_proc_tree_multiple_processes(self, mock_os_kill):
        from app.runner import kill_proc_tree
        mock_proc1 = Mock()
        mock_proc1.pid = 123
        mock_proc2 = Mock()
        mock_proc2.pid = 456
        processes = [mock_proc1, mock_proc2]
        kill_proc_tree(processes, signal.SIGKILL)
        assert mock_os_kill.call_count == 2
        mock_os_kill.assert_any_call(123, signal.SIGKILL)
        mock_os_kill.assert_any_call(456, signal.SIGKILL)

    @patch('app.runner.os.kill', side_effect=OSError("Process already dead"))
    def test_kill_proc_tree_oserror_ignored(self, mock_os_kill):
        from app.runner import kill_proc_tree
        mock_proc = Mock()
        mock_proc.pid = 123
        processes = [mock_proc]
        # Should not raise
        kill_proc_tree(processes, signal.SIGTERM)
        mock_os_kill.assert_called_once_with(123, signal.SIGTERM)

    def test_enqueue_debug_with_existing_job_kill(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Test debug enqueue marks existing debug job for cancellation
        with Session(get_db_engine()) as test_session:
            existing_job = Job(id="existing_debug", owner="owner", set="set", name="name", kind="debug", state="running", pid=12345, param="{}")
            test_session.add(existing_job)
            test_session.commit()
        result_id = runner.enqueue("set", "name", "owner", "debug", False, "{}")
        # Check existing job was marked for cancellation
        with Session(get_db_engine()) as test_session:
            updated = test_session.get(Job, "existing_debug")
            assert updated.state == "cancel"
            assert updated.detail == "Cancelled by new debug job start for owner"
            assert updated.end is not None

    def test_enqueue_debug_existing_job_no_pid(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Test debug enqueue with existing job that has no pid
        with Session(get_db_engine()) as test_session:
            existing_job = Job(id="existing_debug", owner="owner", set="set", name="name", kind="debug", state="running", pid=None, param="{}")
            test_session.add(existing_job)
            test_session.commit()
        result_id = runner.enqueue("set", "name", "owner", "debug", False, "{}")
        # Job marked for cancellation
        with Session(get_db_engine()) as test_session:
            updated = test_session.get(Job, "existing_debug")
            assert updated.state == "cancel"

    def test_enqueue_debug_psutil_no_such_process(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Test debug enqueue marks job for cancellation even if pid exists
        with Session(get_db_engine()) as test_session:
            existing_job = Job(id="existing_debug", owner="owner", set="set", name="name", kind="debug", state="running", pid=12345, param="{}")
            test_session.add(existing_job)
            test_session.commit()
        result_id = runner.enqueue("set", "name", "owner", "debug", False, "{}")
        # Job marked for cancellation
        with Session(get_db_engine()) as test_session:
            updated = test_session.get(Job, "existing_debug")
            assert updated.state == "cancel"

    def test_enqueue_with_overrides(self, mock_db):
        from app.lib import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        overrides = '{"key": "value"}'
        result_id = runner.enqueue("set", "name", "owner", "debug", True, "param", 0, overrides)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.query(Job).filter_by(id=result_id).first()
            assert fetched.overrides == overrides

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    @patch('os.remove')
    @patch('shutil.rmtree', side_effect=Exception("rmtree error"))
    @patch('os.listdir')
    def test_drop_rmtree_exception(self, mock_listdir, mock_rmtree, mock_remove, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = True
        result = runner.drop("123", "owner", "set")
        assert result is False

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    @patch('os.remove', side_effect=Exception("remove error"))
    @patch('shutil.rmtree')
    @patch('os.listdir')
    def test_drop_remove_exception(self, mock_listdir, mock_rmtree, mock_remove, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.side_effect = [True, True]  # sock exists, workspace exists
        result = runner.drop("123", "owner", "set")
        assert result is False

    def test_load_lib_debug(self, mock_git_ops):
        from app import runner
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'lib code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.py']):
            result = runner.loadLib("set", "name", "debug")
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=False)
            assert result == 'lib code'


class TestAssignJob:
    def test_assign_job_regular(self, mock_db):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        # Mock get_debug_port and get_debugpy_adapter_pid for non-debug job
        with patch('app.runner.get_debug_port', return_value=5678), \
             patch('app.runner.get_debugpy_adapter_pid', return_value=None):
            runner.assignJob("123", 456)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "running"
            assert fetched.start is not None
            assert fetched.pid == 456
            assert fetched.debugpy_pid is None

    def test_assign_job_debug(self, mock_db):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Create debug job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="debug", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        # Mock get_debug_port and get_debugpy_adapter_pid for debug job
        with patch('app.runner.get_debug_port', return_value=5678), \
             patch('app.runner.get_debugpy_adapter_pid', return_value=789):
            runner.assignJob("123", 456)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "running"
            assert fetched.start is not None
            assert fetched.pid == 456
            assert fetched.debugpy_pid == 789

class TestTerminateJobProcesses:
    @patch('app.runner.postTerm')
    @patch('app.runner.os.kill')
    @patch('app.runner.psutil.Process')
    def test_terminate_graceful_quick_debug(self, mock_Process, mock_os_kill, mock_postTerm):
        from app.runner import terminate_job_processes
        mock_parent = Mock()
        mock_parent.pid = 123
        mock_parent.children.return_value = []
        mock_parent.is_running.return_value = False
        mock_Process.return_value = mock_parent
        terminate_job_processes("job_id", 123, True, 'owner')
        mock_os_kill.assert_called_once_with(123, signal.SIGTERM)
        mock_postTerm.assert_called_once_with("job_id")

    @patch('app.runner.postTerm')
    @patch('app.runner.os.kill')
    @patch('app.runner.psutil.Process')
    def test_terminate_graceful_quick_non_debug(self, mock_Process, mock_os_kill, mock_postTerm):
        from app.runner import terminate_job_processes
        mock_parent = Mock()
        mock_parent.pid = 123
        mock_parent.children.return_value = []
        mock_parent.is_running.return_value = False
        mock_Process.return_value = mock_parent
        terminate_job_processes("job_id", 123, False, 'owner')
        mock_os_kill.assert_called_once_with(123, signal.SIGTERM)
        mock_postTerm.assert_called_once_with("job_id")

    @patch('app.runner.postTerm')
    @patch('app.runner.kill_proc_tree')
    @patch('time.sleep')
    @patch('time.time')
    @patch('app.runner.get_process_tree')
    def test_terminate_timeout_force_kill(self, mock_tree, mock_time, mock_sleep, mock_kill_tree, mock_post):
        from app.runner import terminate_job_processes
        mock_time.side_effect = iter(range(32))
        mock_sleep.return_value = None  # No-op sleep
        mock_alive = Mock()
        mock_alive.is_running.return_value = True
        mock_tree.return_value = [mock_alive]
        terminate_job_processes("job_id", 123, True, 'owner', graceful_timeout=15.0)
        mock_kill_tree.assert_has_calls([
            call(ANY, signal.SIGTERM),
            call(ANY, signal.SIGKILL)
        ], any_order=False)
        mock_post.assert_called_once_with("job_id", killed=True)

    @patch('app.runner.psutil.Process')
    def test_get_process_tree_exists_children(self, mock_Process):
        from app.runner import get_process_tree
        mock_parent = Mock(pid=123)
        mock_child = Mock(pid=456)
        mock_parent.children.return_value = [mock_child]
        mock_Process.return_value = mock_parent
        tree = get_process_tree(123)
        assert len(tree) == 2
        assert tree[0].pid == 123
        assert tree[1].pid == 456

    def test_get_process_tree_pid_gone(self):
        from app.runner import get_process_tree
        with patch('app.runner.psutil.Process', side_effect=psutil.NoSuchProcess(123)):
            tree = get_process_tree(123)
        assert tree == []


class TestPostTerm:
    def test_post_term_killed(self, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", name="testname", state="running", owner="owner", set="set", kind="task")
            session.add(job)
            session.commit()
        postTerm("testid", killed=True)
        with Session(get_db_engine()) as session:
            job = session.get(Job, "testid")
            assert job.state == "cancelled"
            assert job.detail is None
            assert job.pid is None
            assert job.end is not None

    def test_post_term_graceful_cancel(self, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", name="testname", state="cancel", owner="owner", set="set", kind="task")
            session.add(job)
            session.commit()
        postTerm("testid")
        with Session(get_db_engine()) as session:
            job = session.get(Job, "testid")
            assert job.state == "cancelled"
            assert job.detail is None
            assert job.pid is None
            assert job.end is not None

    def test_post_term_graceful_done_nochange(self, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", name="testname", state="done", owner="owner", set="set", kind="task")
            session.add(job)
            session.commit()
        postTerm("testid")
        with Session(get_db_engine()) as session:
            job = session.get(Job, "testid")
            assert job.state == "done"
            assert job.pid is None
            assert job.end is not None

    @patch('app.lib.runner.drop')
    def test_post_term_remove_delete_drop(self, mock_drop, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", name="testname", state="remove", owner="owner", set="set", kind="task")
            session.add(job)
            session.commit()
        postTerm("testid")
        mock_drop.assert_called_once_with("testid", "owner", "set")
        with Session(get_db_engine()) as session:
            assert session.get(Job, "testid") is None

class TestDebugAdapterUtils:
    @patch('app.runner.psutil.process_iter')
    @patch('app.runner.files.conf_users', return_value={"owner": {"debug_port": 5678}})
    @patch('app.runner.os.kill')
    def test_terminate_debugpy_adapter_match_kill(self, mock_kill, mock_conf, mock_iter):
        from app.runner import terminate_debugpy_adapter
        mock_pinfo = Mock()
        mock_pinfo.info = {'pid': 999, 'cmdline': ['python', '-m', 'debugpy.adapter', '--port', '5678']}
        mock_iter.return_value = iter([mock_pinfo])
        terminate_debugpy_adapter("owner")
        mock_kill.assert_called_once_with(999, signal.SIGKILL)

    @patch('app.runner.psutil.process_iter')
    @patch('app.runner.files.conf_users', return_value={"owner": {"debug_port": 5678}})
    def test_terminate_debugpy_adapter_no_match(self, mock_conf, mock_iter):
        from app.runner import terminate_debugpy_adapter
        mock_iter.return_value = iter([])
        with patch('app.runner.logger.warning') as mock_warn:
            terminate_debugpy_adapter("owner")
            mock_warn.assert_called()

    @patch('app.runner.files.conf_users', return_value={})
    def test_terminate_debugpy_adapter_no_port(self, mock_conf):
        from app.runner import terminate_debugpy_adapter
        with patch('app.runner.logger.warning') as mock_warn:
            terminate_debugpy_adapter("owner")
            mock_warn.assert_called_with("no debug_port configured for owner %s", "owner")

    def test_get_debugpy_adapter_pid_match(self):
        from app.runner import get_debugpy_adapter_pid
        mock_pinfo = Mock()
        mock_pinfo.info = {'pid': 999, 'cmdline': ['python', '-m', 'debugpy.adapter', '--port', '5678']}
        with patch('app.runner.psutil.process_iter', return_value=iter([mock_pinfo])):
            pid = get_debugpy_adapter_pid(5678)
            assert pid == 999

    def test_get_debugpy_adapter_pid_no_match(self):
        from app.runner import get_debugpy_adapter_pid
        with patch('app.runner.psutil.process_iter', return_value=iter([])):
            pid = get_debugpy_adapter_pid(5678)
            assert pid is None

    @patch('app.runner.files.conf_users', return_value={"owner": {"debug_port": 5678}})
    def test_validate_debug_prerequisites_valid(self, mock_conf):
        from app.runner import validate_debug_prerequisites
        port = validate_debug_prerequisites("id", "owner")
        assert port == 5678

    @patch('app.runner.files.conf_users', return_value={"owner": {"debug_port": "invalid"}})
    def test_validate_debug_prerequisites_invalid(self, mock_conf):
        from app.runner import validate_debug_prerequisites
        with pytest.raises(Exception, match="Invalid or missing debug_port"):
            validate_debug_prerequisites("id", "owner")

class TestLibRunnerLoad:
    def test_load_lib_task_committed(self, mock_git_ops):
        from app.lib.runner import loadLib
        with patch('os.listdir', return_value=['lib.task.py']):
            mock_git_ops.infer_type_from_filename.return_value = 'task'
            result = loadLib("testset", "lib", "task")
            mock_git_ops.read_content.assert_called_once_with('data/git/testset', 'lib', 'task', committed=True)

    def test_load_lib_debug_uncommitted(self, mock_git_ops):
        from app.lib.runner import loadLib
        with patch('os.listdir', return_value=['lib.task.py']):
            mock_git_ops.infer_type_from_filename.return_value = 'task'
            result = loadLib("testset", "lib", "debug")
            mock_git_ops.read_content.assert_called_once_with('data/git/testset', 'lib', 'task', committed=False)

    def test_load_lib_no_file(self, mock_git_ops):
        from app.lib.runner import loadLib
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match="Library testset::lib not existing"):
                loadLib("testset", "lib", "task")

    def test_load_job_success(self, mock_db, mock_git_ops):
        from app.lib.runner import loadJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", owner="owner", set="testset", name="testname", kind="task", param="param", state="dequeued")
            session.add(job)
            session.commit()
        with patch('os.listdir', return_value=['testname.task.py']):
            mock_git_ops.infer_type_from_filename.return_value = "task"
            result = loadJob("testid")
            with Session(get_db_engine()) as session:
                updated = session.get(Job, "testid")
                assert updated.state == "running"
                assert updated.start is not None

    def test_load_job_debug_uncommitted(self, mock_db, mock_git_ops):
        from app.lib.runner import loadJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", owner="owner", set="testset", name="testname", kind="debug", param="param", state="dequeued")
            session.add(job)
            session.commit()
        with patch('os.listdir', return_value=['testname.task.py']):
            mock_git_ops.infer_type_from_filename.return_value = "task"
            result = loadJob("testid")
            mock_git_ops.read_content.assert_called_with('data/git/testset', 'testname', 'task', committed=False)

    def test_load_job_no_job(self, mock_db, mock_git_ops):
        from app.lib.runner import loadJob
        with pytest.raises(Exception, match="Job testid not existing"):
            loadJob("testid")

    def test_load_job_no_file(self, mock_db, mock_git_ops):
        from app.lib.runner import loadJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            job = Job(id="testid", owner="owner", set="testset", name="testname", kind="task", param="param", state="dequeued")
            session.add(job)
            session.commit()
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match="Scriptlet testset::testname not existing"):
                loadJob("testid")
