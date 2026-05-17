import pytest
from unittest.mock import patch, MagicMock, mock_open
from fastapi import HTTPException
from app.web import app
from app.routers.scriptlet.inventory import CreateArgument, SaveArgument

def test_get_inventory_list(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/inventory/list
    response = mock_client.get("/scriptlet/inventory/list", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert all("set" in item and "name" in item and "type" in item for item in data)

def test_get_inventory_sets(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/inventory/sets
    response = mock_client.get("/scriptlet/inventory/sets", headers={"X-User": "testuser"})
    assert response.status_code == 200
    data = response.json()["sets"]
    assert isinstance(data, list)
    assert len(data) > 0

@pytest.mark.parametrize("item_type", ["task", "view", "markdown"])
def test_put_inventory_item_create(mock_client, mock_git_ops, item_type, test_temp_dir, patch_tempfile, mock_datetime):
    # Test PUT /scriptlet/inventory/item with JSON body
    name = f"test_{item_type}" if item_type != "markdown" else "README"
    create_data = {
        "set": "testset",
        "name": name,
        "type": item_type
    }
    with patch("os.path.exists", return_value=True), \
         patch("app.lib.git.check_name_unique", return_value=True), \
         patch("os.makedirs"):
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["set"] == "testset"
        assert data["name"] == name
        assert data["type"] == item_type

def test_put_inventory_item_create_conflict(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # First create
    create_data = {"set": "testset", "name": "conflict", "type": "task"}
    with patch("os.path.exists", return_value=True), \
         patch("os.makedirs"):
        mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
    # Second attempt - conflict: create_scriptlet raises FileExistsError under the lock.
    with patch("os.path.exists", return_value=True), \
         patch("app.lib.git.create_scriptlet", side_effect=FileExistsError("Name 'conflict' already exists")):
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 409

@pytest.mark.parametrize("item_type", ["task", "view", "markdown"])
def test_post_inventory_item_save(mock_client, mock_git_ops, item_type, test_temp_dir, patch_tempfile, mock_datetime):
    # Test POST /scriptlet/inventory/item with JSON body
    name = f"save_{item_type}"
    create_data = {"set": "testset", "name": name, "type": item_type}
    save_data = {"set": "testset", "name": name, "code": f"# Saved {item_type} code"}
    expected_ext = ".py" if item_type == "task" else ".html" if item_type == "view" else ".md"
    
    # First create
    with patch("os.path.exists", return_value=True), \
         patch("app.lib.git.check_name_unique", return_value=True), \
         patch("os.makedirs"):
        mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
    
    # Then save
    with patch("os.listdir", return_value=[f"{name}{expected_ext}"]), \
         patch("os.path.exists", return_value=True):
        response = mock_client.post("/scriptlet/inventory/item", json=save_data, headers={"X-User": "testuser"})
        assert response.status_code == 200

def test_delete_inventory_item(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Create then delete
    create_data = {"set": "testset", "name": "delete", "type": "task"}
    with patch("os.path.exists", return_value=True), \
         patch("app.lib.git.check_name_unique", return_value=True), \
         patch("os.makedirs"):
        mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
    
    with patch("os.listdir", return_value=["delete.task.py"]), \
         patch("os.path.exists", return_value=True):
        response = mock_client.delete("/scriptlet/inventory/item?set=testset&name=delete", headers={"X-User": "testuser"})
        assert response.status_code == 200

def test_get_inventory_code(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Create then get code
    create_data = {"set": "testset", "name": "code", "type": "task"}
    save_data = {"set": "testset", "name": "code", "code": "# Sample code"}
    
    with patch("os.path.exists", return_value=True), \
         patch("app.lib.git.check_name_unique", return_value=True), \
         patch("os.makedirs"):
        mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
    
    with patch("os.path.exists", return_value=True), \
         patch("os.listdir", return_value=["code.task.py"]):
        mock_client.post("/scriptlet/inventory/item", json=save_data, headers={"X-User": "testuser"})
    
    with patch("os.listdir", return_value=["code.task.py"]):
        response = mock_client.get("/scriptlet/inventory/code?set=testset&name=code", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "code" in data
        assert data["code"] == "# Sample code"

def test_inventory_unauthorized(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # No X-User header
    response = mock_client.get("/scriptlet/inventory/list")
    assert response.status_code == 200  # Anonymous allowed
    data = response.json()
    assert isinstance(data, list)

def test_inventory_invalid_set(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    create_data = {"set": "nonexistent", "name": "test", "type": "task"}
    with patch("os.path.exists", return_value=False):
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 400

@pytest.mark.parametrize("method", ["post", "put"])
def test_controller_body_handling_json(mock_client, mock_controller_glob, method):
    # Valid JSON with application/json
    json_data = {"key": "value"}
    headers = {"X-User": "testuser"}
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}
        
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", json=json_data, headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", json=json_data, headers=headers)
        
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["data"] == json_data
        assert params["data_type"] == "json"
        assert params["type"] == method.upper()

@pytest.mark.parametrize("method", ["post", "put"])
def test_controller_body_handling_invalid_json(mock_client, mock_controller_glob, method):
    # Invalid JSON with application/json -> fallback to binary
    body = b"invalid json"
    headers = {"X-User": "testuser", "content-type": "application/json"}
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}
        
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", content=body, headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", content=body, headers=headers)
        
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert isinstance(params["data"], bytes)
        assert params["data"] == body
        assert params["data_type"] == "binary"
        assert params["type"] == method.upper()

@pytest.mark.parametrize("method", ["post", "put"])
def test_controller_body_handling_text(mock_client, mock_controller_glob, method):
    # Text with text/plain
    body = b"hello world"
    headers = {"X-User": "testuser", "content-type": "text/plain"}
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}
        
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", content=body, headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", content=body, headers=headers)
        
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["data"] == "hello world"
        assert params["data_type"] == "text"
        assert params["type"] == method.upper()

@pytest.mark.parametrize("method", ["post", "put"])
def test_controller_body_handling_binary(mock_client, mock_controller_glob, method):
    # Binary with application/octet-stream
    body = b'\x00\x01\x02'
    headers = {"X-User": "testuser", "content-type": "application/octet-stream"}
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}
        
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", content=body, headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", content=body, headers=headers)
        
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["data"] == body
        assert params["data_type"] == "binary"
        assert params["type"] == method.upper()

@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_controller_body_handling_empty(mock_client, mock_controller_glob, method):
    # Empty body
    headers = {"X-User": "testuser"}
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"success": True}
        
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", headers=headers)
        else:  # delete
            response = mock_client.delete("/controller/testset/testname/q", headers=headers)
        
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["data"] is None
        assert params["data_type"] == "none"
        assert params["type"] == method.upper()

