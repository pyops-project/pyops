import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch

import tempfile
import os
import shutil
from unittest.mock import Mock, patch
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient
from multiprocessing import Process
from pathlib import Path

"""Common mock config fixture for tests."""
@pytest.fixture(scope="function")
def mock_conf(tmp_path):
    from app.lib import files
    import json
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
            "pool_size": 2
        },
        "runner": {
            "git_pull_interval": 60,
            "venv": str(test_venv),
            "pkg": str(test_pkg)
        }
    }
    conf_path = tmp_path / "test_pyops.json"
    with open(conf_path, "w") as f:
        json.dump(mock_raw, f)
    files.Config.init(str(conf_path))
    yield

"""Standard isolated DB fixture for all tests using temporary SQLite with full patching and cleanup."""
@pytest.fixture(scope="function")
def mock_db(mock_conf, tmp_path):
    import gc
    from app.orm.database import Base, get_db_engine, init_db, destroy_db
    from app.orm.job import Job
    from app.orm.schedule import Schedule
    from unittest.mock import patch

    destroy_db()
    db_path = tmp_path / "test.db"
    if db_path.exists():
        db_path.unlink()
    init_db()

    try:
        yield
    finally:
        destroy_db()
        gc.collect()
        if db_path.exists():
            db_path.unlink()

"""Fixture for FastAPI TestClient with config patches and user override."""
@pytest.fixture
def mock_client(mock_conf, tmp_path, mock_db):
    from app.web import app

    def override_get_current_user(request):
        user = request.headers.get("X-User")
        if user:
            return {"id": user, "name": user}
        return None

    app.dependency_overrides["get_current_user"] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()

"""Fixture for mock git directory structure and OS mocks."""
@pytest.fixture
def mock_git_dir(tmp_path):
    # Create mock git structure: data/git/{set}/
    mock_data_dir = tmp_path / "data" / "git"
    mock_data_dir.mkdir(parents=True, exist_ok=True)
    
    def create_mock_set(set_name):
        set_dir = mock_data_dir / set_name
        set_dir.mkdir(exist_ok=True)
        (set_dir / ".git").mkdir()
        # Add sample files for list_scriptlets
        (set_dir / "sample_task.py").touch()
        (set_dir / "sample_view.html").touch()
        (set_dir / "README.md").write_text(f"# {set_name}\n")
        (set_dir / ".gitignore").write_text("*.bck\n")
        # Dummy for test_set post etc.
        (set_dir / "dummy.py").touch()
        # Controller for menu failures test
        (set_dir / "sample.controller.py").touch()

    create_mock_set("default")
    create_mock_set("test_set")
    
    mock = Mock()
    mock.tmp_path = tmp_path
    mock.mock_data_dir = mock_data_dir
    
    def mock_os_listdir(path):
        path_str = str(path)
        if path_str == str(mock_data_dir):
            return ["default", "test_set"]
        elif path_str == str(mock_data_dir / "default"):
            return ["sample_task.py", "sample_view.html", "README.md", ".gitignore", "dummy.py", "sample.controller.py"]
        elif path_str == str(mock_data_dir / "test_set"):
            return ["dummy.py", "README.md", ".gitignore", "sample.controller.py"]
        return []
    
    def mock_exists(p):
        p_str = str(p)
        return p_str.endswith('.git') or any(p_str.endswith(f'/data/git/{set}') for set in ['default', 'test_set'])
    
    with patch("os.listdir", side_effect=mock_os_listdir), \
         patch("os.path.exists", side_effect=mock_exists):
        yield mock
    
    if mock_data_dir.exists():
        shutil.rmtree(mock_data_dir, ignore_errors=True)

