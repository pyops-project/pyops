import pytest
from unittest.mock import Mock, patch, MagicMock, call
from sqlalchemy.orm import Session
from datetime import datetime
import os
import shutil

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
            job = Job(id="123", set="set", name="name", kind="task", param="param", state="todo", owner="owner")
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
                assert fetched.state == "running"
                assert fetched.start is not None
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=True)
            assert result == ("owner", "set", "name", "task", "param", 'code', None)

    def test_load_job_draft(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        # Draft mode uses committed=False (unstaged)
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="draft", param="param", state="todo", owner="owner")
            test_session.add(job)
            test_session.commit()
        mock_infer.return_value = 'task'
        def mock_read_side(*args, **kwargs):
            return 'draft code'
        mock_read_content.side_effect = mock_read_side
        with patch('os.listdir', return_value=['name.task.py']):
            result = runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "running"
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=False)
            assert result == ("owner", "set", "name", "draft", "param", 'draft code', None)

    def test_load_job_debug(self, mock_db, mock_git_ops):
        from app import runner
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        import json
        # Debug mode: unstaged git like draft, returns overrides
        mock_read_content = mock_git_ops.read_content
        mock_infer = mock_git_ops.infer_type_from_filename
        # Create job first
        with Session(get_db_engine()) as test_session:
            job = Job(id="123", set="set", name="name", kind="debug", param="param", state="todo", owner="owner")
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
                assert fetched.state == "running"
            mock_read_content.assert_called_once_with('data/git/set', 'name', 'task', committed=False)
            assert result == ("owner", "set", "name", "debug", "param", 'debug_code', '{"set::name": "override"}')

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
            with pytest.raises(AttributeError, match=r"'NoneType' object has no attribute 'state'"):
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
            job = Job(id="123", set="test_set", name="test_name", kind="task", param="param", state="todo", owner="owner")
            test_session.add(job)
            test_session.commit()
        with patch('os.listdir', return_value=[]):
            with pytest.raises(Exception, match=r"Scriptlet test_set::test_name not existing"):
                runner.loadJob("123")
            with Session(get_db_engine()) as test_session:
                fetched = test_session.get(Job, "123")
                assert fetched.state == "todo"  # State not updated on missing scriptlet