def test_controller_get_success(mock_client, mock_git_ops):
    # Test /controller GET success
    response = mock_client.get("/controller/default/sample", headers={"X-User": "testuser"})
    assert response.status_code == 200
    assert "<html>" in response.text
    assert "default::Test Controller" in response.text  # From mock name

def test_controller_get_exec_fail(mock_client, mock_git_ops):
    # Test /controller GET with exec failure
    with patch("builtins.exec", side_effect=SyntaxError("exec error")):
        response = mock_client.get("/controller/default/sample", headers={"X-User": "testuser"})
        assert response.status_code == 200
        assert "Controller Draft Error" in response.text
        assert "failed to execute" in response.text

def test_menu_success(mock_client, mock_git_ops):
    # Test /menu success
    response = mock_client.get("/menu")
    assert response.status_code == 200
    data = response.json()
    assert "menu" in data
    assert "failures" in data
    assert isinstance(data["menu"], list)
    assert isinstance(data["failures"], list)
    assert len(data["failures"]) == 0

def test_menu_with_failures(mock_client, mock_git_ops):
    # Test /menu with exec failure
    def mock_exec(code, glob):
        raise SyntaxError("exec error")
    with patch("builtins.exec", side_effect=mock_exec):
        response = mock_client.get("/menu")
        assert response.status_code == 200
        data = response.json()
        assert "failures" in data
        assert len(data["failures"]) > 0
        failure = data["failures"][0]
        assert "set" in failure
        assert "name" in failure
        assert "error" in failure
        assert "exec error" in failure["error"]

