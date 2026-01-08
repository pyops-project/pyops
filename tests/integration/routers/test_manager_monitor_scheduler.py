import pytest
from fastapi.testclient import TestClient
from app.web import app
from app.orm.job import Job
from app.orm.schedule import Schedule
from sqlalchemy.orm import Session
from unittest.mock import patch

class TestUIRouters:
    def test_get_manager(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/manager", headers={"X-User": "testuser"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert "<html>" in response.text

    def test_get_manager_unauthorized(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/manager")
        assert response.status_code == 200  # Anonymous allowed

    def test_get_monitor(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/monitor", headers={"X-User": "testuser"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert "<html>" in response.text

    def test_get_monitor_unauthorized(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/monitor")
        assert response.status_code == 200  # Anonymous allowed

    def test_get_scheduler(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/scheduler", headers={"X-User": "testuser"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert "<html>" in response.text

    def test_get_scheduler_unauthorized(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/scheduler")
        assert response.status_code == 200  # Anonymous allowed

    def test_get_manager_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.sites.get", side_effect=Exception("site error")):
            response = mock_client.get("/scriptlet/manager")
            assert response.status_code == 500
            assert "site error" in response.json()["detail"]

    def test_get_monitor_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.sites.get", side_effect=Exception("site error")):
            response = mock_client.get("/scriptlet/monitor")
            assert response.status_code == 500
            assert "site error" in response.json()["detail"]

    def test_get_scheduler_exception(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.sites.get", side_effect=Exception("site error")):
            response = mock_client.get("/scriptlet/scheduler")
            assert response.status_code == 500
            assert "site error" in response.json()["detail"]
