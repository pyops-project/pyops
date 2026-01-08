import pytest
import subprocess
from unittest.mock import Mock, patch, call, mock_open
from pathlib import Path
import shutil
import os

from app.lib import sets

class TestSets:
    @pytest.fixture(autouse=True)
    def setup_method(self, tmp_path):
        self.git_dir = tmp_path / "mock_git"
        self.git_dir.mkdir()

    def test_list_sets_empty_dir(self):
        result = sets.list_sets(str(self.git_dir))
        assert result == []

    def test_list_sets_nonexistent_dir(self):
        nonexistent_dir = self.git_dir / "nonexistent"
        result = sets.list_sets(str(nonexistent_dir))
        assert result == []

    def test_list_sets_no_git_dirs(self):
        (self.git_dir / "no_git").mkdir()
        result = sets.list_sets(str(self.git_dir))
        assert result == []

    def test_list_sets_with_git_success(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._check') as mock_check:
            mock_check.return_value = "main"
            result = sets.list_sets(str(self.git_dir))
        assert len(result) == 1
        assert result[0] == {"name": "testset", "branch": "main"}

    def test_list_sets_with_git_error(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._check') as mock_check:
            mock_check.side_effect = subprocess.CalledProcessError(1, "git")
            result = sets.list_sets(str(self.git_dir))
        assert len(result) == 1
        assert result[0] == {"name": "testset", "branch": "unknown"}

    def test_clone_set_exists(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        with pytest.raises(ValueError, match="already exists"):
            sets.clone_set(str(self.git_dir), "testset", "https://example.com")

    def test_clone_set_success(self):
        with patch('app.lib.sets._call') as mock_call:
            mock_call.return_value = None
            result = sets.clone_set(str(self.git_dir), "newset", "https://example.com")
        assert result is True
        mock_call.assert_called_once_with(["git", "clone", "https://example.com", "newset"], str(self.git_dir))


    def test_clone_set_with_git_config(self):
        with patch('app.lib.sets._call') as mock_call:
            mock_call.return_value = None
            result = sets.clone_set(str(self.git_dir), "newset", "https://example.com", git_name="User", git_email="user@example.com")
        assert result is True
        mock_call.assert_has_calls([
            call(["git", "clone", "https://example.com", "newset"], str(self.git_dir)),
            call(["git", "config", "user.name", "User"], str(self.git_dir / "newset")),
            call(["git", "config", "user.email", "user@example.com"], str(self.git_dir / "newset"))
        ], any_order=False)

    def test_clone_set_with_credentials(self):
        from contextlib import contextmanager
        @contextmanager
        def mock_repo_lock(*args, **kwargs):
            yield
        with patch('app.lib.sets.repo_lock', mock_repo_lock), \
             patch('app.lib.sets._call') as mock_call, \
             patch('builtins.open', mock_open()) as mock_file:
            mock_call.return_value = None
            result = sets.clone_set(str(self.git_dir), "newset", "https://example.com/repo", username="user", password="pass")
        assert result is True
        # Check clone URL has credentials
        mock_call.assert_any_call(["git", "clone", "https://user:pass@example.com/repo", "newset"], str(self.git_dir))
        # Check credentials stored
        mock_file.assert_called_once_with(Path(self.git_dir) / "newset" / ".git" / "credentials", "w")
        mock_file().write.assert_called_once_with("https://user:pass@example.com\n")
        mock_call.assert_any_call(["git", "config", "credential.helper", "store --file .git/credentials"], str(self.git_dir / "newset"))



    def test_remove_set_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            sets.remove_set(str(self.git_dir), "missing")

    def test_remove_set_success(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        with patch('shutil.rmtree'):
            result = sets.remove_set(str(self.git_dir), "testset")
        assert result is True

    def test_switch_branch_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            sets.switch_branch(str(self.git_dir), "missing", "develop")

    def test_switch_branch_success(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._call') as mock_call:
            mock_call.return_value = None
            result = sets.switch_branch(str(self.git_dir), "testset", "develop")
        assert result is True
        mock_call.assert_called_once_with(["git", "checkout", "develop"], str(self.git_dir / "testset"))
