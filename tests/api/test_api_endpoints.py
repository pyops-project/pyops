import pytest
from unittest.mock import patch, MagicMock
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
         patch("app.lib.git.check_name_unique", return_value=True), \
         patch("os.makedirs"):
        mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
    # Second attempt - conflict
    with patch("app.lib.git.check_name_unique", return_value=False):
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
