import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session


def test_put_schedule_item_create(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    active = True  # Router always sets active=True
    # First enqueue job
    unique_name = "testtask_create"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "testset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200
        job_id = enqueue_resp.json()["job"]

    # Test PUT /scriptlet/schedule/item to create schedule
    with patch("subprocess.check_output", return_value=b"pass\n"):
        response = mock_client.put(
            "/scriptlet/schedule/item",
            json={
                "set": "testset",
                "name": unique_name,
                "drop_done": False,
                "schedule": "* * * * *",
                "param": '{"key": "value"}',
                "active": active
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "schedule" in data
        schedule_id = data["schedule"]
        assert isinstance(schedule_id, int)

    # Verify DB state
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = session.get(Schedule, schedule_id)
        assert sched is not None
        assert sched.set == "testset"
        assert sched.name == unique_name
        assert sched.owner == "testuser"
        assert sched.active == active
        assert sched.schedule == "* * * * *"
        assert sched.param == '{"key": "value"}'
        assert sched.drop_done == False

def test_put_schedule_item_create_invalid_cron(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Invalid cron
    with patch("subprocess.check_output", return_value=b"pass\n"):
        response = mock_client.put(
            "/scriptlet/schedule/item",
            json={
                "set": "testset",
                "name": "invalidcron",
                "drop_done": False,
                "schedule": "invalid * * * *",  # ValueError
                "param": "{}"
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 400  # Malformed cron is a client error, not 500
        assert "cron" in response.json()["detail"].lower()

def test_put_schedule_item_create_no_job(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # No corresponding job (git show fails)
    with patch("app.lib.git.read_content") as mock_read:
        mock_read.side_effect = FileNotFoundError
        response = mock_client.put(
            "/scriptlet/schedule/item",
            json={
                "set": "noset",
                "name": "nojob",
                "drop_done": False,
                "schedule": "* * * * *",
                "param": "{}"
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 404

def test_get_schedule_list(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/schedule/list
    # First enqueue job
    unique_name = "listtask_list"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "listset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200

    # Then create a schedule
    create_resp = mock_client.put(
        "/scriptlet/schedule/item",
        json={
            "set": "listset",
            "name": unique_name,
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}"
        },
        headers={"X-User": "testuser"}
    )
    assert create_resp.status_code == 200
    schedule_id = create_resp.json()["schedule"]

    response = mock_client.get("/scriptlet/schedule/list", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(s["id"] == schedule_id for s in data)
    assert "id" in data[0]
    assert data[0]["set"] == "listset"
    assert data[0]["name"] == unique_name
    assert data[0]["active"] == True

    # Verify owner via DB since not in response
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = session.get(Schedule, schedule_id)
        assert sched.owner == "testuser"
        assert sched.name == unique_name

def test_post_schedule_activate(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Create then activate
    # First enqueue job
    unique_name = "activatetask_activate"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "activateset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200

    # Then create schedule
    create_resp = mock_client.put(
        "/scriptlet/schedule/item",
        json={
            "set": "activateset",
            "name": unique_name,
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}",
            "active": True
        },
        headers={"X-User": "testuser"}
    )
    schedule_id = create_resp.json()["schedule"]

    response = mock_client.get(f"/scriptlet/schedule/activate?id={schedule_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200

    # Verify DB state
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = session.get(Schedule, schedule_id)
        assert sched.active == True
        assert sched.last is not None
        assert sched.name == unique_name

def test_post_schedule_deactivate(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Create active then deactivate
    # First enqueue job
    unique_name = "deactivatetask_deactivate"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "deactivateset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200

    # Then create schedule active
    create_resp = mock_client.put(
        "/scriptlet/schedule/item",
        json={
            "set": "deactivateset",
            "name": unique_name,
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}",
            "active": True  # Start active
        },
        headers={"X-User": "testuser"}
    )
    schedule_id = create_resp.json()["schedule"]

    response = mock_client.get(f"/scriptlet/schedule/deactivate?id={schedule_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200

    # Verify DB state
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = session.get(Schedule, schedule_id)
        assert sched.active == False
        assert sched.name == unique_name

def test_post_schedule_run(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Manual run
    # First enqueue job
    unique_name = "runtask_run"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "runset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200

    # Then create schedule
    create_resp = mock_client.put(
        "/scriptlet/schedule/item",
        json={
            "set": "runset",
            "name": unique_name,
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}"
        },
        headers={"X-User": "testuser"}
    )
    schedule_id = create_resp.json()["schedule"]

    with patch("app.lib.runner.enqueue") as mock_enqueue:
        mock_enqueue.return_value = "mock_job_id"
        response = mock_client.get(f"/scriptlet/schedule/run?id={schedule_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        assert data["job"] == "mock_job_id"
        assert mock_enqueue.called

def test_post_schedule_run_not_found(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    response = mock_client.get("/scriptlet/schedule/run?id=999", headers={"X-User": "testuser"})
    assert response.status_code == 404

def test_delete_schedule_item(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Create then delete
    # First enqueue job
    unique_name = "deletescheduletask_delete"
    with patch("subprocess.check_output", return_value=b"pass\n"):
        enqueue_resp = mock_client.put(
            "/scriptlet/job/item",
            json={
                "set": "deleteset",
                "name": unique_name,
                "drop_done": False,
                "param": "{}",
                "debug": False
            },
            headers={"X-User": "testuser"}
        )
        assert enqueue_resp.status_code == 200

    # Then create schedule
    create_resp = mock_client.put(
        "/scriptlet/schedule/item",
        json={
            "set": "deleteset",
            "name": unique_name,
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}"
        },
        headers={"X-User": "testuser"}
    )
    schedule_id = create_resp.json()["schedule"]

    response = mock_client.delete(f"/scriptlet/schedule/item?id={schedule_id}", headers={"X-User": "testuser"})
    assert response.status_code == 200

    # Verify DB state
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = session.get(Schedule, schedule_id)
        assert sched is None

def test_delete_schedule_item_not_found(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    response = mock_client.delete("/scriptlet/schedule/item/999", headers={"X-User": "testuser"})
    assert response.status_code == 404

def test_schedule_unauthorized(mock_client, mock_db, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # No auth
    response = mock_client.get("/scriptlet/schedule/list")
    assert response.status_code == 200

def test_schedule_create_parametrized(mock_client, mock_db, mock_git_ops, mock_croniter, test_temp_dir, patch_tempfile, mock_datetime):
    # Test with different cron patterns
    crons = ["* * * * *", "0 0 * * *", "*/5 * * * *"]
    for cron in crons:
        name = f"param_{cron.replace(' ', '_')}"
        # First enqueue job
        with patch("subprocess.check_output", return_value=b"pass\n"):
            enqueue_resp = mock_client.put(
                "/scriptlet/job/item",
                json={
                    "set": "paramset",
                    "name": name,
                    "drop_done": False,
                    "param": "{}",
                    "debug": False
                },
                headers={"X-User": "testuser"}
            )
            assert enqueue_resp.status_code == 200

        # Then create schedule
        with patch("subprocess.check_output", return_value=b"pass\n"):
            response = mock_client.put(
                "/scriptlet/schedule/item",
                json={
                    "set": "paramset",
                    "name": name,
                    "drop_done": False,
                    "schedule": cron,
                    "param": '{"test": "param"}'
                },
                headers={"X-User": "testuser"}
            )
            assert response.status_code == 200

        # Verify DB state for each
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        with Session(get_db_engine()) as session:
            sched = session.query(Schedule).filter(Schedule.name == name).first()
            assert sched is not None
            assert sched.schedule == cron
            assert sched.param == '{"test": "param"}'
            assert sched.set == "paramset"
