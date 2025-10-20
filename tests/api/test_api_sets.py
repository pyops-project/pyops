import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.web import app
import subprocess

client = TestClient(app)

def test_get_sets_list():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.list_sets') as mock_list:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_list.return_value = [{"name": "testset", "branch": "main"}]
        response = client.get("/scriptlet/sets/list")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert len(data["sets"]) == 1
        assert data["sets"][0]["name"] == "testset"

def test_post_clone_success():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.clone_set') as mock_clone:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_clone.return_value = True
        data = {"name": "newset", "url": "https://github.com/example/repo.git", "branch": "main"}
        response = client.post("/scriptlet/sets/clone", json=data)
        assert response.status_code == 200
        data_resp = response.json()
        assert data_resp["status"] == "ok"
        assert "Cloned set 'newset'" in data_resp["message"]

def test_post_clone_value_error():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.clone_set') as mock_clone:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_clone.side_effect = ValueError("Invalid name")
        data = {"name": "", "url": "https://github.com/example/repo.git"}
        response = client.post("/scriptlet/sets/clone", json=data)
        assert response.status_code == 400
        assert "Invalid name" in response.json()["detail"]

def test_post_clone_exception():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.clone_set') as mock_clone:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_clone.side_effect = Exception("Clone failed")
        data = {"name": "newset", "url": "https://github.com/example/repo.git"}
        response = client.post("/scriptlet/sets/clone", json=data)
        assert response.status_code == 500
        assert "Clone failed" in response.json()["detail"]

def test_delete_remove_success():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.remove_set') as mock_remove:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_remove.return_value = True
        response = client.delete("/scriptlet/sets/remove/testset")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "Removed set 'testset'" in data["message"]

def test_delete_remove_value_error():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.remove_set') as mock_remove:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_remove.side_effect = ValueError("Not found")
        response = client.delete("/scriptlet/sets/remove/missing")
        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

def test_delete_remove_exception():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.remove_set') as mock_remove:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_remove.side_effect = Exception("Remove failed")
        response = client.delete("/scriptlet/sets/remove/testset")
        assert response.status_code == 500
        assert "Remove failed" in response.json()["detail"]

def test_post_switch_success():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.switch_branch') as mock_switch:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_switch.return_value = True
        data = {"name": "testset", "branch": "develop"}
        response = client.post("/scriptlet/sets/switch", json=data)
        assert response.status_code == 200
        data_resp = response.json()
        assert data_resp["status"] == "ok"
        assert "Switched 'testset' to 'develop'" in data_resp["message"]

def test_post_switch_value_error():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.switch_branch') as mock_switch:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_switch.side_effect = ValueError("Not found")
        data = {"name": "missing", "branch": "develop"}
        response = client.post("/scriptlet/sets/switch", json=data)
        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

def test_post_switch_exception():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.routers.scriptlet.sets.sets.switch_branch') as mock_switch:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        mock_switch.side_effect = Exception("Switch failed")
        data = {"name": "testset", "branch": "develop"}
        response = client.post("/scriptlet/sets/switch", json=data)
        assert response.status_code == 500
        assert "Switch failed" in response.json()["detail"]

def test_post_switch_nonexistent_branch():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock_conf, \
         patch('app.lib.sets.switch_branch') as mock_switch:
        mock_conf.return_value = {"git": "/tmp/mock_git"}
        # Mock to raise CalledProcessError for non-existent branch
        mock_switch.side_effect = subprocess.CalledProcessError(1, ["git", "checkout", "nonexistent"])
        data = {"name": "testset", "branch": "nonexistent"}
        response = client.post("/scriptlet/sets/switch", json=data)
        assert response.status_code == 500
        assert "nonexistent" in response.json()["detail"]  # Or check for git error message
