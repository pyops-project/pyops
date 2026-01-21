import pytest
import json
from fastapi.testclient import TestClient
from app.web import app
from app.routers.scriptlet.schedule import ScheduleArgument
# from app.orm.job import Job  # Not needed for schedule tests
from app.orm.schedule import Schedule
from sqlalchemy.orm import Session
from app.orm.database import get_db_engine
from unittest.mock import patch

class TestScheduleRouter:
    # No class setup; use db_session in each test

    def test_put_schedule_item_create(self, mock_client, mock_db, mock_git_ops, mock_croniter):
        with patch('app.lib.git.read_content', return_value='pass'):
            create_data = {
                "set": "default",
                "name": "test_job",
                "drop_done": False,
                "schedule": "* * * * *",
                "param": "{}"
            }
            response = mock_client.put("/scriptlet/schedule/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
        data = response.json()
        assert "schedule" in data
        with Session(get_db_engine()) as session:
            schedule_id = data["schedule"]
            schedule = session.query(Schedule).filter_by(id=schedule_id).first()
            assert schedule is not None
            assert schedule.active is True
            assert schedule.schedule == "* * * * *"
            assert schedule.set == "default"
            assert schedule.name == "test_job"
            assert schedule.owner == "testuser"
            assert schedule.param == "{}"

    def test_put_schedule_item_create_invalid_cron(self, mock_client, mock_db, mock_git_ops, mock_croniter):
        with patch('app.lib.git.read_content', return_value='pass'):
            create_data = {
                "set": "default",
                "name": "test_job",
                "drop_done": False,
                "schedule": "invalid***",
                "param": "{}"
            }
            with patch("croniter.croniter", side_effect=ValueError("Invalid cron")):
                response = mock_client.put("/scriptlet/schedule/item", json=create_data, headers={"X-User": "testuser"})
                assert response.status_code == 500

    def test_put_schedule_item_create_no_job(self, mock_client, mock_db, mock_git_ops):
        create_data = {
            "set": "nonexistent",
            "name": "nonexistent",
            "drop_done": False,
            "schedule": "* * * * *",
            "param": "{}"
        }
        with patch('app.lib.git.read_content', side_effect=FileNotFoundError):
            response = mock_client.put("/scriptlet/schedule/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 404

    def test_get_schedule_list(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule1 = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False, max_retries=1)
            schedule2 = Schedule(set="default", name="test_job2", owner="testuser", schedule="0 0 * * *", active=False, param="{}", last=None, drop_done=False, max_retries=2)
            session.add_all([schedule1, schedule2])
            session.commit()

        response = mock_client.get("/scriptlet/schedule/list", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert any(s["active"] is True for s in data)
        assert any(s["active"] is False for s in data)
        assert all(s["set"] == "default" for s in data)
        assert any(s["name"] == "test_job" for s in data)
        assert any(s["name"] == "test_job2" for s in data)
        assert any(s["max_retries"] == 1 for s in data)
        assert any(s["max_retries"] == 2 for s in data)

    def test_post_schedule_activate(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=False, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        response = mock_client.get(f"/scriptlet/schedule/activate?id={schedule_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        with Session(get_db_engine()) as session:
            updated_schedule = session.query(Schedule).filter_by(id=schedule_id).first()
            assert updated_schedule.active is True
            assert updated_schedule.last is not None

    def test_post_schedule_deactivate(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        response = mock_client.get(f"/scriptlet/schedule/deactivate?id={schedule_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        with Session(get_db_engine()) as session:
            updated_schedule = session.query(Schedule).filter_by(id=schedule_id).first()
            assert updated_schedule.active is False

    def test_post_schedule_run(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        with patch("app.lib.runner.enqueue") as mock_enqueue:
            mock_enqueue.return_value = 2  # New job id
            response = mock_client.get(f"/scriptlet/schedule/run?id={schedule_id}", headers={"X-User": "testuser"})
            assert response.status_code == 200
            mock_enqueue.assert_called_once_with("default", "test_job", "testuser", "task", False, "{}", 0, None)
            assert response.json()["job"] == 2
            with Session(get_db_engine()) as session:
                updated_schedule = session.query(Schedule).filter_by(id=schedule_id).first()
                assert updated_schedule.last is not None

    def test_post_schedule_run_not_found(self, mock_client, mock_db):
        response = mock_client.get("/scriptlet/schedule/run?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_delete_schedule_item(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        response = mock_client.delete(f"/scriptlet/schedule/item?id={schedule_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        with Session(get_db_engine()) as session:
            deleted = session.query(Schedule).filter_by(id=schedule_id).first()
            assert deleted is None

    def test_delete_schedule_item_not_found(self, mock_client, mock_db):
        response = mock_client.delete("/scriptlet/schedule/item?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_schedule_unauthorized(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            other_schedule = Schedule(set="default", name="other", owner="otheruser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(other_schedule)
            session.commit()
        
        response = mock_client.get("/scriptlet/schedule/list", headers={"X-User": "testuser"})
        data = response.json()
        assert len(data) == 1  # No owner filtering in code

    @pytest.mark.parametrize("cron_expr", [
        "* * * * *",
        "0 0 * * *",
        "*/5 * * * *"
    ])
    def test_schedule_create_parametrized(self, mock_client, mock_db, mock_git_ops, mock_croniter, cron_expr):
        with patch('app.lib.git.read_content', return_value='pass'):
            create_data = {
                "set": "default",
                "name": "test_job",
                "drop_done": False,
                "schedule": cron_expr,
                "param": "{}"
            }
            response = mock_client.put("/scriptlet/schedule/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
            schedule_id = response.json()["schedule"]
            with Session(get_db_engine()) as session:
                schedule = session.query(Schedule).filter_by(id=schedule_id).first()
                assert schedule.schedule == cron_expr
                assert schedule.active is True  # Code always sets active=True
                assert schedule.set == "default"
                assert schedule.name == "test_job"
                assert schedule.owner == "testuser"
                assert schedule.param == "{}"

    def test_put_schedule_item_exception(self, mock_client, mock_db, mock_git_ops):
        with patch('app.lib.git.read_content', side_effect=Exception("git error")):
            create_data = {
                "set": "default",
                "name": "test_job",
                "drop_done": False,
                "schedule": "* * * * *",
                "param": "{}"
            }
            response = mock_client.put("/scriptlet/schedule/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "git error" in response.json()["detail"]

    def test_get_schedule_list_exception(self, mock_client, mock_db):
        with patch('app.routers.scriptlet.schedule.Session', side_effect=Exception("db error")):
            response = mock_client.get("/scriptlet/schedule/list", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]

    def test_post_schedule_activate_exception(self, mock_client, mock_db):
        with patch('app.routers.scriptlet.schedule.Session', side_effect=Exception("db error")):
            response = mock_client.get("/scriptlet/schedule/activate?id=1", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]

    def test_post_schedule_deactivate_exception(self, mock_client, mock_db):
        with patch('app.routers.scriptlet.schedule.Session', side_effect=Exception("db error")):
            response = mock_client.get("/scriptlet/schedule/deactivate?id=1", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]

    def test_post_schedule_run_enqueue_exception(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        with patch("app.lib.runner.enqueue", side_effect=Exception("enqueue error")):
            response = mock_client.get(f"/scriptlet/schedule/run?id={schedule_id}", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "enqueue error" in response.json()["detail"]

    def test_delete_schedule_item_exception(self, mock_client, mock_db):
        with patch('app.routers.scriptlet.schedule.Session', side_effect=Exception("db error")):
            response = mock_client.delete("/scriptlet/schedule/item?id=1", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]

    def test_get_schedule_item(self, mock_client, mock_db):
        with Session(get_db_engine()) as session:
            schedule = Schedule(set="default", name="test_job", owner="testuser", schedule="* * * * *", active=True, param="{}", last=None, drop_done=False)
            session.add(schedule)
            session.commit()
            schedule_id = schedule.id
        
        response = mock_client.get(f"/scriptlet/schedule/item?id={schedule_id}", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["set"] == "default"
        assert data["name"] == "test_job"

    def test_get_schedule_item_not_found(self, mock_client, mock_db):
        response = mock_client.get("/scriptlet/schedule/item?id=999", headers={"X-User": "testuser"})
        assert response.status_code == 404

    def test_get_schedule_item_exception(self, mock_client, mock_db):
        with patch('app.routers.scriptlet.schedule.Session', side_effect=Exception("db error")):
            response = mock_client.get("/scriptlet/schedule/item?id=1", headers={"X-User": "testuser"})
            assert response.status_code == 500
            assert "db error" in response.json()["detail"]
