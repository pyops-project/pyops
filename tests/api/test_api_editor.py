import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.web import app
from app.lib.files import conf_data
from app.lib.git import get_file_path, read_content
import jedi

def test_get_editor_task(mock_client, mock_git_ops, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/editor_task
    response = mock_client.get("/scriptlet/editor_task")
    assert response.status_code == 200
    assert "<html>" in response.text

def test_get_editor_library(mock_client, mock_git_ops, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/editor_library
    response = mock_client.get("/scriptlet/editor_library")
    assert response.status_code == 200
    assert "<html>" in response.text

def test_get_editor_view(mock_client, mock_git_ops, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/editor_view
    response = mock_client.get("/scriptlet/editor_view")
    assert response.status_code == 200
    assert "<html>" in response.text

def test_get_editor_md(mock_client, mock_git_ops, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Test GET /scriptlet/editor_md
    response = mock_client.get("/scriptlet/editor_md")
    assert response.status_code == 200
    assert "<html>" in response.text

def test_get_editor_not_found(mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Invalid editor type
    response = mock_client.get("/scriptlet/editor_invalid", headers={"X-User": "testuser"})
    assert response.status_code == 404

def test_post_completion_success(mock_client, mock_git_ops, mock_jedi, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Test POST /scriptlet/completion
    response = mock_client.post(
        "/scriptlet/completion",
        json={
            "code": "def test_func(): pass",
            "line": 1,
            "column": 5
        },
        headers={"X-User": "testuser"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "test_func"
    assert "score" in data[0]

def test_post_completion_invalid_input(mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Invalid input
    response = mock_client.post(
        "/scriptlet/completion",
        json={
            "code": "",
            "line": 0,
            "column": 0
        },
        headers={"X-User": "testuser"}
    )
    assert response.status_code == 500

def test_post_completion_jedi_error(mock_client, mock_git_ops, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Jedi error
    with patch('app.routers.scriptlet.editor.jedi.Script') as mock_script:
        mock_script.side_effect = Exception("Jedi error")
        response = mock_client.post(
            "/scriptlet/completion",
            json={
                "code": "invalid code",
                "line": 1,
                "column": 1
            },
            headers={"X-User": "testuser"}
        )
        assert response.status_code == 500

def test_post_completion_no_auth(mock_client, mock_git_ops, mock_jedi, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # No auth
    response = mock_client.post(
        "/scriptlet/completion",
        json={
            "code": "def hello(): pass",
            "line": 1,
            "column": 5
        }
    )
    assert response.status_code == 200

def test_editor_unauthorized(mock_client, mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # No auth for editor
    response = mock_client.get("/scriptlet/editor_task")
    assert response.status_code == 200
