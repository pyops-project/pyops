import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY
from sqlalchemy.orm import Session
from datetime import datetime
import os
import shutil
import signal
import psutil
import sys

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
        mock_exists.side_effect = [True, True]
        mock_listdir.return_value = []
        mock_rmtree.return_value = None
        mock_remove.return_value = None
        result = runner.drop("123", "set")
        assert result is True
        mock_remove.assert_called_once_with('/tmp/pyops/123.sock')
        mock_rmtree.assert_has_calls([
            call('/tmp/mock/data/jobs/set/123'),
            call('/tmp/mock/data/jobs/set')
        ], any_order=False)

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    def test_drop_no_workspace(self, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = False  # No workspace exists
        result = runner.drop("123", "set")
        assert result is False

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    @patch('shutil.rmtree', side_effect=OSError("rm error"))
    def test_drop_error(self, mock_rmtree, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = True
        result = runner.drop("123", "set")
        assert result is False

    @patch('app.lib.runner.files.conf_data', return_value={"jobs": "/tmp/mock/data/jobs"})
    @patch('tempfile.gettempdir')
    @patch('os.path.exists')
    def test_drop_no_exist(self, mock_exists, mock_gettempdir, mock_conf_data):
        from app.lib import runner
        # Edge: no exist returns False
        mock_gettempdir.return_value = '/tmp'
        mock_exists.return_value = False
        result = runner.drop("123", "set")
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
        from app.lib import job
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'lib code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.py']):
            result = job.loadLib("set", "name", "task", mock_git_ops)
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=True)
            assert result == 'lib code'

    def test_load_lib_no_file(self, mock_git_ops):
        from app.lib import job
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match=r"Scriptlet set::name not existing"):
                job.loadLib("set", "name", "task", mock_git_ops)

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

    def test_load_job_cas_aborts_when_cancel_races_mid_flight(self, mock_db, mock_git_ops):
        """Cancel arrives between loadJob's initial 'dequeued' read and its commit.

        The conditional UPDATE must affect 0 rows, loadJob must raise, and the
        'cancel' state must NOT be overwritten with 'loading' — otherwise the
        runner term-loop loses track of the cancel and the job runs to completion
        as 'done' (the bulk-cancel race observed against the vsix integration tests).
        """
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="dequeued", owner="owner")
            test_session.add(job)
            test_session.commit()
        mock_git_ops.infer_type_from_filename.return_value = 'task'

        def cancel_races_in_during_io(*args, **kwargs):
            # Simulate the HTTP cancel handler flipping the state from another
            # process while loadJob is still reading code from disk.
            with Session(get_db_engine()) as cancel_session:
                cancel_target = cancel_session.get(Job, "123")
                cancel_target.state = "cancel"
                cancel_session.commit()
            return 'code'

        mock_git_ops.read_content.side_effect = cancel_races_in_during_io
        with patch('os.listdir', return_value=['name.task.py']):
            with pytest.raises(Exception, match=r"state changed to cancel during loadJob"):
                runner.loadJob("123")
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            # CAS lost — state preserved at 'cancel', NOT overwritten to 'loading'.
            assert fetched.state == "cancel"
            assert fetched.start is None  # 'loading' write never happened

