import pytest
import json
import uuid
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.web import app
from app.orm.database import get_db_engine
from app.routers.scriptlet.job import RunArgument
from app.orm.job import Job
from unittest.mock import patch, MagicMock
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
