import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.web import app

@pytest.fixture
def client():
    return TestClient(app)

def test_controller_proxy_path_backward_compat(client, mock_git_ops, mock_db):
    # Test backward compatibility: /q calls send path="/q"
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}

        response = client.get("/controller/testset/testname/q?cmd=foo", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/q"
        assert params["type"] == "GET"
        assert params["cmd"] == "foo"

def test_controller_proxy_path_subpath(client, mock_git_ops, mock_db):
    # Test subpath forwarding
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"received": "path"}

        # POST with JSON body
        json_data = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        response = client.post("/controller/testset/testname/v1/chat/completions", json=json_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/v1/chat/completions"
        assert params["type"] == "POST"
        assert params["data"] == json_data
        assert params["data_type"] == "json"

def test_controller_proxy_path_query_params(client, mock_git_ops, mock_db):
    # Test query params with subpath
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"ok": True}

        response = client.get("/controller/testset/testname/api/models?limit=10&after=gpt", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/api/models"
        assert params["type"] == "GET"
        assert params["limit"] == "10"
        assert params["after"] == "gpt"

def test_controller_proxy_path_empty_rest(client, mock_git_ops, mock_db):
    # Test empty rest (trailing /)
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"path": "/"}

        response = client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/"
        assert params["type"] == "GET"

def test_controller_proxy_path_put_delete(client, mock_git_ops, mock_db):
    # Test PUT and DELETE with subpath
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"method": "PUT"}

        # PUT
        response = client.put("/controller/testset/testname/items/123", json={"name": "test"}, headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/items/123"
        assert params["type"] == "PUT"
        assert params["data"] == {"name": "test"}
        assert params["data_type"] == "json"

        # DELETE
        mock_instance.request.reset_mock()
        response = client.delete("/controller/testset/testname/items/123", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/items/123"
        assert params["type"] == "DELETE"

def test_controller_proxy_path_error_no_service(client, mock_git_ops, mock_db):
    # Test error when no running service
    with patch("app.lib.pyops.ServiceClient", side_effect=Exception("No running service: testset testname")):
        response = client.get("/controller/testset/testname/v1/models", headers={"X-User": "testuser"})
        assert response.status_code == 200  # Returns {"error": ...}
        data = response.json()
        assert "error" in data
        assert "No running service" in data["error"]

def test_controller_proxy_path_exec_fail(client, mock_git_ops, mock_db):
    # Test exec failure in controller code
    with patch("builtins.exec", side_effect=SyntaxError("controller syntax error")):
        response = client.get("/controller/testset/testname/v1/models", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "controller syntax error" in data["error"]

def test_controller_proxy_preserves_query_path(client, mock_git_ops, mock_db):
    # Test backward compatibility: preserves ?path= if present
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"path_preserved": True}

        response = client.get("/controller/testset/testname/?path=/custom&cmd=foo", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/custom"
        assert params["cmd"] == "foo"
