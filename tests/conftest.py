import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch

import contextlib
import sqlite3
import tempfile
import os
import shutil
from unittest.mock import Mock, patch
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient
from pathlib import Path


# --- E2E test auto-skip --------------------------------------------------
#
# E2E suites under `tests/e2e/` and `tests/pyopsctl/e2e/` spawn real
# pyops servers and take minutes to run; they don't belong in the
# default `pytest tests/` invocation that the `test` CI stage runs.
# Auto-mark and skip them unless --e2e is passed or the e2e path is
# targeted directly. The matching test:e2e CI stage covers both
# directories explicitly.

def pytest_addoption(parser):
    parser.addoption("--e2e", action="store_true", default=False, help="Run E2E tests")


def _is_e2e_item(item):
    p = str(item.fspath)
    return (
        "tests/e2e" in p or "tests\\e2e" in p
        or "tests/pyopsctl/e2e" in p or "tests\\pyopsctl\\e2e" in p
    )


def pytest_collection_modifyitems(config, items):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnknownMarkWarning)
        e2e_marker = pytest.mark.e2e

    run_e2e = config.getoption("--e2e", default=False)
    if not run_e2e:
        args = config.invocation_params.args
        run_e2e = any("e2e" in str(a) for a in args)

    for item in items:
        if _is_e2e_item(item):
            item.add_marker(e2e_marker)
            if not run_e2e:
                item.add_marker(pytest.mark.skip(
                    reason="E2E tests require --e2e flag or direct tests/<>/e2e/ path"
                ))
# -------------------------------------------------------------------------

"""Common mock config fixture for tests."""
@pytest.fixture(scope="function")
def mock_conf(tmp_path, monkeypatch):
    from app.lib import files
    from app.lib import runner as lib_runner
    import json
    # enqueue() refuses while the shutdown flag exists; a per-test path keeps a
    # pyops runner shutting down elsewhere on the host from failing tests
    monkeypatch.setattr(lib_runner, "SHUTDOWN_FLAG_PATH", str(tmp_path / "pyops" / "shutdown.flag"))
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

@pytest.fixture
def set_repo(mock_conf):
    """Factory for real git set repos under the configured git base.

    `set_repo(name, committed={}, drafts={})` creates `<git base>/<name>`,
    commits `committed` ({repo-relative path: content}) together with the
    README.md and `*.bck` .gitignore a set created by `set_create` starts
    with, then writes `drafts` to the working tree without committing.
    Returns the repo path. The repo lives under the per-test tmp_path, so
    it goes with it."""
    import subprocess
    from app.lib import files

    def git(repo, *args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def write(repo, tree):
        for rel, content in tree.items():
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def make(name, committed=None, drafts=None):
        repo = os.path.join(files.conf_data()["git"], name)
        os.makedirs(repo)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "tests@pyops.local")
        git(repo, "config", "user.name", "pyops tests")
        git(repo, "config", "commit.gpgsign", "false")
        write(repo, {"README.md": f"# {name}\n", ".gitignore": "*.bck\n", **(committed or {})})
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "initial")
        write(repo, drafts or {})
        return repo

    return make

"""Base DB fixture: the configured temp DB path (tmp_path/test.db), absent, with the models
registered and no global engine bound; yields the path."""
@pytest.fixture(scope="function")
def empty_db(mock_conf, tmp_path):
    import gc
    from app.orm.database import destroy_db
    from app.orm.job import Job
    from app.orm.schedule import Schedule

    destroy_db()
    db_path = tmp_path / "test.db"
    if db_path.exists():
        db_path.unlink()

    try:
        yield db_path
    finally:
        destroy_db()
        gc.collect()
        if db_path.exists():
            db_path.unlink()

"""Standard isolated DB fixture for all tests using temporary SQLite with full patching and cleanup."""
@pytest.fixture(scope="function")
def mock_db(empty_db):
    from app.orm.database import init_db

    init_db()

