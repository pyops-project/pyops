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
            
            workspace_path = tmp_path / f"data/jobs/testuser/default/{job_id}"
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
    def test_job_list_owner_filter(self, mock_client, mock_db, state, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_same_id = str(uuid.uuid4().hex)
            job_same = Job(id=job_same_id, owner="testuser", set="default", name="same", kind="task", state=state, param="{}")
            job_diff_id = str(uuid.uuid4().hex)
            job_diff = Job(id=job_diff_id, owner="other", set="default", name="diff", kind="task", state=state, param="{}")
            session.add_all([job_same, job_diff])
            session.commit()

        response = mock_client.get("/scriptlet/job/list", headers={"X-User": "testuser"})
        data = response.json()
        assert len(data) == 1
        assert data[0]["owner"] == "testuser"
        assert data[0]["state"] == state

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
        workspace = tmp_path / f"data/jobs/testuser/default/{job_id}"
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

    @pytest.mark.parametrize("state", ["todo", "cancelled", "killed", "failed", "done"])
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

    @pytest.mark.parametrize("state", ["dequeued", "running", "cancel", "remove", "removed"])
    def test_delete_job_item_invalid_states(self, mock_client, mock_db, state, test_temp_dir, patch_tempfile, mock_datetime):
        with Session(get_db_engine()) as session:
            job_id = str(uuid.uuid4().hex)
            job = Job(id=job_id, owner="testuser", set="default", name="test", kind="task", state=state, param="{}")
            session.add(job)
            session.commit()

        response = mock_client.delete(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 400
        assert f"is in state '{state}' and cannot be deleted" in response.json()["detail"]
        assert "Allowed states: todo, cancelled, killed, failed, done" in response.json()["detail"]

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
            owner, set_, name, kind, param, code, overrides_str = loadJob(job_id)
            assert kind == "debug"
            assert code == "# overridden code"
            assert overrides_str is not None