class TestProcessImports:
    @patch('app.lib.job.loadLib')
    def test_process_imports_valid_lib(self, mock_load_lib):
        from app.lib.job import process_imports
        mock_load_lib.return_value = 'lib_code'
        text = "#!{set::lib}\npass"
        libs = []
        process_imports(text, 'set', 'task', libs)
        assert libs == [{"code": "lib_code", "filename": "set::lib"}]
        mock_load_lib.assert_called_once_with('set', 'lib', 'task', ANY)

    @patch('app.lib.job.loadLib')
    def test_process_imports_empty_set(self, mock_load_lib):
        from app.lib.job import process_imports
        mock_load_lib.return_value = 'lib_code'
        text = "#!{::lib}\npass"
        libs = []
        process_imports(text, 'current_set', 'task', libs)
        assert libs == [{"code": "lib_code", "filename": "current_set::lib"}]
        mock_load_lib.assert_called_once_with('current_set', 'lib', 'task', ANY)

    def test_process_imports_invalid_format(self):
        from app.lib.job import process_imports
        text = "#!{invalid}\npass"
        libs = []
        result = process_imports(text, 'set', 'task', libs)
        assert result == "#!{invalid}\npass"
        assert libs == []

    def test_process_imports_no_match(self):
        from app.lib.job import process_imports
        text = "pass\n"
        libs = []
        result = process_imports(text, 'set', 'task', libs)
        assert result == "pass\n"
        assert libs == []

    @patch('app.lib.job.loadLib', side_effect=Exception('load error'))
    def test_process_imports_load_error(self, mock_load_lib):
        from app.lib.job import process_imports
        text = "#!{set::lib}\npass"
        libs = []
        with pytest.raises(Exception, match='load error'):
            process_imports(text, 'set', 'task', libs)

class TestProcessInstallLines:
    @patch('app.lib.job.process_install_lines')
    @patch('os.makedirs')
    @patch('app.lib.job.subprocess.run')
    @patch('app.lib.job.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_pip_install(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.lib.job import process_install_lines
        # Mock pip install succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!pip_module']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == lines
        assert mock_run.call_count == 1
        expected_cmd = ". /venv/testset/bin/activate && pip install pip_module"
        mock_run.assert_called_once_with(expected_cmd, shell=True, capture_output=True, text=True, check=True)

    @patch('app.lib.job.process_install_lines')
    @patch('os.makedirs')
    @patch('app.lib.job.subprocess.run')
    @patch('app.lib.job.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_pip_install_with_args(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.lib.job import process_install_lines
        # Mock pip install with additional arguments succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!torch --index-url https://download.pytorch.org/whl/cu130']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == lines
        assert mock_run.call_count == 1
        expected_cmd = ". /venv/testset/bin/activate && pip install torch --index-url https://download.pytorch.org/whl/cu130"
        mock_run.assert_called_once_with(expected_cmd, shell=True, capture_output=True, text=True, check=True)

    @patch('app.lib.job.process_install_lines')
    @patch('os.makedirs')
    @patch('app.lib.job.subprocess.run')
    @patch('app.lib.job.os.path.join', side_effect=lambda *args: '/'.join(args))
    @patch('app.lib.job.os.path.exists', return_value=False)
    def test_process_install_lines_git_clone(self, mock_exists, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.lib.job import process_install_lines
        # Mock git clone succeeds
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        lines = ['#!https://github.com/user/repo.git']
        process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        # Check that git clone was called with correct args
        mock_run.assert_any_call(['git', 'clone', 'https://github.com/user/repo.git', '/pkg/testset/repo'], capture_output=True, text=True, cwd='/pkg/testset')

    @patch('app.lib.job.process_install_lines')
    @patch('os.makedirs')
    @patch('app.lib.job.subprocess.run')
    @patch('app.lib.job.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_no_bang(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.lib.job import process_install_lines
        lines = ['pass']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == ['pass']
        mock_run.assert_not_called()

    @patch('app.lib.job.process_install_lines')
    @patch('os.makedirs')
    @patch('app.lib.job.subprocess.run')
    @patch('app.lib.job.os.path.join', side_effect=lambda *args: '/'.join(args))
    def test_process_install_lines_empty(self, mock_join, mock_run, mock_makedirs, mock_ensure):
        from app.lib.job import process_install_lines
        lines = ['!']
        result = process_install_lines(lines, '/venv', '/pkg', 'testset', 'job_id')
        assert result == ['!']
        mock_run.assert_not_called()

class TestKillProcTree:
    @patch('app.lib.job.os.kill')
    def test_kill_proc_tree_single_process(self, mock_os_kill):
        from app.lib.job import kill_proc_tree
        mock_proc = Mock()
        mock_proc.pid = 123
        processes = [mock_proc]
        kill_proc_tree(processes, signal.SIGTERM)
        mock_os_kill.assert_called_once_with(123, signal.SIGTERM)

    @patch('app.lib.job.os.kill')
    def test_kill_proc_tree_multiple_processes(self, mock_os_kill):
        from app.lib.job import kill_proc_tree
        mock_proc1 = Mock()
        mock_proc1.pid = 123
        mock_proc2 = Mock()
        mock_proc2.pid = 456
        processes = [mock_proc1, mock_proc2]
        kill_proc_tree(processes, signal.SIGKILL)
        assert mock_os_kill.call_count == 2
        mock_os_kill.assert_any_call(123, signal.SIGKILL)
        mock_os_kill.assert_any_call(456, signal.SIGKILL)

    @patch('app.lib.job.os.kill', side_effect=OSError("Process already dead"))
    def test_kill_proc_tree_oserror_ignored(self, mock_os_kill):
        from app.lib.job import kill_proc_tree
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
        result = runner.drop("123", "set")
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
        result = runner.drop("123", "set")
        assert result is False

    def test_load_lib_debug(self, mock_git_ops):
        from app.lib import job
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'lib code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.py']):
            result = job.loadLib("set", "name", "debug", mock_git_ops)
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=False)
            assert result == 'lib code'


class TestAssignJob:
    def test_assign_job_records_pid_and_start(self, mock_db):
        """assignJob now only sets pid + start. The flip to `running` is
        driven by the job process's `state=running` ZMQ message (handled
        by markRunning) after `exec(param)` returns."""
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="loading", owner="owner")
            test_session.add(job)
            test_session.commit()
        runner.assignJob("123", 456)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            # State stays at 'loading' — markRunning hasn't fired yet.
            assert fetched.state == "loading"
            assert fetched.start is not None
            assert fetched.pid == 456

    def test_assign_job_preserves_cancel_state(self, mock_db):
        """A cancel that races in before assignJob lands must survive: we
        only touch pid+start, never state."""
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="cancel", owner="owner")
            test_session.add(job)
            test_session.commit()
        runner.assignJob("123", 456)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "cancel"
            assert fetched.pid == 456
            assert fetched.start is not None

    def test_assign_job_preserves_cancelling_state(self, mock_db):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="cancelling", owner="owner")
            test_session.add(job)
            test_session.commit()
        runner.assignJob("123", 456)
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "123")
            assert fetched.state == "cancelling"
            assert fetched.pid == 456