"""Fixture for mock git operations (functions and subprocess)."""
@pytest.fixture
def mock_git_ops(mock_git_dir):
    mock_data_dir = mock_git_dir.mock_data_dir
    mock = Mock()
    mock.tmp_path = mock_git_dir.tmp_path
    
    def mock_list_scriptlets():
        scriptlets = []
        sets = ["default", "test_set"]
        for set_name in sets:
            set_dir_path = str(mock_data_dir / set_name)
            files = ["sample_task.py", "sample_view.html", "README.md", "dummy.py", "sample.controller.py"]
            for file in files:
                name = Path(file).stem
                type_ = "task" if file.endswith(".py") and "controller" not in file else "controller" if "controller" in file else "view" if file.endswith(".html") else "markdown"
                scriptlets.append({"set": set_name, "name": name, "type": type_})
        return scriptlets
    
    def mock_write_content(repo, name, type_, content):
        ext = ".py" if type_ == "task" else ".html" if type_ == "view" else ".md"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
    
    def mock_read_content(repo, name, type_, committed=True):
        ext = ".py" if type_ == "task" else ".html" if type_ == "view" else ".md"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        return full_path.read_text(encoding="utf-8") if full_path.exists() else "pass"
    
    def mock_infer_type_from_filename(fn):
        if fn.endswith(".py"):
            return "task" if "controller" not in fn else "controller"
        elif fn.endswith(".html"):
            return "view"
        elif fn.endswith(".md"):
            return "markdown"
        return "unknown"
    
    def mock_check_name_unique(repo, name):
        return True
    
    def mock_get_controller_code(repo, name, debug=True):
        ext = ".py"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return "set = 'test'; service = 'test'; debug = False; head = 'head'; menu = 'menu'; body = 'body'; name = 'Test Controller'"

    def mock_get_view_content(repo, name, debug=False):
        ext = ".html"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return "<div>Default View</div>"

    def mock_get_file_path(repo, name, type_):
        ext = ".py" if type_ == "task" else ".html" if type_ == "view" else ".md"
        return f"{name}{ext}"

    def mock_get_repo_path(set_name):
        return f"data/git/{set_name}"

    def mock_get_git_base():
        return str(mock_data_dir)

    def mock_delete_file_and_commit(repo, file_path, msg):
        full_path = mock_git_dir.tmp_path / repo / file_path
        if full_path.exists():
            full_path.unlink()

    def mock_rollback_committed(set_name, name, type_):
        pass

    def mock_perform_git_add_commit_push(repo, file_path, msg):
        pass

    def mock_add_commit_push(msg):
        pass

    def mock_get_repo_status(set_name):
        return {"branch": "main", "status": "clean"}

    from unittest.mock import MagicMock

    mock_list_scriptlets_mock = MagicMock(side_effect=mock_list_scriptlets)
    mock_get_git_base_mock = MagicMock(return_value=str(mock_data_dir))
    mock_get_repo_path_mock = MagicMock(side_effect=mock_get_repo_path)
    mock_write_content_mock = MagicMock(side_effect=mock_write_content)
    mock_read_content_mock = MagicMock(side_effect=mock_read_content)
    mock_infer_type_mock = MagicMock(side_effect=mock_infer_type_from_filename)
    mock_check_name_unique_mock = MagicMock(return_value=True)
    mock_get_file_path_mock = MagicMock(side_effect=mock_get_file_path)
    mock_perform_git_add_commit_push_mock = MagicMock()
    mock_delete_file_and_commit_mock = MagicMock(side_effect=mock_delete_file_and_commit)
    mock_rollback_committed_mock = MagicMock(side_effect=mock_rollback_committed)
    mock_get_repo_status_mock = MagicMock(return_value={"branch": "main"})
    mock_get_controller_code_mock = MagicMock(side_effect=mock_get_controller_code)
    mock_get_view_content_mock = MagicMock(side_effect=mock_get_view_content)
    mock_subprocess_run = MagicMock(return_value=Mock(returncode=0, stdout="", stderr=""))
    mock_check_output = MagicMock(return_value=b"")

    mock.list_scriptlets = mock_list_scriptlets_mock
    mock.get_git_base = mock_get_git_base_mock
    mock.get_repo_path = mock_get_repo_path_mock
    mock.write_content = mock_write_content_mock
    mock.read_content = mock_read_content_mock
    mock.infer_type_from_filename = mock_infer_type_mock
    mock.check_name_unique = mock_check_name_unique_mock
    mock.get_file_path = mock_get_file_path_mock
    mock.perform_git_add_commit_push = mock_perform_git_add_commit_push_mock
    mock.delete_file_and_commit = mock_delete_file_and_commit_mock
    mock.rollback_committed = mock_rollback_committed_mock
    mock.get_repo_status = mock_get_repo_status_mock
    mock.get_controller_code = mock_get_controller_code_mock
    mock.get_view_content = mock_get_view_content_mock

    with patch("app.lib.git.list_scriptlets", mock_list_scriptlets_mock), \
        patch("app.lib.git.get_git_base", mock_get_git_base_mock), \
        patch("app.lib.git.get_repo_path", mock_get_repo_path_mock), \
        patch("app.lib.git.write_content", mock_write_content_mock), \
        patch("app.lib.git.read_content", mock_read_content_mock), \
        patch("app.lib.git.infer_type_from_filename", mock_infer_type_mock), \
        patch("app.lib.git.check_name_unique", mock_check_name_unique_mock), \
        patch("app.lib.git.get_file_path", mock_get_file_path_mock), \
        patch("app.lib.git.perform_git_add_commit_push", mock_perform_git_add_commit_push_mock), \
        patch("app.lib.git.delete_file_and_commit", mock_delete_file_and_commit_mock), \
        patch("app.lib.git.rollback_committed", mock_rollback_committed_mock), \
        patch("app.lib.git.get_repo_status", mock_get_repo_status_mock), \
        patch("app.web.get_controller_code", mock_get_controller_code_mock), \
        patch("app.web.get_view_content", mock_get_view_content_mock), \
        patch("subprocess.run", mock_subprocess_run), \
        patch("subprocess.check_output", mock_check_output):
        yield mock