# FROZEN: the sqlite_master DDL that `create_all` emits for the v3.3.6 models
# (generated once from the v3.3.6 tag; trailing whitespace and index order
# normalised). It is the "existing deployment" baseline the schema-sync tests
# upgrade from — never regenerate it from HEAD, or it stops being the 3.3.6
# shape once the models gain columns.
V3_3_6_SCHEMA = """
CREATE TABLE job (
	id VARCHAR NOT NULL,
	"set" VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	owner VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	state VARCHAR NOT NULL,
	drop_done BOOLEAN NOT NULL,
	max_retries INTEGER NOT NULL,
	param VARCHAR,
	pid INTEGER,
	detail VARCHAR,
	overrides VARCHAR,
	start DATETIME,
	"end" DATETIME,
	PRIMARY KEY (id)
);
CREATE INDEX ix_job_end ON job ("end");
CREATE INDEX ix_job_kind ON job (kind);
CREATE INDEX ix_job_name ON job (name);
CREATE INDEX ix_job_owner ON job (owner);
CREATE INDEX ix_job_pid ON job (pid);
CREATE INDEX ix_job_set ON job ("set");
CREATE INDEX ix_job_start ON job (start);
CREATE INDEX ix_job_state ON job (state);
CREATE TABLE schedule (
	id INTEGER NOT NULL,
	schedule VARCHAR NOT NULL,
	"set" VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	owner VARCHAR NOT NULL,
	active BOOLEAN NOT NULL,
	drop_done BOOLEAN NOT NULL,
	max_retries INTEGER NOT NULL,
	param VARCHAR,
	last DATETIME,
	PRIMARY KEY (id)
);
CREATE INDEX ix_schedule_active ON schedule (active);
CREATE INDEX ix_schedule_last ON schedule (last);
CREATE INDEX ix_schedule_name ON schedule (name);
CREATE INDEX ix_schedule_owner ON schedule (owner);
CREATE INDEX ix_schedule_schedule ON schedule (schedule);
CREATE INDEX ix_schedule_set ON schedule ("set");
"""

# Rows of an existing 3.3.6 deployment; terminal job state with drop_done=0
# and an inactive schedule, so the runner's start-up housekeeping leaves them alone.
V3_3_6_ROWS = """
INSERT INTO job (id, "set", name, owner, kind, state, drop_done, max_retries, param, start, "end")
VALUES ('seed-done', 'seed', 'task', 'seed_user', 'task', 'done', 0, 2, '{"k": 1}',
        '2026-01-01 00:00:00.000000', '2026-01-01 00:01:00.000000');
INSERT INTO schedule (id, schedule, "set", name, owner, active, drop_done, max_retries, param, last)
VALUES (1, '0 3 * * *', 'seed', 'task', 'seed_user', 0, 1, 0, '{}', NULL);
"""

# Frozen, populated schema baselines of released versions the schema sync must
# upgrade from (rule: docs/insights_app.md § Schema Sync). Only a release whose
# schema differs from the newest baseline gets an entry.
SCHEMA_BASELINES = {
    "3.3.6": V3_3_6_SCHEMA + V3_3_6_ROWS,
}

