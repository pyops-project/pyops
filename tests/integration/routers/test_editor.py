import pytest
from fastapi.testclient import TestClient
from app.web import app
from app.routers.scriptlet.editor import CompletionArgument
from unittest.mock import patch

class TestEditorRouter:
    @pytest.mark.parametrize("editor_type", ["task", "library", "view", "md"])
    def test_get_editor_html(self, mock_client, editor_type, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get(f"/scriptlet/editor_{editor_type}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        # Check for common elements in editor HTML
        assert "<ace-editor>" in response.text or "ace.js" in response.text  # Ace integration
        assert "bootstrap.min.css" in response.text  # UI framework

    def test_get_editor_html_not_found(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/editor_invalid")
        assert response.status_code == 404

    def test_post_completion(self, mock_client, mock_jedi, test_temp_dir, patch_tempfile, mock_datetime):
        completion_data = {
            "code": "def test_func():\n    pass\n\ntest_",
            "line": 3,
            "column": 5
        }
        response = mock_client.post("/scriptlet/completion", json=completion_data)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1  # From mock
        assert data[0]["name"] == "test_func"
        assert data[0]["value"] == "test_func"
        assert "Test function doc" in data[0]["meta"]
        assert data[0]["score"] == 8  # function type

    def test_post_completion_invalid_input(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        invalid_data = {"code": "", "line": -1, "column": 0}
        response = mock_client.post("/scriptlet/completion", json=invalid_data)
        assert response.status_code == 500  # Jedi ValueError caught as 500

    def test_post_completion_jedi_error(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("jedi.Script.complete", side_effect=Exception("Jedi error")):
            response = mock_client.post("/scriptlet/completion", json={"code": "def test:", "line": 0, "column": 4})
            assert response.status_code == 500

    def test_post_completion_no_auth(self, mock_client, mock_jedi, test_temp_dir, patch_tempfile, mock_datetime):
        # Assuming no auth required for completions
        response = mock_client.post("/scriptlet/completion", json={"code": "print(", "line": 0, "column": 6})
        assert response.status_code == 200

    @pytest.mark.parametrize("editor_type", ["task", "library", "view", "md"])
    def test_get_editor_html_exception(self, mock_client, editor_type, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.sites.get", side_effect=Exception("site error")):
            response = mock_client.get(f"/scriptlet/editor_{editor_type}")
            assert response.status_code == 500
            assert "site error" in response.json()["detail"]
