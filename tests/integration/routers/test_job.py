import pytest
import json
import os
import uuid
from app.lib import git
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.web import app
from app.orm.database import get_db_engine
from app.routers.scriptlet.job import RunArgument
from app.orm.job import Job
from unittest.mock import patch, MagicMock, Mock
from pathlib import Path

class TestJobRouter:
    def test_put_job_item_enqueue(self, mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        run_data = {
            "set": "default",
            "name": "test_task",
            "drop_done": False,
            "param": json.dumps({"key": "value"}),
            "debug": False
        }
        response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        job_id = data["job"]
        with Session(get_db_engine()) as session:
            job = session.query(Job).filter_by(id=job_id).first()
            assert job is not None
            assert job.state == "todo"
            assert job.param == run_data["param"]
            assert job.owner
            assert job.schedule_id is None

    def test_put_job_item_enqueue_invalid_param(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        run_data = {"name": "test", "drop_done": False, "param": "{}", "debug": False}  # Missing 'set'
        response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
        assert response.status_code == 422  # Pydantic validation error for missing 'set'

    def test_get_job_list(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            # Create sample jobs
            job1 = Job(id=str(uuid.uuid4().hex), owner="testuser", set="default", name="task1", kind="task", state="todo", param="{}")
            job2 = Job(id=str(uuid.uuid4().hex), owner="testuser", set="default", name="task2", kind="task", state="running", param="{}")
            session.add_all([job1, job2])
            session.commit()

        response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert any(j["state"] == "todo" for j in data)
        assert any(j["state"] == "running" for j in data)

    def test_get_job_list_includes_set_and_name(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        """Regression: `/scriptlet/job/list` must include the `set` and
        `name` columns. Pre-3.3.0 the endpoint omitted both, so
        `pyopsctl job list --set <name>` (and the table's set/name
        columns) silently rendered empty even when matching jobs
        existed."""
        with Session(get_db_engine()) as session:
            job1 = Job(id=str(uuid.uuid4().hex), owner="testuser", set="cami", name="causal_service", kind="service", state="running", param="{}")
            job2 = Job(id=str(uuid.uuid4().hex), owner="testuser", set="myset", name="other", kind="task", state="done", param="{}")
            session.add_all([job1, job2])
            session.commit()

        response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        cami_rows = [j for j in data if j.get("set") == "cami"]
        assert len(cami_rows) == 1, f"--set cami filter must hit; got {data!r}"
        assert cami_rows[0]["name"] == "causal_service"
        myset_rows = [j for j in data if j.get("name") == "other"]
        assert len(myset_rows) == 1
        assert myset_rows[0]["set"] == "myset"

    def test_get_job_list_filter_states(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_todo = Job(id=str(uuid.uuid4().hex), owner="testuser", set="default", name="todo", kind="task", state="todo", param="{}")
            job_done = Job(id=str(uuid.uuid4().hex), owner="testuser", set="default", name="done", kind="task", state="done", param="{}")
            session.add_all([job_todo, job_done])
            session.commit()
        
        response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert any(j["state"] == "todo" for j in data)
        assert any(j["state"] == "done" for j in data)

    def test_get_job_item(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state="todo", param="{}", detail="None")
            session.add(job)
            session.commit()
        
        response = mock_client.get(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["state"] == "todo"

    def test_get_job_item_includes_full_metadata(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        """Regression: the detail endpoint `/scriptlet/job/item` must return
        `name`, `kind`, `param`, `start`, `end` — not just the
        `id/owner/set/state/detail/stdout/stderr` subset it used to. The
        list endpoint exposed these all along, so a *detail* view returning
        fewer fields than the list rendered `set/name` with an empty name
        and `kind`/`param` as null in `pyopsctl job view`, even though the
        job had been enqueued with them and the param had run."""
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="reporting", name="export_report",
                      kind="draft", state="done", param="customer = '000000_sysup'", detail=None)
            session.add(job)
            session.commit()

        response = mock_client.get(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "export_report"
        assert data["kind"] == "draft"
        assert data["param"] == "customer = '000000_sysup'"
        # start/end are present as keys even when unset (datetime|null).
        assert "start" in data
        assert "end" in data

    def test_get_job_item_not_found(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/job/item?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_post_job_cancel(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state="running", param="{}")
            session.add(job)
            session.commit()
        
        response = mock_client.get(f"/scriptlet/job/cancel?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        with Session(get_db_engine()) as session:
            updated_job = session.query(Job).filter_by(id=job_id).first()
            assert updated_job.state == "cancel"

    def test_post_job_cancel_not_found(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/job/cancel?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_post_job_download(self, mock_client, mock_db, tmp_path, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state="done", param="{}")
            session.add(job)
            session.commit()
            
            workspace_path = tmp_path / f"data/jobs/default/{job_id}"
            workspace_path.mkdir(parents=True)
            (workspace_path / "stdout.log").write_text("Test output")
            
            with patch("app.lib.runner.query") as mock_query:
                mock_query.return_value = job
                with patch("app.routers.scriptlet.job.ZipStream") as mock_zip:
                    mock_zip.return_value = MagicMock()
                    response = mock_client.get(f"/scriptlet/job/download?id={job_id}", headers={"X-User": "testuser"})
                    assert response.status_code == 200
                    assert "application/x-zip-compressed" in response.headers["content-type"]
                    mock_zip.assert_called_once()

    def test_post_job_download_not_found(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/job/download?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_delete_job_item(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state="done", param="{}")
            session.add(job)
            session.commit()
        
        response = mock_client.delete(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        # Response body is empty dict, but status confirms success
        
        # Verify job state is set to "remove" (soft delete)
        with Session(get_db_engine()) as session:
            updated_job = session.query(Job).filter_by(id=job_id).first()
            assert updated_job.state == "remove"

    def test_job_unauthorized(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="otheruser", set="default", name="test", kind="task", state="todo", param="{}")
            session.add(job)
            session.commit()
        
        response = mock_client.get(f"/scriptlet/job/item?id={job_id}")  # No X-User or mismatch
        assert response.status_code == 200  # No auth check in endpoint
        data = response.json()
        assert data["owner"] == "otheruser"

    @pytest.mark.parametrize("state", ["todo", "running", "done", "failed"])
    def test_job_list_returns_all_owners(self, mock_client, mock_db, state, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_same_id = str(uuid.uuid4().hex)
            job_same = Job(id=job_same_id, owner="testuser", set="default", name="same", kind="task", state=state, param="{}")
            job_diff_id = str(uuid.uuid4().hex)
            job_diff = Job(id=job_diff_id, owner="other", set="default", name="diff", kind="task", state=state, param="{}")
            session.add_all([job_same, job_diff])
            session.commit()

        response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
        data = response.json()
        owners = {j["owner"] for j in data}
        assert {"testuser", "other"} <= owners
        assert all(j["state"] == state for j in data if j["id"] in {job_same_id, job_diff_id})

    @patch("app.lib.git.read_content")
    @patch('app.runner.terminate_job_processes')
    def test_post_job_cancel_killed_path(self, mock_terminate, mock_read_content, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        from app.runner import postTerm
        # Mock git.read_content to return dummy code for enqueue
        mock_read_content.return_value = "print('dummy task code')"

        # Mock terminate_job_processes to simulate force kill and call postTerm
        def mock_terminate_func(job_id, pid, is_debug, owner):
            postTerm(job_id, killed=True)
        mock_terminate.side_effect = mock_terminate_func

        # Enqueue a debug job
        run_data = {
            "set": "default",
            "name": "test_long_running",
            "drop_done": False,
            "param": json.dumps({}),  # Empty param
            "debug": True  # Debug job
        }
        response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        job_id = response.json()["job"]

        # Manually set to running with PID (simulate daemon start)
        with Session(get_db_engine()) as session:
            job = session.query(Job).filter_by(id=job_id).first()
            job.state = "running"
            job.pid = 12345  # Mock PID
            job.kind = "debug"
            session.commit()

        # Mock lib_runner.cancel to set "cancel"
        with patch("app.lib.runner.cancel") as mock_cancel:
            mock_cancel.return_value = {"id": job_id, "state": "cancel"}

            # Call cancel API
            cancel_response = mock_client.get(f"/scriptlet/job/cancel?id={job_id}", headers={"X-User": "testuser"})
            assert cancel_response.status_code == 200

        # Simulate daemon starting termination thread
        mock_terminate(job_id, 12345, True, "testuser")

        # Verify state after termination
        with Session(get_db_engine()) as session:
            updated_job = session.query(Job).filter_by(id=job_id).first()
            assert updated_job.state == "cancelled"
            assert updated_job.detail is None
            assert updated_job.pid is None
            assert updated_job.end is not None

    @patch("app.lib.git.read_content")
    @patch('app.runner.terminate_job_processes')
    def test_post_job_cancel_graceful_path(self, mock_terminate, mock_read_content, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        from app.runner import postTerm
        # Mock git.read_content to return dummy code for enqueue
        mock_read_content.return_value = "print('dummy task code')"

        # Mock terminate_job_processes to simulate graceful termination and call postTerm
        def mock_terminate_func(job_id, pid, is_debug, owner):
            postTerm(job_id, killed=False)
        mock_terminate.side_effect = mock_terminate_func

        # Enqueue a debug job
        run_data = {
            "set": "default",
            "name": "test_long_running",
            "drop_done": False,
            "param": json.dumps({}),  # Empty param
            "debug": True  # Debug job
        }
        response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        job_id = response.json()["job"]

        # Manually set to running with PID (simulate daemon start)
        with Session(get_db_engine()) as session:
            job = session.query(Job).filter_by(id=job_id).first()
            job.state = "running"
            job.pid = 12345  # Mock PID
            job.kind = "debug"
            session.commit()

        # Mock lib_runner.cancel to set "cancel"
        with patch("app.lib.runner.cancel") as mock_cancel:
            mock_cancel.return_value = {"id": job_id, "state": "cancel"}

            # Call cancel API
            cancel_response = mock_client.get(f"/scriptlet/job/cancel?id={job_id}", headers={"X-User": "testuser"})
            assert cancel_response.status_code == 200

        # Simulate daemon starting termination thread
        mock_terminate(job_id, 12345, True, "testuser")

        # Verify state after termination
        with Session(get_db_engine()) as session:
            updated_job = session.query(Job).filter_by(id=job_id).first()
            assert updated_job.state == "cancelled"
            assert updated_job.detail is None
            assert updated_job.pid is None
            assert updated_job.end is not None

    def test_put_job_item_invalid_overrides_key(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        run_data = {
            "set": "default",
            "name": "test_task",
            "drop_done": False,
            "param": "{}",
            "kind": "debug",
            "overrides": {"invalid_key": "code"}
        }
        response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
        assert response.status_code == 400
        assert "Invalid override key" in response.json()["detail"]

    def test_put_job_item_scriptlet_not_existing(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.git.read_content", side_effect=FileNotFoundError):
            run_data = {
                "set": "default",
                "name": "nonexistent",
                "drop_done": False,
                "param": "{}"
            }
            response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
            assert response.status_code == 404
            assert "not existing" in response.json()["detail"]

    def test_put_job_item_debug_scriptlet_not_existing_with_override(self, mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """Regression: debug enqueue succeeds if override provided despite no remote scriptlet."""
        with patch("app.lib.git.read_content", side_effect=FileNotFoundError):
            response = mock_client.put(
                "/scriptlet/job/item",
                json={
                    "set": "default",
                    "name": "missingtask",
                    "drop_done": False,
                    "param": "{}",
                    "kind": "debug",
                    "overrides": {"default::missingtask": "# overridden code"}
                },
                headers={"X-User": "testuser"}
            )
            assert response.status_code == 200
            job_id = response.json()["job"]
            # Verify job created in DB
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter(Job.id == job_id).first()
                assert job is not None
                assert job.kind == "debug"
                assert job.state == "todo"

    def test_put_job_item_exception(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.git.get_repo_path", side_effect=Exception("repo error")):
            run_data = {
                "set": "default",
                "name": "test_task",
                "drop_done": False,
                "param": "{}"
            }
            response = mock_client.put("/scriptlet/job/item", json=run_data, headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "repo error" in response.json()["detail"]

    def test_get_job_item_with_logs(self, mock_client, mock_db, tmp_path, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state="done", param="{}", detail="test detail")
            session.add(job)
            session.commit()
        
        # Create log files
        workspace = tmp_path / f"data/jobs/default/{job_id}"
        workspace.mkdir(parents=True)
        (workspace / "stdout.log").write_text("stdout content")
        (workspace / "stderr.log").write_text("stderr content")
        
        with patch("app.lib.files.conf_data", return_value={"jobs": str(tmp_path / "data/jobs")}):
            response = mock_client.get(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
            assert response.status_code == 200
            data = response.json()
            assert data["stdout"] == "stdout content"
            assert data["stderr"] == "stderr content"

    def test_get_job_item_exception(self, mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.runner.query", side_effect=Exception("query error")):
            response = mock_client.get("/scriptlet/job/item?id=123", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "query error" in response.json()["detail"]

    def test_delete_job_item_not_found(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.runner.remove", return_value=None):
            response = mock_client.delete("/scriptlet/job/item?id=999", headers={"X-User": "testuser"})
            assert response.status_code == 404

    def test_delete_job_item_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        # Mock query to return a job in valid state
        mock_job = MagicMock()
        mock_job.state = "done"
        with patch("app.lib.runner.query", return_value=mock_job), \
             patch("app.lib.runner.remove", side_effect=Exception("remove error")):
            response = mock_client.delete("/scriptlet/job/item?id=123", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "remove error" in response.json()["detail"]

    @pytest.mark.parametrize("state", ["todo", "cancelled", "failed", "done"])
    def test_delete_job_item_allowed_states(self, mock_client, mock_db, state, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state=state, param="{}")
            session.add(job)
            session.commit()

        response = mock_client.delete(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200

        # Verify job state is set to "remove"
        with Session(get_db_engine()) as session:
            updated_job = session.query(Job).filter_by(id=job_id).first()
            assert updated_job.state == "remove"

    @pytest.mark.parametrize("state", ["killed", "dequeued", "running", "cancel", "remove", "removed"])
    def test_delete_job_item_invalid_states(self, mock_client, mock_db, state, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state=state, param="{}")
            session.add(job)
            session.commit()

        response = mock_client.delete(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 400
        assert f"is in state '{state}' and cannot be deleted" in response.json()["detail"]
        assert "Allowed states: todo, cancelled, failed, done" in response.json()["detail"]

    def test_get_job_cancel_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.runner.cancel", side_effect=Exception("cancel error")):
            response = mock_client.get("/scriptlet/job/cancel?id=123", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "cancel error" in response.json()["detail"]

    def test_get_job_download_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.runner.query", side_effect=Exception("query error")):
            response = mock_client.get("/scriptlet/job/download?id=123", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "query error" in response.json()["detail"]

    def test_get_job_list_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.routers.scriptlet.job.Session", side_effect=Exception("db error")):
            response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]

    def test_loadjob_debug_override_missing_file_success(self, mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """Regression: runner.loadJob succeeds for debug enqueue with override despite no remote scriptlet file."""
        with patch("app.lib.git.read_content") as mock_read_content, \
             patch("os.listdir") as mock_listdir:
            mock_read_content.side_effect = FileNotFoundError("missing")

            def listdir_side_effect(path):
                repo_path = git.get_repo_path("default")
                if repo_path == path:
                    return []
                try:
                    return os.listdir(path)
                except OSError:
                    return []

            mock_listdir.side_effect = listdir_side_effect

            response = mock_client.put(
                "/scriptlet/job/item",
                json={
                    "set": "default",
                    "name": "missingtask",
                    "drop_done": False,
                    "param": "{}",
                    "kind": "debug",
                    "overrides": {"default::missingtask": "# overridden code"}
                },
                headers={"X-User": "testuser"}
            )
            assert response.status_code == 200
            job_id = response.json()["job"]

            # Dequeue job for loadJob precondition
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter(Job.id == job_id).first()
                assert job is not None
                job.state = "dequeued"
                session.commit()

            from app.runner import loadJob
            owner, set_, name, kind, param, code, overrides_str, max_retries = loadJob(job_id)
            assert kind == "debug"
            assert code == "# overridden code"
            assert overrides_str is not None
            assert max_retries == 0

    def test_put_job_item_testdebug_accepted(self, mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """kind=testdebug is a valid Literal value and stored verbatim on the Job row.
        Mirrors the kind=debug acceptance path; the runner-side __name__=__test__
        and .test→.debug symlink semantics are exercised in unit/e2e tests."""
        with patch("app.lib.git.read_content", side_effect=FileNotFoundError):
            response = mock_client.put(
                "/scriptlet/job/item",
                json={
                    "set": "default",
                    "name": "missingtask",
                    "drop_done": False,
                    "param": "{}",
                    "kind": "testdebug",
                    "overrides": {"default::missingtask": "# overridden code"}
                },
                headers={"X-User": "testuser"}
            )
            assert response.status_code == 200
            job_id = response.json()["job"]
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter(Job.id == job_id).first()
                assert job is not None
                assert job.kind == "testdebug"
                assert job.state == "todo"

    def test_put_job_item_testdebug_invalid_overrides_key(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """Override-key regex applies to testdebug too — same validation pathway."""
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "default",
                "name": "test_task",
                "drop_done": False,
                "param": "{}",
                "kind": "testdebug",
                "overrides": {"invalid_key": "code"}
            },
            headers={"X-User": "testuser"},
        )
        assert response.status_code == 400
        assert "Invalid override key" in response.json()["detail"]

    def test_loadjob_testdebug_override_returns_unstaged(self, mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """loadJob reads override body for kind=testdebug, mirroring debug.
        Confirms `committed=False` and the override-dict fast-path both treat
        testdebug like the other preview/diagnostic kinds."""
        with patch("app.lib.git.read_content") as mock_read_content, \
             patch("os.listdir") as mock_listdir:
            mock_read_content.side_effect = FileNotFoundError("missing")

            def listdir_side_effect(path):
                repo_path = git.get_repo_path("default")
                if repo_path == path:
                    return []
                try:
                    return os.listdir(path)
                except OSError:
                    return []

            mock_listdir.side_effect = listdir_side_effect

            response = mock_client.put(
                "/scriptlet/job/item",
                json={
                    "set": "default",
                    "name": "missingtask",
                    "drop_done": False,
                    "param": "{}",
                    "kind": "testdebug",
                    "overrides": {"default::missingtask": "# overridden testdebug code"},
                },
                headers={"X-User": "testuser"},
            )
            assert response.status_code == 200
            job_id = response.json()["job"]

            with Session(get_db_engine()) as session:
                job = session.query(Job).filter(Job.id == job_id).first()
                assert job is not None
                job.state = "dequeued"
                session.commit()

            from app.runner import loadJob
            owner, set_, name, kind, param, code, overrides_str, max_retries = loadJob(job_id)
            assert kind == "testdebug"
            assert code == "# overridden testdebug code"
            assert overrides_str is not None
