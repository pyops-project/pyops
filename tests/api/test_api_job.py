import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from sqlalchemy.orm import Session

@pytest.fixture
def mock_enqueue(mock_db):
    with patch('app.lib.runner.enqueue') as mock:
        def enqueue_side_effect(set_, name, owner, kind, drop_done, param, max_retries=0, overrides=None):
            # Mock DB insert and return ID
            from app.orm.job import Job
            from app.orm.database import get_db_engine
            job = Job(
                owner=owner,
                set=set_,
                name=name,
                kind=kind,
                state="todo",
                drop_done=drop_done,
                param=param,
                max_retries=max_retries,
                overrides=overrides
            )
            job_id = job.id = f"{owner}_{set_}_{name}_{int(datetime.now().timestamp())}"
            with Session(get_db_engine()) as session:
                session.add(job)
                session.flush()
                session.commit()
            return job_id
        mock.side_effect = enqueue_side_effect
        yield mock


@pytest.mark.parametrize("kind", ["task"])
def test_put_job_item_enqueue(mock_client, mock_db, mock_git_ops, mock_enqueue, kind, test_temp_dir, patch_tempfile, mock_datetime):
    # Test PUT /scriptlet/job/item to enqueue
    unique_name = f"testtask_{kind}"
    with patch("subprocess.check_output", return_value=b"pass\n"):  # Mock git show
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": unique_name,
                "drop_done": False,
                "param": '{"key": "value"}',
                "kind": kind
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        job_id = data["job"]
        assert isinstance(job_id, str)
        assert mock_enqueue.called

@pytest.mark.parametrize("kind", ["debug"])
def test_put_job_item_enqueue_debug(mock_client, mock_db, mock_git_ops, mock_enqueue, kind, test_temp_dir, patch_tempfile, mock_datetime):
    # Test PUT /scriptlet/job/item to enqueue debug with overrides
    unique_name = f"testtask_{kind}"
    with patch("subprocess.check_output", return_value=b"pass\n"):  # Mock git show
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": unique_name,
                "drop_done": False,
                "param": '{"key": "value"}',
                "kind": kind,
                "overrides": {"testset::override": "def override(): pass"}
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        job_id = data["job"]
        assert isinstance(job_id, str)
        assert mock_enqueue.called
        # Verify overrides_json passed
        args, kwargs = mock_enqueue.call_args
        assert args[7] == '{"testset::override": "def override(): pass"}'