class TestMarkRunning:
    def test_mark_running_flips_loading_to_running(self, mock_db):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as s:
            s.add(Job(id="m1", set="s", name="n", kind="task", param="", state="loading", owner="o", pid=42))
            s.commit()
        runner.markRunning("m1")
        with Session(get_db_engine()) as s:
            fetched = s.get(Job, "m1")
            assert fetched.state == "running"
            assert fetched.pid == 42  # pid is untouched

    def test_mark_running_preserves_terminal_state(self, mock_db):
        """A late `state=running` arriving after the job already finished
        or was cancelled must not regress the row to running."""
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        for terminal in ("cancel", "cancelling", "cancelled", "done", "failed", "remove", "removed"):
            with Session(get_db_engine()) as s:
                s.query(Job).filter(Job.id == "m2").delete()
                s.add(Job(id="m2", set="s", name="n", kind="task", param="", state=terminal, owner="o", pid=42))
                s.commit()
            runner.markRunning("m2")
            with Session(get_db_engine()) as s:
                fetched = s.get(Job, "m2")
                assert fetched.state == terminal, f"markRunning regressed {terminal!r}"

class TestTerminateJobProcesses:
    @patch('app.orm.database.conf_data', return_value={"db": "/tmp/test.db"})
    @patch('app.runner.Session')
    @patch('app.runner.postTerm')
    @patch('app.lib.job.os.killpg')
    def test_terminate_graceful_quick(self, mock_killpg, mock_postTerm, mock_Session, mock_conf_data):
        """SIGTERM the group; group drains immediately (killpg(0) → ProcessLookupError) → postTerm."""
        from app.runner import terminate_job_processes
        mock_session_instance = Mock()
        mock_job = Mock(state='running', pid=123)
        mock_session_instance.get.return_value = mock_job
        mock_Session.return_value.__enter__.return_value = mock_session_instance
        mock_Session.return_value.__exit__.return_value = False

        # First killpg(pid, SIGTERM) succeeds, then killpg(pid, 0) raises
        # ProcessLookupError to signal the group has drained.
        call_count = {'n': 0}
        def killpg_side_effect(pgid, sig):
            call_count['n'] += 1
            if sig == 0:
                raise ProcessLookupError()
            # SIGTERM succeeds silently
        mock_killpg.side_effect = killpg_side_effect

        terminate_job_processes("job_id", 123, 'owner')
        # Sent SIGTERM, then polled with sig=0 and got "group gone".
        assert mock_killpg.call_args_list[0] == call(123, signal.SIGTERM)
        mock_postTerm.assert_called_once_with("job_id")

    @patch('app.orm.database.conf_data', return_value={"db": "/tmp/test.db"})
    @patch('app.runner.Session')
    @patch('app.runner.postTerm')
    @patch('app.lib.job.os.killpg')
    @patch('time.sleep')
    @patch('time.time')
    def test_terminate_timeout_force_kill(self, mock_time, mock_sleep, mock_killpg, mock_post, mock_Session, mock_conf_data):
        """Group never drains → SIGKILL the group → postTerm(killed=True)."""
        from app.runner import terminate_job_processes
        mock_session_instance = Mock()
        mock_job = Mock(state='running', pid=123)
        mock_session_instance.get.return_value = mock_job
        mock_Session.return_value.__enter__.return_value = mock_session_instance
        mock_Session.return_value.__exit__.return_value = False
        mock_time.side_effect = iter(range(32))
        mock_sleep.return_value = None
        # killpg always succeeds — pretend the group's still alive
        # throughout the graceful window so we fall through to SIGKILL.
        mock_killpg.return_value = None

        terminate_job_processes("job_id", 123, 'owner', graceful_timeout=15.0)
        # SIGTERM first, then SIGKILL after timeout.
        sigterm_calls = [c for c in mock_killpg.call_args_list if c == call(123, signal.SIGTERM)]
        sigkill_calls = [c for c in mock_killpg.call_args_list if c == call(123, signal.SIGKILL)]
        assert len(sigterm_calls) == 1
        assert len(sigkill_calls) == 1
        mock_post.assert_called_once_with("job_id", killed=True)

    @patch('app.lib.job.psutil.Process')
    def test_get_process_tree_exists_children(self, mock_Process):
        from app.lib.job import get_process_tree
        mock_parent = Mock(pid=123)
        mock_child = Mock(pid=456)
        mock_parent.children.return_value = [mock_child]
        mock_Process.return_value = mock_parent
        tree = get_process_tree(123)
        assert len(tree) == 2
        assert tree[0].pid == 123
        assert tree[1].pid == 456


