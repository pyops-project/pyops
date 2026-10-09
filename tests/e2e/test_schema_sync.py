"""E2E: a real server started on a populated 3.3.6 database syncs it at start-up and serves its rows;
a sync that fails aborts the start-up."""

import contextlib
import os
import re
import sqlite3
import subprocess

import pytest

from tests.e2e.conftest import PROJECT_ROOT, SERVER_SHUTDOWN_TIMEOUT, SERVER_STARTUP_TIMEOUT, ServerLog, _kill_server


@pytest.fixture
def e2e_env(e2e_env, seed_db):
    """conftest's e2e_env with data/app.db pre-seeded as a populated 3.3.6 database."""
    seed_db(e2e_env["data_dir"] / "app.db", "3.3.6")
    return e2e_env


def _schema(path):
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        return conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()


def _run_until_exit(e2e_env):
    """Start the server like pyops_server, but wait for it to exit on its own instead of serving.

    Returns (exit code, ServerLog); the exit code is None when it was still running after the start-up timeout."""
    log_path = str(e2e_env["tmp_path"] / "server.log")
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [
                os.path.join(PROJECT_ROOT, ".venv", "bin", "gunicorn"),
                "-c", "app/gunicorn.py",
                "--access-logfile", "-",
                "--error-logfile", "-",
                "--capture-output",
            ],
            cwd=PROJECT_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYOPS_CONFIG": e2e_env["config_path"]},
            preexec_fn=os.setsid,
        )
        try:
            returncode = proc.wait(timeout=SERVER_STARTUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            returncode = None
        finally:
            _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
    return returncode, ServerLog(log_path)


def test_startup_on_v336_db_serves_old_rows(pyops_server, api):
    r = api.get("/scriptlet/job/item", params={"id": "seed-done"})
    assert r.status_code == 200, r.text
    assert (r.json()["state"], r.json()["param"]) == ("done", '{"k": 1}')

    r = api.get("/scriptlet/schedule/list")
    assert r.status_code == 200, r.text
    schedules = {s["id"]: s for s in r.json()}
    assert (schedules[1]["name"], schedules[1]["active"]) == ("task", False)

    assert pyops_server.contains(r"schema sync: added column job\.schedule_id")
    assert pyops_server.contains(r"schema sync: created index ix_job_schedule_id")
    pyops_server.assert_no_unexpected_errors()


def test_failed_sync_aborts_startup(e2e_env):
    """The server exits before the runner starts; the database keeps its schema and the log names the backup."""
    db = e2e_env["data_dir"] / "app.db"
    with contextlib.closing(sqlite3.connect(str(db))) as conn:
        # index names are global in SQLite: the sync's CREATE INDEX ix_job_schedule_id fails
        conn.executescript("CREATE TABLE stray (x INTEGER); CREATE INDEX ix_job_schedule_id ON stray (x);")
    schema = _schema(db)

    returncode, log = _run_until_exit(e2e_env)

    assert returncode not in (None, 0), log.read()[-2000:]
    assert log.contains(
        rf"schema sync of {re.escape(str(db))} failed and was rolled back: index ix_job_schedule_id already exists"
    )
    [backup] = e2e_env["data_dir"].glob("app.bck.*.db")
    assert log.contains(rf"backup: {re.escape(str(backup))}")
    assert _schema(db) == schema
    assert not log.contains("Runner logging configured")