def test_controller_q_exec_fail(mock_client, mock_git_ops):
    # Test /q with exec failure returns {"error"}
    with patch("builtins.exec", side_effect=SyntaxError("exec error")):
        response = mock_client.get("/controller/default/sample/q", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "execution failed" in data["error"]

def test_controller_q_http_exception(mock_client, mock_git_ops):
    # Test /q with HTTPException (e.g., missing file) raises as before
    with patch("app.web.get_controller_code", side_effect=HTTPException(status_code=404, detail="Not found")):
        response = mock_client.get("/controller/nonexistent/missing/q", headers={"X-User": "testuser"})
        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_controller_q_exec_fail_other_methods(mock_client, mock_controller_glob, method):
    # Test /q exec failure for POST/PUT/DELETE returns {"error"}
    with patch("builtins.exec", side_effect=SyntaxError("exec error")):
        headers = {"X-User": "testuser"}
        if method == "post":
            response = mock_client.post("/controller/testset/testname/q", headers=headers)
        elif method == "put":
            response = mock_client.put("/controller/testset/testname/q", headers=headers)
        else:  # delete
            response = mock_client.delete("/controller/testset/testname/q", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "execution failed" in data["error"]

def test_root_exception(mock_client):
    # Test root endpoint exception handling
    with patch("app.lib.sites.get", side_effect=Exception("site error")):
        response = mock_client.get("/")
        assert response.status_code == 500
        assert "site error" in response.json()["detail"]

def test_favicon_content_type(mock_client):
    with patch("app.lib.files.get", return_value=b"\x00\x00\x01\x00"):
        response = mock_client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/x-icon"

def test_favicon_exception(mock_client):
    # Test favicon endpoint exception handling
    with patch("app.lib.files.get", side_effect=Exception("file error")):
        response = mock_client.get("/favicon.ico")
        assert response.status_code == 500
        assert "file error" in response.json()["detail"]

@pytest.mark.parametrize("path,expected_type", [
    ("style.css", "text/css"),
    ("app.js", "text/javascript"),
    ("logo.png", "image/png"),
    ("data.json", "application/json"),
    ("unknown.bin", "application/octet-stream"),
])
def test_file_content_type(mock_client, path, expected_type):
    with patch("app.lib.files.get", return_value=b"content"):
        response = mock_client.get(f"/files/{path}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(expected_type)

def test_file_exception(mock_client):
    # Test file endpoint exception handling
    with patch("app.lib.files.get", side_effect=Exception("file error")):
        response = mock_client.get("/files/test.css")
        assert response.status_code == 500
        assert "file error" in response.json()["detail"]

def test_controller_get_controller_code_404(mock_client, mock_git_ops):
    # Test controller GET with missing controller file
    with patch("app.web.get_controller_code", side_effect=HTTPException(status_code=404, detail="Not found")):
        response = mock_client.get("/controller/testset/missing")
        assert response.status_code == 404

def test_controller_get_view_content_404(mock_client, mock_git_ops):
    # Test controller GET with missing view files
    with patch("app.web.get_view_content", side_effect=HTTPException(status_code=404, detail="Not found")):
        response = mock_client.get("/controller/testset/missing")
        assert response.status_code == 404

def test_controller_get_wrong_type_controller(mock_client, mock_git_ops):
    # Test controller GET with wrong type (not controller)
    with patch("app.web.get_controller_code", side_effect=HTTPException(status_code=405, detail="Not a controller")):
        response = mock_client.get("/controller/testset/wrongtype")
        assert response.status_code == 405

def test_controller_get_wrong_type_view(mock_client, mock_git_ops):
    # Test controller GET with wrong type view
    response = mock_client.get("/controller/testset/wrongtype")
    assert response.status_code == 405

def test_menu_exception(mock_client):
    # Test menu endpoint exception handling
    with patch("app.lib.git.list_scriptlets", side_effect=Exception("git error")):
        response = mock_client.get("/menu")
        assert response.status_code == 500
        assert "git error" in response.json()["detail"]

def test_inventory_list_exception(mock_client):
    # Test inventory list exception
    with patch("app.lib.git.list_scriptlets", side_effect=Exception("list error")):
        response = mock_client.get("/scriptlet/inventory/list", headers={"X-User": "testuser"})
        assert response.status_code == 500
        assert "list error" in response.json()["detail"]

def test_inventory_item_put_exception(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test item_put exception
    create_data = {"set": "testset", "name": "exception", "type": "task"}
    with patch("app.lib.git.get_git_base", side_effect=Exception("git base error")):
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 500
        assert "git base error" in response.json()["detail"]

def test_inventory_item_post_no_files(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test item_post with no files
    save_data = {"set": "testset", "name": "nofile", "code": "pass"}
    with patch("os.listdir", return_value=[]):
        response = mock_client.post("/scriptlet/inventory/item", json=save_data, headers={"X-User": "testuser"})
        assert response.status_code == 404
        assert "not existing" in response.json()["detail"]

def test_inventory_item_post_exception(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test item_post exception
    save_data = {"set": "testset", "name": "exception", "code": "pass"}
    with patch("os.listdir", return_value=["exception.task.py"]), \
         patch("app.lib.git.write_content", side_effect=Exception("write error")):
        response = mock_client.post("/scriptlet/inventory/item", json=save_data, headers={"X-User": "testuser"})
        assert response.status_code == 500
        assert "write error" in response.json()["detail"]

def test_inventory_draft_post_no_files(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test draft_post with no files
    save_data = {"set": "testset", "name": "nofile", "code": "pass"}
    with patch("os.listdir", return_value=[]):
        response = mock_client.post("/scriptlet/inventory/draft", json=save_data)
        assert response.status_code == 404
        assert "not existing" in response.json()["detail"]

def test_inventory_draft_post_exception(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test draft_post exception
    save_data = {"set": "testset", "name": "exception", "code": "pass"}
    with patch("os.listdir", return_value=["exception.task.py"]), \
         patch("app.lib.git.write_content", side_effect=Exception("write error")):
        response = mock_client.post("/scriptlet/inventory/draft", json=save_data)
        assert response.status_code == 500
        assert "write error" in response.json()["detail"]

def test_inventory_item_del_no_files(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test item_del with no files
    with patch("os.listdir", return_value=[]):
        response = mock_client.delete("/scriptlet/inventory/item?set=testset&name=nofile", headers={"X-User": "testuser"})
        assert response.status_code == 404
        assert "not existing" in response.json()["detail"]

def test_inventory_item_del_exception(mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
    # Test item_del exception
    with patch("os.listdir", return_value=["exception.task.py"]), \
         patch("app.lib.git.delete_file_and_commit", side_effect=Exception("delete error")):
        response = mock_client.delete("/scriptlet/inventory/item?set=testset&name=exception", headers={"X-User": "testuser"})
        assert response.status_code == 500
        assert "delete error" in response.json()["detail"]

def test_inventory_draft_del_no_files(mock_client):
    # Test draft_del with no files
    response = mock_client.delete("/scriptlet/inventory/draft?set=testset&name=nofile")
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]



def test_inventory_draft_del_exception(mock_client):
    # Test draft_del exception
    with patch("app.lib.git.get_repo_path", side_effect=Exception("repo error")):
        response = mock_client.delete("/scriptlet/inventory/draft?set=testset&name=exception")
        assert response.status_code == 500
        assert "repo error" in response.json()["detail"]

def test_inventory_code_get_no_files(mock_client):
    # Test code_get with no files
    with patch("os.listdir", return_value=[]):
        response = mock_client.get("/scriptlet/inventory/code?set=testset&name=nofile")
        assert response.status_code == 404
        assert "does not exist" in response.json()["detail"]

def test_inventory_code_get_exception(mock_client):
    # Test code_get exception - now fails earlier with 404 since set doesn't exist
    with patch("os.listdir", return_value=["exception.task.py"]), \
         patch("app.lib.git.read_content", side_effect=Exception("read error")):
        response = mock_client.get("/scriptlet/inventory/code?set=testset&name=exception")
        assert response.status_code == 404
        assert "does not exist" in response.json()["detail"]

def test_inventory_sets_exception(mock_client):
    # Test list_sets exception
    with patch("app.lib.git.get_git_base", side_effect=Exception("git base error")):
        response = mock_client.get("/scriptlet/inventory/sets", headers={"X-User": "testuser"})
        assert response.status_code == 500
        assert "git base error" in response.json()["detail"]

def test_inventory_status_no_files(mock_client):
    # Test status_get with no files
    with patch("os.listdir", return_value=[]):
        response = mock_client.get("/scriptlet/inventory/status?set=testset&name=nofile")
        assert response.status_code == 404
        assert "does not exist" in response.json()["detail"]

def test_inventory_status_exception(mock_client):
    # Test status_get exception - now fails earlier with 404 since set doesn't exist
    with patch("os.listdir", return_value=["exception.task.py"]), \
         patch("app.lib.git.has_unstaged_changes", side_effect=Exception("status error")):
        response = mock_client.get("/scriptlet/inventory/status?set=testset&name=exception")
        assert response.status_code == 404
        assert "does not exist" in response.json()["detail"]

def test_repo_status_exception(mock_client):
    # Test repo_status exception
    with patch("app.lib.git.get_repo_status", side_effect=Exception("status error")):
        response = mock_client.get("/repo/status?set=testset")
        assert response.status_code == 500
        assert "status error" in response.json()["detail"]


def test_version_endpoint(mock_client):
    with patch("app.lib.files.version", return_value="9.9.9"):
        resp = mock_client.get("/version")
        assert resp.status_code == 200
        assert resp.json() == {"version": "9.9.9"}


def test_controller_path_forwarding(mock_client, mock_controller_glob):
    # Test that additional path is forwarded in ZMQ msg
    with patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = {"path": "/v1/chat/completions"}

        # Test GET with subpath
        response = mock_client.get("/controller/testset/testname/v1/chat/completions?param=value", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/v1/chat/completions"
        assert params["type"] == "GET"
        assert params["param"] == "value"  # query param

        # Test POST with body and subpath
        json_data = {"model": "gpt-4"}
        mock_instance.request.reset_mock()
        response = mock_client.post("/controller/testset/testname/v1/chat/completions", json=json_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/v1/chat/completions"
        assert params["type"] == "POST"
        assert params["data"] == json_data
        assert params["data_type"] == "json"

        # Test empty rest
        mock_instance.request.reset_mock()
        response = mock_client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/"
        assert params["type"] == "GET"
