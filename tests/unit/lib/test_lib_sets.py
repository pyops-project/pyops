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
            sets.clone_set(str(self.git_dir), "https://example.com/testset.git")

    def test_clone_set_success(self):
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets.conf_runner') as mock_conf_runner, \
             patch('os.makedirs') as mock_makedirs, \
             patch('subprocess.check_call') as mock_check_call:
            mock_call.return_value = None
            mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
            result = sets.clone_set(str(self.git_dir), "https://example.com/newset.git")
        assert result == "newset"
        mock_call.assert_called_once_with(["git", "clone", "https://example.com/newset.git", "newset"], str(self.git_dir))


    def test_clone_set_with_git_config(self):
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets.conf_runner') as mock_conf_runner, \
             patch('os.makedirs') as mock_makedirs, \
             patch('subprocess.check_call') as mock_check_call:
            mock_call.return_value = None
            mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
            result = sets.clone_set(str(self.git_dir), "https://example.com/newset.git", git_name="User", git_email="user@example.com")
        assert result == "newset"
        mock_call.assert_has_calls([
            call(["git", "clone", "https://example.com/newset.git", "newset"], str(self.git_dir)),
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
             patch('builtins.open', mock_open()) as mock_file, \
             patch('app.lib.sets.conf_runner') as mock_conf_runner, \
             patch('os.makedirs') as mock_makedirs, \
             patch('subprocess.check_call') as mock_check_call:
            mock_call.return_value = None
            mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
            result = sets.clone_set(str(self.git_dir), "https://example.com/repo.git", username="user", password="pass")
        assert result == "repo"
        # Check clone URL has credentials
        mock_call.assert_any_call(["git", "clone", "https://user:pass@example.com/repo.git", "repo"], str(self.git_dir))
        # Check credentials stored
        mock_file.assert_called_once_with(Path(self.git_dir) / "repo" / ".git" / "credentials", "w")
        mock_file().write.assert_called_once_with("https://user:pass@example.com\n")
        mock_call.assert_any_call(["git", "config", "credential.helper", "store --file .git/credentials"], str(self.git_dir / "repo"))



    def test_remove_set_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            sets.remove_set(str(self.git_dir), "missing")

    def test_remove_set_success(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        with patch('shutil.rmtree'), \
             patch('app.lib.sets.conf_runner') as mock_conf_runner:
            mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
            result = sets.remove_set(str(self.git_dir), "testset")
        assert result is True

    def test_switch_branch_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            sets.switch_branch(str(self.git_dir), "missing", "develop")

    def test_switch_branch_local_exists(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._check') as mock_check, \
             patch('app.lib.sets._call') as mock_call:
            mock_check.return_value = "refs/heads/develop"  # Branch exists locally
            mock_call.return_value = None
            result = sets.switch_branch(str(self.git_dir), "testset", "develop")
        assert result is True
        mock_check.assert_called_once_with(["git", "rev-parse", "--verify", "refs/heads/develop"], str(self.git_dir / "testset"))
        mock_call.assert_called_once_with(["git", "checkout", "develop"], str(self.git_dir / "testset"))

    def test_switch_branch_create_new_no_remote(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets._check') as mock_check:
            # Local check fails, remote check fails (no remote), create branch
            mock_check.side_effect = [
                subprocess.CalledProcessError(1, "git"),  # Local branch check
                subprocess.CalledProcessError(1, "git"),  # Remote list
                subprocess.CalledProcessError(1, "git"),  # Remote URL check
            ]
            mock_call.return_value = None
            result = sets.switch_branch(str(self.git_dir), "testset", "newbranch")
        assert result is True
        mock_call.assert_called_once_with(["git", "checkout", "-b", "newbranch"], str(testset_dir))

    def test_switch_branch_create_new_with_remote(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets._check') as mock_check:
            # Local check fails, remote exists but branch not on remote, create and push
            mock_check.side_effect = [
                subprocess.CalledProcessError(1, "git"),  # Local branch
                "origin",  # Remotes
                "",  # ls-remote returns empty (branch not on remote)
                "https://example.com",  # Remote URL
            ]
            mock_call.return_value = None
            result = sets.switch_branch(str(self.git_dir), "testset", "newbranch")
        assert result is True
        expected_calls = [
            call(["git", "checkout", "-b", "newbranch"], str(testset_dir)),
            call(["git", "push", "-u", "origin", "newbranch"], str(testset_dir)),
        ]
        mock_call.assert_has_calls(expected_calls)

    def test_switch_branch_remote_exists(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets._check') as mock_check:
            # Local check fails, remote exists and has branch, fetch and checkout
            mock_check.side_effect = [
                subprocess.CalledProcessError(1, "git"),  # Local branch
                "origin",  # Remotes
                "abc123 refs/heads/feature",  # ls-remote returns branch exists
                "https://example.com",  # Remote URL (not needed but checked)
            ]
            mock_call.return_value = None
            result = sets.switch_branch(str(self.git_dir), "testset", "feature")
        assert result is True
        expected_calls = [
            call(["git", "fetch", "origin"], str(testset_dir)),
            call(["git", "checkout", "-b", "feature", "origin/feature"], str(testset_dir)),
        ]
        mock_call.assert_has_calls(expected_calls)

    def test_switch_branch_create_new_push_fails(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.sets._call') as mock_call, \
             patch('app.lib.sets._check') as mock_check:
            # Local check fails, remote exists but branch not on remote, create, push fails
            mock_check.side_effect = [
                subprocess.CalledProcessError(1, "git"),  # Local branch
                "origin",  # Remotes
                "",  # ls-remote empty
                "https://example.com",  # Remote URL
            ]
            mock_call.side_effect = [None, subprocess.CalledProcessError(1, "git")]  # checkout -b succeeds, push fails
            result = sets.switch_branch(str(self.git_dir), "testset", "newbranch")
        assert result is True  # Should still succeed
        expected_calls = [
            call(["git", "checkout", "-b", "newbranch"], str(testset_dir)),
            call(["git", "push", "-u", "origin", "newbranch"], str(testset_dir)),
        ]
        mock_call.assert_has_calls(expected_calls)

    def test_create_set_exists(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        with pytest.raises(ValueError, match="already exists"):
            sets.create_set(str(self.git_dir), "testset")

    def test_create_set_success(self):
        with patch('app.lib.git.get_repo_path') as mock_get_repo_path, \
             patch('app.lib.sets.conf_runner') as mock_conf_runner, \
             patch('os.makedirs') as mock_makedirs, \
             patch('subprocess.check_call') as mock_check_call:
            mock_get_repo_path.return_value = None
            mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
            result = sets.create_set(str(self.git_dir), "newset")
        assert result is True
        newset_dir = self.git_dir / "newset"
        assert newset_dir.exists()
        mock_get_repo_path.assert_called_once_with("newset")

    def test_pull_set_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            sets.pull_set(str(self.git_dir), "missing")

    def test_pull_set_success(self):
        testset_dir = self.git_dir / "testset"
        testset_dir.mkdir()
        (testset_dir / ".git").mkdir()
        with patch('app.lib.git.get_repo_path') as mock_get_repo_path, \
             patch('app.lib.git.pull_if_needed') as mock_pull_if_needed:
            mock_get_repo_path.return_value = str(testset_dir)
            mock_pull_if_needed.return_value = True
            result = sets.pull_set(str(self.git_dir), "testset")
        assert result is True
        mock_get_repo_path.assert_called_once_with("testset")
        mock_pull_if_needed.assert_called_once_with(str(testset_dir))