def sqlite_schema(path):
    """The sqlite_master rows (type, name, sql) of the SQLite file at path, read with stdlib sqlite3."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        return conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()

"""Factory fixture writing the populated baseline of a released version to a path with stdlib sqlite3 (no app code)."""
@pytest.fixture(scope="function")
def seed_db():
    def _seed(path, baseline):
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            conn.executescript(SCHEMA_BASELINES[baseline])

    return _seed

"""Each schema baseline version in turn, for tests that must upgrade from every one of them."""
@pytest.fixture(scope="function", params=list(SCHEMA_BASELINES))
def schema_baseline(request):
    return request.param

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
        (set_dir / "sample.controller.py").write_text("set = 'default'\nservice = 'sample.service'\nname = 'Test Controller'\ndebug = False\n")
        # Views for controller
        (set_dir / "head.html").write_text("<title>Head</title>")
        (set_dir / "menu.html").write_text("<nav>Menu</nav>")
        (set_dir / "body.html").write_text("<div>Body</div>")
        if set_name == "testset":
            # For wrong type test
            (set_dir / "wrongtype.controller.py").write_text("set = 'testset'\nservice = 'testservice'\nname = 'Wrong Type'\ndebug = False\nhead = 'wronghead'\nmenu = 'menu'\nbody = 'body'\n")
            (set_dir / "wronghead.html").write_text("<title>Head</title>")  # Wrong type, mock infer_type returns task
            # For controller tests
            (set_dir / "testname.py").write_text("set = 'testset'\nservice = 'testservice'\nname = 'Test Controller'\ndebug = False\nhead = 'head'\nmenu = 'menu'\nbody = 'body'\n")
            (set_dir / "head.html").write_text("<title>Head</title>")
            (set_dir / "menu.html").write_text("<nav>Menu</nav>")
            (set_dir / "body.html").write_text("<div>Body</div>")

    create_mock_set("default")
    create_mock_set("testset")
    
    mock = Mock()
    mock.tmp_path = tmp_path
    mock.mock_data_dir = mock_data_dir
    
    original_listdir = os.listdir
    original_exists = os.path.exists
    
    def mock_os_listdir(path):
        path_str = str(path)
        if path_str.endswith("data/git"):
            return ["default", "testset"]
        elif path_str.endswith("data/git/default"):
            return ["sample_task.py", "sample_view.html", "README.md", ".gitignore", "dummy.py", "sample.controller.py", "head.html", "menu.html", "body.html"]
        elif path_str.endswith("data/git/testset"):
            return ["dummy.py", "README.md", ".gitignore", "sample.controller.py", "wrongtype.controller.py", "wronghead.html", "testname.py", "head.html", "menu.html", "body.html"]
        else:
            return original_listdir(path)
    
    def mock_exists(p):
        p_str = str(p)
        if p_str.endswith('.git') or any(p_str.endswith(f'/data/git/{set}') for set in ['default', 'testset']):
            return True
        else:
            return original_exists(p)
    
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
        sets = ["default", "testset"]
        for set_name in sets:
            set_dir_path = str(mock_data_dir / set_name)
            files = ["sample_task.py", "sample_view.html", "README.md", "dummy.py", "sample.controller.py"]
            for file in files:
                name = file.split(".")[0]
                type_ = "task" if file.endswith(".py") and "controller" not in file else "controller" if "controller" in file else "view" if file.endswith(".html") else "markdown"
                scriptlets.append({"set": set_name, "name": name, "type": type_})
        return scriptlets
    
    def mock_write_content(repo, name, type_, content):
        ext = ".py" if type_ == "task" else ".html" if type_ == "view" else ".md" if type_ == "markdown" else ".js" if type_ == "jslibrary" else ".md"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
    
    def mock_read_content(repo, name, type_, committed=True, strict=False):
        if type_ == "controller" and name == "sample":
            return "set = 'default'\nservice = 'sample.service'\nname = 'Test Controller'\ndebug = False\nhead = 'head'\nmenu = 'menu'\nbody = 'body'\n"
        elif type_ == "controller" and name == "wrongtype":
            return "set = 'testset'\nservice = 'testservice'\nname = 'Wrong Type'\ndebug = False\nhead = 'wronghead'\nmenu = 'menu'\nbody = 'body'\n"
        elif type_ == "controller" and name == "testname":
            return "set = 'testset'\nservice = 'testservice'\nname = 'Test Controller'\ndebug = False\nhead = 'head'\nmenu = 'menu'\nbody = 'body'\n"
        ext = ".py" if type_ in ["task", "controller"] else ".html" if type_ == "view" else ".md" if type_ == "markdown" else ".js" if type_ == "jslibrary" else ".md"
        full_path = mock_git_dir.tmp_path / repo / f"{name}{ext}"
        return full_path.read_text(encoding="utf-8") if full_path.exists() else "pass"
    
    def mock_infer_type_from_filename(fn):
        if fn == "wronghead.html":
            return "task"  # Wrong type for test
        if fn == "testname.py":
            return "controller"  # For controller tests
        if fn.endswith(".py"):
            return "task" if "controller" not in fn else "controller"
        elif fn.endswith(".html"):
            return "view"
        elif fn.endswith(".md"):
            return "markdown"
        elif fn.endswith(".js"):
            return "jslibrary"
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

    def mock_delete_file_unstaged(repo, file_path):
        full_path = mock_git_dir.tmp_path / repo / file_path
        if full_path.exists():
            full_path.unlink()

    def mock_rollback_committed(set_name, name, type_):
        pass

    def mock_create_scriptlet(set_name, name, type_, content):
        repo = f"data/git/{set_name}"
        mock_write_content(repo, name, type_, content)
        return mock_get_file_path(repo, name, type_)

    def mock_add_commit_push(msg):
        pass

    def mock_get_repo_status(set_name):
        return {"branch": "main", "status": "clean"}

    def mock_find_scriptlets(repo, name):
        """git.find_scriptlets over the mocked listing: the files of `repo`
        (os.listdir, patched by mock_git_dir or the test) whose name part
        before the first dot is `name`, typed by the mocked
        infer_type_from_filename. The real git.resolve_scriptlet selects
        from these."""
        from app.lib.git import Scriptlet, validate_scriptlet_name
        validate_scriptlet_name(name)
        try:
            listing = os.listdir(repo)
        except FileNotFoundError:
            listing = []
        return [
            Scriptlet(name, mock_infer_type_mock(f), f, os.path.join(repo, f))
            for f in listing
            if f.split(".", 1)[0] == name and f.endswith((".py", ".html", ".js", ".md"))
        ]

    from unittest.mock import MagicMock

    mock_list_scriptlets_mock = MagicMock(side_effect=mock_list_scriptlets)
    mock_get_git_base_mock = MagicMock(return_value=str(mock_data_dir))
    mock_get_repo_path_mock = MagicMock(side_effect=mock_get_repo_path)
    mock_write_content_mock = MagicMock(side_effect=mock_write_content)
    mock_read_content_mock = MagicMock(side_effect=mock_read_content)
    mock_infer_type_mock = MagicMock(side_effect=mock_infer_type_from_filename)
    mock_check_name_unique_mock = MagicMock(return_value=True)
    mock_get_file_path_mock = MagicMock(side_effect=mock_get_file_path)
    mock_create_scriptlet_mock = MagicMock(side_effect=mock_create_scriptlet)
    mock_delete_file_unstaged_mock = MagicMock(side_effect=mock_delete_file_unstaged)
    mock_rollback_committed_mock = MagicMock(side_effect=mock_rollback_committed)
    mock_get_repo_status_mock = MagicMock(return_value={"branch": "main"})
    mock_find_scriptlets_mock = MagicMock(side_effect=mock_find_scriptlets)
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
    mock.create_scriptlet = mock_create_scriptlet_mock
    mock.delete_file_unstaged = mock_delete_file_unstaged_mock
    mock.rollback_committed = mock_rollback_committed_mock
    mock.get_repo_status = mock_get_repo_status_mock
    mock.find_scriptlets = mock_find_scriptlets_mock

    with patch("app.lib.git.list_scriptlets", mock_list_scriptlets_mock), \
        patch("app.lib.git.get_git_base", mock_get_git_base_mock), \
        patch("app.lib.git.get_repo_path", mock_get_repo_path_mock), \
        patch("app.lib.git.write_content", mock_write_content_mock), \
        patch("app.lib.git.read_content", mock_read_content_mock), \
        patch("app.lib.git.infer_type_from_filename", mock_infer_type_mock), \
        patch("app.lib.git.check_name_unique", mock_check_name_unique_mock), \
        patch("app.lib.git.get_file_path", mock_get_file_path_mock), \
        patch("app.lib.git.create_scriptlet", mock_create_scriptlet_mock), \
        patch("app.lib.git.delete_file_unstaged", mock_delete_file_unstaged_mock), \
        patch("app.lib.git.rollback_committed", mock_rollback_committed_mock), \
        patch("app.lib.git.get_repo_status", mock_get_repo_status_mock), \
        patch("app.lib.git.find_scriptlets", mock_find_scriptlets_mock), \
        patch("subprocess.run", mock_subprocess_run), \
        patch("subprocess.check_output", mock_check_output):
        yield mock

@pytest.fixture
def mock_popen():
    with patch("subprocess.Popen") as mock_popen:
        mock_process = Mock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Alive by default; use side_effect for sequences
        mock_process.terminate.return_value = None
        mock_process.wait.return_value = None
        mock_popen.return_value = mock_process
        yield mock_popen

@pytest.fixture
def mock_zmq():
    with patch("zmq.Context") as mock_context, \
         patch("zmq.Poller") as mock_poller:
        mock_socket = Mock()
        mock_socket.connect.return_value = None
        mock_socket.bind.return_value = None
        # Simulate ZMQError on first two sends, success on third for retry tests
        mock_socket.send.side_effect = [Exception("ZMQError"), Exception("ZMQError"), None]
        mock_socket.recv.return_value = b'ack'
        mock_socket.setsockopt.return_value = None
        mock_socket.close.return_value = None
        mock_context.return_value.socket.return_value = mock_socket
        mock_context.return_value.term.return_value = None
        mock_poller_instance = Mock()
        mock_poller_instance.register.return_value = None
        mock_poller_instance.poll.return_value = [(mock_socket, 0)]
        mock_poller.return_value = mock_poller_instance
        yield {"context": mock_context, "socket": mock_socket, "poller": mock_poller}

@pytest.fixture
def runner_sock_path():
    return "/tmp/pyops/runner.sock"

@pytest.fixture
def mock_send_completion():
    with patch("app.job_runner.send_completion") as mock_send:
        yield mock_send

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
    with (
        patch("app.web.get_controller_code", return_value=code),
        patch("app.lib.runner.find", return_value=["fake_job_123"])
    ):
        yield

@pytest.fixture(scope="function")
def job_env(tmp_path, mock_conf):
    data_base = tmp_path / "data"
    data_base.mkdir(parents=True, exist_ok=True)
    git_base = data_base / "git"
    venv_base = tmp_path / "venv"
    pkg_base = tmp_path / "pkg"
    return {
        "conf_data": {
            "jobs": str(data_base),
            "git": str(git_base),
            "db": str(tmp_path / "test.db"),  # Align w/ mock_db
            "web": {"bind": "bind"}
        },
        "conf_runner": {
            "venv": str(venv_base),
            "pkg": str(pkg_base),
            "git_pull_interval": 60
        },
        "conf_users": {"anonymous": {"debug_port": 5678}},
        "conf_web": {"bind": "127.0.0.1:8000"}
    }


@pytest.fixture
def pyops_job():
    """Factory: `pyops_job(script, conf, kind="task", run=False, **bound)`
    returns the globals of a pyops-set job running `<script>.task.py`.

    The globals are the ones the job runner builds
    (`job_runner.setup_job_globals`, including the `setname == "pyops"`
    extras `appspace`, `config`, `validate_set_name` and `CORE_DEPS`) from
    `conf`, the config sections it hands to the job: `data` and `runner`,
    optionally `users` and `web`. `bound` is bound into the globals before
    any scriptlet code runs: the job parameter's variables, or a stand-in
    for a runner-provided global (`control`, `listener`).
    set.library.py — the scripts' `#!{::set}` include — and the script are
    then exec'd in that order, under their real paths so coverage
    attributes them. With `run=True` the script runs as the job runner
    runs it (`__name__ == "__main__"`), so its main() executes and any
    exception it raises propagates; otherwise main() is left to the test."""
    from unittest.mock import MagicMock
    from app import job_runner
    pyops_set_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "pyops")

    def build(script, conf, kind="task", run=False, **bound):
        setspace = os.path.join(conf["data"]["jobs"], "pyops")
        with patch.object(job_runner, "_conf_data", conf["data"]), \
             patch.object(job_runner, "_conf_runner", conf["runner"]), \
             patch.object(job_runner, "_conf_users", conf.get("users", {})), \
             patch.object(job_runner, "_conf_web", conf.get("web", {"bind": "127.0.0.1:8000"})), \
             patch.object(job_runner, "_conf_log", {}), \
             patch.object(job_runner.pyops, "ServiceListener", return_value=MagicMock()):
            glob = job_runner.setup_job_globals(
                owner="alice",
                setspace=setspace,
                workspace=os.path.join(setspace, "jid"),
                web_bind=conf.get("web", {"bind": "127.0.0.1:8000"})["bind"],
                job_id="jid",
                kind=kind,
                setname="pyops",
                name=script,
            )
        glob.update(bound)
        if not run:
            glob["__name__"] = f"{script}_loaded"
        for fname in ("set.library.py", f"{script}.task.py"):
            path = os.path.join(pyops_set_dir, fname)
            with open(path) as f:
                exec(compile(f.read(), path, "exec"), glob)
        return glob

    return build