def test_put_job_item_enqueue_debug_invalid_overrides(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test PUT /scriptlet/job/item debug with invalid override key
    with patch("subprocess.check_output", return_value=b"pass\n"):
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": "invalid",
                "drop_done": False,
                "param": "{}",
                "kind": "debug",
                "overrides": {"invalid_key": "code"}
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 400
        assert "Invalid override key" in response.json()["detail"]

def test_put_job_item_enqueue_invalid_param(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Invalid param (non-JSON)
    with patch("subprocess.check_output", return_value=b"pass\n"):
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": "invalid",
                "drop_done": False,
                "param": "not_json",  # Invalid JSON
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200  # No validation error on param str


@pytest.mark.parametrize("state", ["todo", "running", "done"])
def test_get_job_list(mock_client, mock_db, mock_git_ops, mock_enqueue, state, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/job/list?state=...
    # First enqueue a job
    unique_name = f"listtask_{state}"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "listset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200
        job_id = enqueue_resp.json()["job"]
    
    response = mock_client.get(f"/scriptlet/job/list?state={state}", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1  # At least the enqueued job
    assert any(j["id"] == job_id for j in data)
    assert "id" in data[0]

def test_get_job_list_filter_states(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    # Test filtering by multiple states (comma-separated)
    unique_name = "filtertask_filter"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        mock_client.put("/scriptlet/job/item", json={"set": "filterset", "name": unique_name, "drop_done": False, "param": "{}", "kind": "task"}, headers={"X-User": "testuser"})
    
    response = mock_client.get("/scriptlet/job/list?state=todo,done", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()
    # Filtered OK, may be empty if no matching states


def test_get_job_item(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    # Enqueue then get
    unique_name = "itemtask_get"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "itemset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        job_id = enqueue_resp.json()["job"]
    
    response = mock_client.get(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_id
    assert data["set"] == "itemset"

def test_get_job_item_not_found(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    response = mock_client.get("/scriptlet/job/item?id=999", headers={"X-User": "testuser"})
    assert response.status_code == 404


def test_post_job_cancel(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    # Enqueue then cancel
    unique_name = "canceltask_cancel"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "cancelset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        job_id = enqueue_resp.json()["job"]
    
    response = mock_client.get(f"/scriptlet/job/cancel?id={job_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200
    # Verify state change (mocked)

def test_post_job_cancel_not_found(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    response = mock_client.get("/scriptlet/job/cancel?id=999", headers={"X-User": "testuser"})
    assert response.status_code == 404


def test_get_job_download(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    # Enqueue then download (mock workspace)
    with patch("subprocess.check_output", return_value=b"pass\n"), \
         patch('app.lib.runner.drop', return_value=None), \
         patch('app.routers.scriptlet.job.ZipStream') as mock_zipstream:
        mock_finalize = MagicMock()
        mock_finalize.return_value = iter([b"mock_zip_data"])
        mock_zipstream.return_value.finalize = mock_finalize
        mock_zipstream.return_value.add_path.return_value = None
        unique_name = "downloadtask_download"
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "downloadset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        job_id = enqueue_resp.json()["job"]
        # The job ran: its workspace exists (a job without one answers 404).
        import os
        from app.lib import files
        os.makedirs(os.path.join(files.conf_data()["jobs"], "downloadset", job_id))
        response = mock_client.get(f"/scriptlet/job/download?id={job_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/x-zip-compressed"

def test_post_job_download_not_found(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    response = mock_client.get("/scriptlet/job/download?id=999", headers={"X-User": "testuser"})
    assert response.status_code == 404


def test_delete_job_item(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    # Enqueue then set to done and delete
    unique_name = "deletetask_delete"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "deleteset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "kind": "task"
            },
            headers={"X-User": "testuser"}
        )
        job_id = enqueue_resp.json()["job"]

    # Set job state to 'done' to allow deletion
    from app.orm.job import Job
    from sqlalchemy.orm import Session
    from app.orm.database import get_db_engine
    with Session(get_db_engine()) as session:
        job = session.get(Job, job_id)
        job.state = "done"
        session.commit()

    response = mock_client.delete(f"/scriptlet/job/item?id={job_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200

def test_job_unauthorized(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # No auth
    response = mock_client.get("/scriptlet/job/list")
    assert response.status_code == 200


@pytest.mark.parametrize("existing_state", ["dequeued", "running"])
def test_debug_job_cancellation_sequencing(mock_client, mock_db, mock_git_ops, existing_state, test_temp_dir, patch_tempfile, mock_datetime):
    # Regression test for debug job sequencing issues
    # Test that enqueuing a debug job cancels existing debug jobs properly

    from app.orm.job import Job
    from sqlalchemy.orm import Session
    from app.orm.database import get_db_engine

    # Create existing debug job in specified state
    existing_job_id = "existing_debug_job"
    with Session(get_db_engine()) as session:
        existing_job = Job(
            id=existing_job_id,
            owner="testuser",
            set="testset",
            name="existing",
            kind="debug",
            state=existing_state,
            drop_done=False,
            param="{}",
            overrides=None
        )
        session.add(existing_job)
        session.commit()

    # Enqueue new debug job (use real enqueue logic, not mocked)
    with patch("subprocess.check_output", return_value=b"pass\n"):
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": "newdebug",
                "drop_done": False,
                "param": '{"key": "value"}',
                "kind": "debug"
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        new_job_id = data["job"]
        assert isinstance(new_job_id, str)

    # Verify existing job was marked for cancellation
    with Session(get_db_engine()) as session:
        existing = session.get(Job, existing_job_id)
        assert existing is not None
        assert existing.state == "cancel"
        assert existing.detail == "Cancelled by new debug job start for testuser"

    # Verify new job was created
    with Session(get_db_engine()) as session:
        new_job = session.get(Job, new_job_id)
        assert new_job is not None
        assert new_job.state == "todo"
        assert new_job.kind == "debug"


def test_put_job_item_debug_missing_with_override(mock_client, mock_db, mock_git_ops, mock_enqueue, test_temp_dir, patch_tempfile, mock_datetime):
    """Test debug enqueue succeeds despite missing remote scriptlet if overridden."""
    with patch("app.lib.git.read_content") as mock_read_content:
        mock_read_content.side_effect = FileNotFoundError("missing")
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": "overridetask",
                "drop_done": False,
                "param": "{}",
                "kind": "debug",
                "overrides": {"testset::overridetask": "print('debug code')"}
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        assert mock_enqueue.called


def test_put_job_item_debug_missing_without_override(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    """Test debug enqueue fails if scriptlet missing remotely and not overridden."""
    with patch("app.lib.git.read_content") as mock_read_content:
        mock_read_content.side_effect = FileNotFoundError("missing")
        response = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": "missingtask",
                "drop_done": False,
                "param": "{}",
                "kind": "debug",
                "overrides": {"testset::other": "code"}  # missing testset::missingtask
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 404
        assert "Scriptlet testset::missingtask not existing." in response.json()["detail"]
