import pytest
import subprocess
from unittest.mock import patch, call, mock_open
from pathlib import Path


@pytest.fixture
def git_dir(tmp_path):
    d = tmp_path / "mock_git"
    d.mkdir()
    return d


def test_list_sets_empty_dir(git_dir):
    from app.lib import sets
    result = sets.list_sets(str(git_dir))
    assert result == []

def test_list_sets_nonexistent_dir(git_dir):
    from app.lib import sets
    nonexistent_dir = git_dir / "nonexistent"
    result = sets.list_sets(str(nonexistent_dir))
    assert result == []

def test_list_sets_no_git_dirs(git_dir):
    from app.lib import sets
    (git_dir / "no_git").mkdir()
    result = sets.list_sets(str(git_dir))
    assert result == []

def test_list_sets_with_git_success(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._check') as mock_check:
        mock_check.return_value = "main"
        result = sets.list_sets(str(git_dir))
    assert len(result) == 1
    assert result[0] == {"name": "testset", "branch": "main"}

def test_list_sets_with_git_error(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._check') as mock_check:
        mock_check.side_effect = subprocess.CalledProcessError(1, "git")
        result = sets.list_sets(str(git_dir))
    assert len(result) == 1
    assert result[0] == {"name": "testset", "branch": "unknown"}

def test_clone_set_exists(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        sets.clone_set(str(git_dir), "https://example.com/testset.git")

def test_clone_set_success(git_dir):
    from app.lib import sets
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets.conf_runner') as mock_conf_runner, \
         patch('os.makedirs'), \
         patch('subprocess.check_call'):
        mock_call.return_value = None
        mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
        result = sets.clone_set(str(git_dir), "https://example.com/newset.git")
    assert result == "newset"
    mock_call.assert_called_once_with(["git", "clone", "https://example.com/newset.git", "newset"], str(git_dir))

def test_clone_set_with_git_config(git_dir):
    from app.lib import sets
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets.conf_runner') as mock_conf_runner, \
         patch('os.makedirs'), \
         patch('subprocess.check_call'):
        mock_call.return_value = None
        mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
        result = sets.clone_set(str(git_dir), "https://example.com/newset.git", git_name="User", git_email="user@example.com")
    assert result == "newset"
    mock_call.assert_has_calls([
        call(["git", "clone", "https://example.com/newset.git", "newset"], str(git_dir)),
        call(["git", "config", "user.name", "User"], str(git_dir / "newset")),
        call(["git", "config", "user.email", "user@example.com"], str(git_dir / "newset"))
    ], any_order=False)

def test_clone_set_with_credentials(git_dir):
    from app.lib import sets
    from contextlib import contextmanager
    @contextmanager
    def mock_repo_lock(*args, **kwargs):
        yield
    with patch('app.lib.sets.repo_lock', mock_repo_lock), \
         patch('app.lib.sets._call') as mock_call, \
         patch('builtins.open', mock_open()) as mock_file, \
         patch('app.lib.sets.conf_runner') as mock_conf_runner, \
         patch('os.makedirs'), \
         patch('subprocess.check_call'):
        mock_call.return_value = None
        mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
        result = sets.clone_set(str(git_dir), "https://example.com/repo.git", username="user", password="pass")
    assert result == "repo"
    mock_call.assert_any_call(["git", "clone", "https://user:pass@example.com/repo.git", "repo"], str(git_dir))
    mock_file.assert_called_once_with(Path(git_dir) / "repo" / ".git" / "credentials", "w")
    mock_file().write.assert_called_once_with("https://user:pass@example.com\n")
    mock_call.assert_any_call(["git", "config", "credential.helper", "store --file .git/credentials"], str(git_dir / "repo"))

def test_remove_set_not_found(git_dir):
    from app.lib import sets
    with pytest.raises(ValueError, match="not found"):
        sets.remove_set(str(git_dir), "missing")

def test_remove_set_success(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    with patch('shutil.rmtree'), \
         patch('app.lib.sets.conf_runner') as mock_conf_runner:
        mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
        result = sets.remove_set(str(git_dir), "testset")
    assert result is True

def test_switch_branch_not_found(git_dir):
    from app.lib import sets
    with pytest.raises(ValueError, match="not found"):
        sets.switch_branch(str(git_dir), "missing", "develop")

def test_switch_branch_local_exists(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._check') as mock_check, \
         patch('app.lib.sets._call') as mock_call:
        mock_check.return_value = "refs/heads/develop"
        mock_call.return_value = None
        result = sets.switch_branch(str(git_dir), "testset", "develop")
    assert result is True
    mock_check.assert_called_once_with(["git", "rev-parse", "--verify", "refs/heads/develop"], str(git_dir / "testset"))
    mock_call.assert_called_once_with(["git", "checkout", "develop"], str(git_dir / "testset"))

def test_switch_branch_create_new_no_remote(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets._check') as mock_check:
        mock_check.side_effect = [
            subprocess.CalledProcessError(1, "git"),
            subprocess.CalledProcessError(1, "git"),
            subprocess.CalledProcessError(1, "git"),
        ]
        mock_call.return_value = None
        result = sets.switch_branch(str(git_dir), "testset", "newbranch")
    assert result is True
    mock_call.assert_called_once_with(["git", "checkout", "-b", "newbranch"], str(testset_dir))

def test_switch_branch_create_new_with_remote(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets._check') as mock_check:
        mock_check.side_effect = [
            subprocess.CalledProcessError(1, "git"),
            "origin",
            "",
            "https://example.com",
        ]
        mock_call.return_value = None
        result = sets.switch_branch(str(git_dir), "testset", "newbranch")
    assert result is True
    mock_call.assert_has_calls([
        call(["git", "checkout", "-b", "newbranch"], str(testset_dir)),
        call(["git", "push", "-u", "origin", "newbranch"], str(testset_dir)),
    ])

def test_switch_branch_remote_exists(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets._check') as mock_check:
        mock_check.side_effect = [
            subprocess.CalledProcessError(1, "git"),
            "origin",
            "abc123 refs/heads/feature",
            "https://example.com",
        ]
        mock_call.return_value = None
        result = sets.switch_branch(str(git_dir), "testset", "feature")
    assert result is True
    mock_call.assert_has_calls([
        call(["git", "fetch", "origin"], str(testset_dir)),
        call(["git", "checkout", "-b", "feature", "origin/feature"], str(testset_dir)),
    ])

def test_switch_branch_create_new_push_fails(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.sets._call') as mock_call, \
         patch('app.lib.sets._check') as mock_check:
        mock_check.side_effect = [
            subprocess.CalledProcessError(1, "git"),
            "origin",
            "",
            "https://example.com",
        ]
        mock_call.side_effect = [None, subprocess.CalledProcessError(1, "git")]
        result = sets.switch_branch(str(git_dir), "testset", "newbranch")
    assert result is True
    mock_call.assert_has_calls([
        call(["git", "checkout", "-b", "newbranch"], str(testset_dir)),
        call(["git", "push", "-u", "origin", "newbranch"], str(testset_dir)),
    ])

def test_create_set_exists(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        sets.create_set(str(git_dir), "testset")

def test_create_set_success(git_dir):
    from app.lib import sets
    with patch('app.lib.git.get_repo_path') as mock_get_repo_path, \
         patch('app.lib.sets.conf_runner') as mock_conf_runner, \
         patch('os.makedirs'), \
         patch('subprocess.check_call'):
        mock_get_repo_path.return_value = None
        mock_conf_runner.return_value = {"venv": "/venv", "pkg": "/pkg"}
        result = sets.create_set(str(git_dir), "newset")
    assert result is True
    newset_dir = git_dir / "newset"
    assert newset_dir.exists()
    mock_get_repo_path.assert_called_once_with("newset")

def test_pull_set_not_found(git_dir):
    from app.lib import sets
    with pytest.raises(ValueError, match="not found"):
        sets.pull_set(str(git_dir), "missing")

def test_pull_set_success(git_dir):
    from app.lib import sets
    testset_dir = git_dir / "testset"
    testset_dir.mkdir()
    (testset_dir / ".git").mkdir()
    with patch('app.lib.git.get_repo_path') as mock_get_repo_path, \
         patch('app.lib.git.pull') as mock_pull:
        mock_get_repo_path.return_value = str(testset_dir)
        mock_pull.return_value = True
        result = sets.pull_set(str(git_dir), "testset")
    assert result is True
    mock_get_repo_path.assert_called_once_with("testset")
    mock_pull.assert_called_once_with(str(testset_dir))
