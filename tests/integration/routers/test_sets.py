import pytest
from fastapi.testclient import TestClient
from app.web import app
from unittest.mock import patch, MagicMock

client = TestClient(app)

@pytest.fixture
def mock_conf_data():
    with patch('app.routers.scriptlet.sets.files.conf_data') as mock:
        mock.return_value = {"git": "/tmp/mock_git"}
        yield mock

@pytest.fixture
def mock_sets():
    mock = MagicMock()
    with patch('app.routers.scriptlet.sets.sets', mock):
        yield mock

def test_list_sets(mock_conf_data, mock_sets):
    mock_sets.list_sets.return_value = [{"name": "testset", "branch": "main"}]
    response = client.get("/scriptlet/sets/list")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["sets"]) == 1
    assert data["sets"][0]["name"] == "testset"

def test_clone_set_success(mock_conf_data, mock_sets):
    mock_sets.clone_set.return_value = "repo"
    data = {"url": "https://github.com/example/repo.git"}
    response = client.post("/scriptlet/sets/clone", json=data)
    assert response.status_code == 200
    data_resp = response.json()
    assert data_resp["status"] == "ok"
    assert "Cloned set 'repo'" in data_resp["message"]

def test_clone_set_invalid(mock_conf_data, mock_sets):
    mock_sets.clone_set.side_effect = ValueError("Invalid")
    data = {"url": ""}
    response = client.post("/scriptlet/sets/clone", json=data)
    assert response.status_code == 400

def test_create_set_success(mock_conf_data, mock_sets):
    mock_sets.create_set.return_value = True
    data = {"name": "newset"}
    response = client.post("/scriptlet/sets/create", json=data)
    assert response.status_code == 200
    data_resp = response.json()
    assert data_resp["status"] == "ok"
    assert "Created set 'newset'" in data_resp["message"]

def test_create_set_invalid(mock_conf_data, mock_sets):
    mock_sets.create_set.side_effect = ValueError("Already exists")
    data = {"name": "existing"}
    response = client.post("/scriptlet/sets/create", json=data)
    assert response.status_code == 400

def test_create_set_schedules_venv_bootstrap_after_create(mock_conf_data, mock_sets):
    """Regression: the venv bootstrap (pip install over devpi, 5+ s) must run
    as a BackgroundTask after the HTTP response is formed, not inline. Order
    matters: create_set must complete before ensure_set_environment so the
    git repo exists when bootstrap starts.
    """
    call_order = []
    mock_sets.create_set.side_effect = lambda *a, **kw: call_order.append("create_set") or True
    mock_sets.ensure_set_environment.side_effect = lambda *a, **kw: call_order.append("ensure_set_environment")
    response = client.post("/scriptlet/sets/create", json={"name": "bgtest"})
    assert response.status_code == 200
    mock_sets.create_set.assert_called_once_with("/tmp/mock_git", "bgtest")
    mock_sets.ensure_set_environment.assert_called_once_with("bgtest")
    assert call_order == ["create_set", "ensure_set_environment"]

def test_clone_set_schedules_venv_bootstrap_after_clone(mock_conf_data, mock_sets):
    """Same regression as above for the clone endpoint."""
    call_order = []
    mock_sets.clone_set.side_effect = lambda *a, **kw: call_order.append("clone_set") or "repo"
    mock_sets.ensure_set_environment.side_effect = lambda *a, **kw: call_order.append("ensure_set_environment")
    response = client.post("/scriptlet/sets/clone", json={"url": "https://example/repo.git"})
    assert response.status_code == 200
    mock_sets.ensure_set_environment.assert_called_once_with("repo")
    assert call_order == ["clone_set", "ensure_set_environment"]

def test_remove_set(mock_conf_data, mock_sets):
    mock_sets.remove_set.return_value = None
    response = client.delete("/scriptlet/sets/remove/testset")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_remove_set_not_found(mock_conf_data, mock_sets):
    mock_sets.remove_set.side_effect = ValueError("Not found")
    response = client.delete("/scriptlet/sets/remove/missing")
    assert response.status_code == 404

def test_switch_branch(mock_conf_data, mock_sets):
    mock_sets.switch_branch.return_value = None
    data = {"name": "testset", "branch": "develop"}
    response = client.post("/scriptlet/sets/switch", json=data)
    assert response.status_code == 200
    data_resp = response.json()
    assert data_resp["status"] == "ok"

def test_get_sets_html():
    with patch('app.routers.scriptlet.sets.sites.get', return_value='<html></html>'):
        response = client.get("/scriptlet/sets")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

def test_get_sets_exception():
    with patch('app.routers.scriptlet.sets.sites.get', side_effect=Exception("site error")):
        response = client.get("/scriptlet/sets")
        assert response.status_code == 500
        assert "site error" in response.json()["detail"]

def test_pull_set(mock_conf_data, mock_sets):
    mock_sets.pull_set.return_value = None
    response = client.post("/scriptlet/sets/pull/testset")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_pull_set_not_found(mock_conf_data, mock_sets):
    mock_sets.pull_set.side_effect = ValueError("Not found")
    response = client.post("/scriptlet/sets/pull/missing")
    assert response.status_code == 404
