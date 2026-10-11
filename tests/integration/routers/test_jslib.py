import pytest
from fastapi.testclient import TestClient
from app.web import app
from unittest.mock import patch, MagicMock
import os
from pathlib import Path

class TestJSLibRouter:
    def test_get_jslib_success(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        repo_dir = Path(test_temp_dir) / "git" / "default"
        os.makedirs(repo_dir, exist_ok=True)
        (repo_dir / "mylib.js").write_text("console.log('Hello JS');")

        with patch("app.lib.git.get_repo_path", return_value=str(repo_dir)):
            response = mock_client.get("/scriptlet/jslib/default/mylib")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/javascript"
            assert response.text == "console.log('Hello JS');"

    def test_get_jslib_not_found(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        repo_dir = Path(test_temp_dir) / "git" / "default"
        os.makedirs(repo_dir, exist_ok=True)
        with patch("app.lib.git.get_repo_path", return_value=str(repo_dir)):
            response = mock_client.get("/scriptlet/jslib/default/nonexistent")
            assert response.status_code == 404

    def test_get_jslib_wrong_type(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        repo_dir = Path(test_temp_dir) / "git" / "default"
        os.makedirs(repo_dir, exist_ok=True)
        (repo_dir / "mylib.task.py").touch()

        with patch("app.lib.git.get_repo_path", return_value=str(repo_dir)):
            response = mock_client.get("/scriptlet/jslib/default/mylib")
            # No JS library of that name — the same status as every other
            # "exists, but not as the requested type" case.
            assert response.status_code == 404
            assert response.json()["detail"].endswith("::mylib is not a jslibrary")

    def test_get_jslib_committed_param(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        repo_dir = Path(test_temp_dir) / "git" / "default"
        os.makedirs(repo_dir, exist_ok=True)
        (repo_dir / "mylib.js").write_text("committed code")

        with patch("app.lib.git.get_repo_path", return_value=str(repo_dir)), \
             patch("app.lib.git.read_content", return_value="committed code") as mock_read:
            response = mock_client.get("/scriptlet/jslib/default/mylib?committed=true")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/javascript"
            assert response.text == "committed code"
            mock_read.assert_called_with(str(repo_dir), "mylib", "jslibrary", True)

    def test_get_jslib_uncommitted_param(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        repo_dir = Path(test_temp_dir) / "git" / "default"
        os.makedirs(repo_dir, exist_ok=True)
        (repo_dir / "mylib.js").write_text("unstaged code")

        with patch("app.lib.git.get_repo_path", return_value=str(repo_dir)), \
             patch("app.lib.git.read_content", return_value="unstaged code") as mock_read:
            response = mock_client.get("/scriptlet/jslib/default/mylib?committed=false")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/javascript"
            assert response.text == "unstaged code"
            mock_read.assert_called_with(str(repo_dir), "mylib", "jslibrary", False)


class TestJSLibRealSet:
    """/scriptlet/jslib against a real git set repo (no git mocks)."""

    def test_unknown_set_404(self, mock_client, set_repo):
        set_repo("s1", committed={"lib.js": "// lib\n"})
        response = mock_client.get("/scriptlet/jslib/nope/lib")
        assert response.status_code == 404
        assert response.json()["detail"] == "Set 'nope' does not exist"