class TestKillProcGroup:
    @patch('app.lib.job.os.killpg')
    def test_kill_proc_group_sends_signal(self, mock_killpg):
        from app.lib.job import kill_proc_group
        kill_proc_group(123, signal.SIGTERM)
        mock_killpg.assert_called_once_with(123, signal.SIGTERM)

    @patch('app.lib.job.os.killpg', side_effect=ProcessLookupError())
    def test_kill_proc_group_swallows_process_lookup(self, mock_killpg):
        from app.lib.job import kill_proc_group
        # Group already gone — no exception.
        kill_proc_group(123, signal.SIGKILL)
        mock_killpg.assert_called_once()

    @patch('app.lib.job.os.killpg', side_effect=PermissionError())
    def test_kill_proc_group_swallows_permission(self, mock_killpg):
        from app.lib.job import kill_proc_group
        kill_proc_group(123, signal.SIGKILL)

    @patch('app.lib.job.os.killpg', side_effect=OSError())
    def test_kill_proc_group_propagates_other_oserror(self, mock_killpg):
        from app.lib.job import kill_proc_group
        with pytest.raises(OSError):
            kill_proc_group(123, signal.SIGKILL)


class TestProcGroupAlive:
    @patch('app.lib.job.os.killpg')
    def test_proc_group_alive_true_when_killpg_succeeds(self, mock_killpg):
        from app.lib.job import proc_group_alive
        # killpg(pgid, 0) succeeds → group has at least one process.
        assert proc_group_alive(123) is True
        mock_killpg.assert_called_once_with(123, 0)

    @patch('app.lib.job.os.killpg', side_effect=ProcessLookupError())
    def test_proc_group_alive_false_when_group_gone(self, mock_killpg):
        from app.lib.job import proc_group_alive
        assert proc_group_alive(123) is False

    @patch('app.lib.job.os.killpg', side_effect=PermissionError())
    def test_proc_group_alive_false_when_permission_denied(self, mock_killpg):
        from app.lib.job import proc_group_alive
        assert proc_group_alive(123) is False

    def test_get_process_tree_pid_gone(self):
        from app.lib.job import get_process_tree
        with patch('app.lib.job.psutil.Process', side_effect=psutil.NoSuchProcess(123)):
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
        mock_drop.assert_called_once_with("testid", "set")
        with Session(get_db_engine()) as session:
            assert session.get(Job, "testid") is None