@pytest.fixture
def mock_process():
    with patch("multiprocessing.Process") as mock_proc:
        mock_proc.return_value.start.return_value = None
        mock_proc.return_value.join.return_value = None
        mock_proc.return_value.is_alive.return_value = False
        yield mock_proc
        # Teardown: Terminate any alive mock processes
        if mock_proc.return_value.is_alive():
            mock_proc.return_value.terminate()

@pytest.fixture
def mock_croniter():
    from datetime import datetime, timedelta
    with patch("croniter.croniter") as mock_cron:
        mock_instance = Mock()
        mock_instance.get_next.return_value = datetime.now() + timedelta(minutes=1)
        mock_instance.get_prev.return_value = datetime.now() - timedelta(minutes=1)
        mock_cron.return_value = mock_instance
        yield mock_cron


@pytest.fixture
def mock_jedi():
    import jedi

    class MockCompletion:
        def __init__(self):
            self.name = "test_func"
            self.type = "function"
            self._docstring = "Test function doc"

        def docstring(self):
            return self._docstring

    with patch("jedi.Script") as mock_script:
        mock_script.return_value.complete.return_value = [MockCompletion()]
        yield mock_script

@pytest.fixture(scope="function")
def test_temp_dir(tmp_path):
    test_cache = tmp_path / ".cache"
    test_cache.mkdir(exist_ok=True)
    test_tmp = test_cache / "tmp"
    test_tmp.mkdir(exist_ok=True)
    yield str(test_tmp)

@pytest.fixture(scope="function")
def patch_tempfile(test_temp_dir):
    with patch('tempfile.gettempdir', return_value=test_temp_dir):
        yield

@pytest.fixture()
def mock_datetime():
    from datetime import datetime as real_datetime
    with patch('app.runner.datetime', real_datetime):
        yield

def pytest_configure(config):
    import multiprocessing
    multiprocessing.set_start_method('spawn', force=True)
    import tracemalloc
    tracemalloc.start()
    import warnings

    def custom_showwarning(message, category, filename, lineno, file=None, line=None):
        if category == ResourceWarning:
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('traceback')
            print("ResourceWarning detected. Top tracemalloc stats:")
            for stat in top_stats[:10]:
                print(stat)
                for line in stat.traceback.format():
                    print(line)
        # Call original showwarning
        original_showwarning(message, category, filename, lineno, file, line)

    original_showwarning = warnings.showwarning
    warnings.showwarning = custom_showwarning
    warnings.filterwarnings("always", category=ResourceWarning)

@pytest.fixture
def mock_controller_glob():
    code = """
set = "testset"
service = "testservice"
debug = False
head = "head"
menu = "menu"
body = "body"
name = "Test Controller"
"""
    with patch("app.web.get_controller_code", return_value=code):
        yield
