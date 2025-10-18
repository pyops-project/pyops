import pytest
from fastapi.testclient import TestClient
from app.web import app
from app.routers.scriptlet.inventory import CreateArgument, SaveArgument
from unittest.mock import patch, call, MagicMock

class TestInventoryRouter:
    def test_get_inventory_list(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/list")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0  # From mock sample files
        assert all("set" in item and "name" in item and "type" in item for item in data)

    def test_get_inventory_sets(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/sets")
        assert response.status_code == 200
        data = response.json()["sets"]
        assert isinstance(data, list)
        assert "default" in data  # From mock

    def test_put_inventory_item_create(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "test_set", "name": "test_task", "type": "task"}
        with patch("os.makedirs") as mock_makedirs:
            response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
            data = response.json()
            assert data["set"] == "test_set"
            assert data["name"] == "test_task"
            assert data["type"] == "task"

    def test_put_inventory_item_create_conflict(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "default", "name": "sample_task", "type": "task"}  # Exists in mock
        with patch("app.lib.git.check_name_unique", return_value=False):
            response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 409

    def test_put_inventory_item_create_invalid_type(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "test_set", "name": "invalid", "type": "invalid"}
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["set"] == "test_set"
        assert data["name"] == "invalid"
        assert data["type"] == "invalid"

    @pytest.mark.parametrize("item_type", ["task", "view", "markdown"])
    def test_post_inventory_item_save(self, mock_client, mock_git_ops, item_type, test_temp_dir, patch_tempfile, mock_datetime):
        set_name = "test_set"
        name = f"test_{item_type}"
        code = f"# Sample {item_type} code"
        save_data = {"set": set_name, "name": name, "code": code}
        repo_path = "data/git/test_set"
        expected_ext = ".py" if item_type == "task" else ".html" if item_type == "view" else ".md"
        
        with patch("os.listdir", return_value=[f"{name}{expected_ext}"]):
            response = mock_client.post("/scriptlet/inventory/item", json=save_data, headers={"X-User": "testuser"})
            assert response.status_code == 200

    def test_post_inventory_draft(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        save_data = {"set": "test_set", "name": "draft_task", "code": "# Draft code"}
        repo_path = "data/git/test_set"
        with patch("os.listdir", return_value=["draft_task.py"]):
            response = mock_client.post("/scriptlet/inventory/draft", json=save_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
        # No commit expected

    def test_delete_inventory_item(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("os.listdir", return_value=["sample_task.py"]):
            response = mock_client.delete("/scriptlet/inventory/item?set=default&name=sample_task", headers={"X-User": "testuser"})
            assert response.status_code == 200

    def test_delete_inventory_draft(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = "pass\n"

        with patch("os.listdir", return_value=["draft_task.py"]), \
             patch("app.lib.git.get_committed_content", return_value="pass\n"), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", return_value=mock_file):
            response = mock_client.delete("/scriptlet/inventory/draft?set=test_set&name=draft_task&type=task", headers={"X-User": "testuser"})
            assert response.status_code == 200

    def test_get_inventory_code(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        # Assume file written in previous mock
        with patch("app.lib.git.read_content", return_value="# Sample code"), \
             patch("os.listdir", return_value=["sample_task.py"]):
            response = mock_client.get("/scriptlet/inventory/code?set=default&name=sample_task", headers={"X-User": "testuser"})
            assert response.status_code == 200
            assert response.json()["code"] == "# Sample code"

    def test_get_repo_status(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/repo/status?set=default", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["branch"] == "main"

    def test_inventory_unauthorized(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/list")  # No X-User
        assert response.status_code == 200  # Anonymous allowed
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_inventory_invalid_set(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("os.path.exists", return_value=False):
            response = mock_client.put("/scriptlet/inventory/item", json={"set": "nonexistent", "name": "test", "type": "task"}, headers={"X-User": "testuser"})
            assert response.status_code == 400