class TestProcessInstallLines:
    @patch('app.lib.job.subprocess.run')
    def test_process_install_lines_pip(self, mock_run, mock_conf):
        from app.lib.job import process_install_lines
        import io
        lines = ["code\n", "#!torch --index-url https://download.pytorch.org/whl/cpu\n", "more code\n"]
        mock_run.return_value = Mock()
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        result_lines = process_install_lines(lines, "/venv", "/pkg", "set", "id", stdout_file, stderr_file)
        # Per-job: single pip call, no bootstrap upgrade, array form (not shell), no --upgrade.
        assert mock_run.call_count == 1
        mock_run.assert_called_with(
            ['/venv/set/bin/pip', 'install', '--no-cache-dir', 'torch', '--index-url', 'https://download.pytorch.org/whl/cpu'],
            stdout=stdout_file, stderr=stderr_file, text=True, check=True,
        )

    @patch('app.lib.job.subprocess.run')
    def test_process_install_lines_pip_upgrade_mode(self, mock_run, mock_conf):
        """Update Dependencies path passes upgrade=True → pip gets `--upgrade --upgrade-strategy eager`."""
        from app.lib.job import process_install_lines
        import io
        lines = ["#!torch\n"]
        mock_run.return_value = Mock()
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        process_install_lines(lines, "/venv", "/pkg", "set", "id", stdout_file, stderr_file, upgrade=True)
        mock_run.assert_called_with(
            ['/venv/set/bin/pip', 'install', '--no-cache-dir', '--upgrade', '--upgrade-strategy', 'eager', 'torch'],
            stdout=stdout_file, stderr=stderr_file, text=True, check=True,
        )

    @patch('os.path.exists')
    @patch('app.lib.job.subprocess.run')
    def test_process_install_lines_git_no_pull_in_per_job_mode(self, mock_run, mock_exists, mock_conf):
        """Per-job: existing git repo → checkout if branch given but NO `git pull`.
        The "Update Dependencies" path is the only one that pulls."""
        from app.lib.job import process_install_lines
        import io
        mock_run.return_value = Mock(returncode=0, stderr="")
        mock_exists.return_value = True  # repo_dir already exists
        lines = ["#! https://repo.git mybranch\n"]
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        process_install_lines(lines, "/venv", "/pkg", "set", "id", stdout_file, stderr_file)
        # Calls: git checkout mybranch, then pip install -e (no pull).
        commands = [c.args[0] for c in mock_run.call_args_list]
        assert any('checkout' in cmd and 'mybranch' in cmd for cmd in commands), commands
        assert not any('pull' in cmd for cmd in commands), f"git pull should not run in per-job mode: {commands}"

    @patch('os.path.exists')
    @patch('app.lib.job.subprocess.run')
    def test_process_install_lines_git_pull_in_upgrade_mode(self, mock_run, mock_exists, mock_conf):
        """Update Dependencies path: existing git repo → checkout + `git pull`."""
        from app.lib.job import process_install_lines
        import io
        mock_run.return_value = Mock(returncode=0, stderr="")
        mock_exists.return_value = True
        lines = ["#! https://repo.git mybranch\n"]
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        process_install_lines(lines, "/venv", "/pkg", "set", "id", stdout_file, stderr_file, upgrade=True)
        commands = [c.args[0] for c in mock_run.call_args_list]
        assert any('pull' in cmd for cmd in commands), f"git pull expected in upgrade mode: {commands}"


class TestInstallGitRepoUpgradeFlag:
    @patch('app.lib.job.subprocess.run')
    def test_per_job_no_upgrade_flag(self, mock_run):
        """install_git_repo(upgrade=False) → plain `pip install -e`. No --upgrade,
        no --upgrade-strategy. Fast 'already satisfied' path on re-spawn."""
        from app.lib.job import install_git_repo
        mock_run.return_value = Mock(returncode=0)
        install_git_repo("/venv/bin/pip", "/pkg/myrepo", None, None, upgrade=False)
        cmd = mock_run.call_args.args[0]
        assert "--upgrade" not in cmd
        assert "--upgrade-strategy" not in cmd
        assert cmd[-2:] == ["-e", "/pkg/myrepo"]

    @patch('app.lib.job.subprocess.run')
    def test_update_path_adds_upgrade_eager(self, mock_run):
        """install_git_repo(upgrade=True) → adds --upgrade --upgrade-strategy eager
        so pip re-resolves and picks up newer commit's setup.py changes."""
        from app.lib.job import install_git_repo
        mock_run.return_value = Mock(returncode=0)
        install_git_repo("/venv/bin/pip", "/pkg/myrepo", None, None, upgrade=True)
        cmd = mock_run.call_args.args[0]
        assert "--upgrade" in cmd
        assert "--upgrade-strategy" in cmd
        idx = cmd.index("--upgrade-strategy")
        assert cmd[idx + 1] == "eager"


class TestProcessCodeInstallsUpgradeFlag:
    """process_code_installs threads `upgrade` through to process_install_lines.
    The Update Dependencies flow needs upgrade=True to propagate end-to-end."""

    def test_upgrade_flag_propagates_to_pip_install(self, mock_conf, tmp_path, monkeypatch):
        from app.lib import job as job_lib

        observed = []

        def fake_install_lines(lines, venv_path, pkg_path, set_, job_id, stdout, stderr, upgrade=False):
            observed.append(upgrade)
            return lines

        # Make set_install_lock a no-op so the test doesn't hit real flock
        from contextlib import contextmanager

        @contextmanager
        def fake_lock(name):
            yield

        monkeypatch.setattr(job_lib, "process_install_lines", fake_install_lines)
        monkeypatch.setattr("app.lib.sets.set_install_lock", fake_lock)

        from unittest.mock import MagicMock
        job_lib.process_code_installs(
            "#!somepkg\n",
            "#!othercode\n",
            [{"code": "#!libpkg\n", "filename": "x::y"}],
            "/venv", "/pkg", "alpha", "j1",
            MagicMock(), MagicMock(),
            upgrade=True,
        )
        assert observed == [True, True, True], f"upgrade should propagate to all 3 calls: {observed}"
