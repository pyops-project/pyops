import pytest
import subprocess
import os
from app.lib import git
from app.lib import files

@pytest.fixture
def integration_repo(tmp_path, monkeypatch):
    """Fixture for a real Git repo with remote for push tests."""
    # Mock config to use tmp_path for git base
    test_git_base = tmp_path / 'git_base'
    test_git_base.mkdir()
    monkeypatch.setattr(files, 'conf_data', lambda: {'git': str(test_git_base)})
    monkeypatch.setattr(files, 'conf_runner', lambda: {'git_pull_interval': 0})

    # Create local repo
    set_name = 'test_set'
    repo = git.get_repo_path(set_name)
    assert os.path.exists(os.path.join(repo, '.git'))

    # Init remote (simple bare repo in tmp)
    remote_path = tmp_path / 'remote'
    remote_path.mkdir()
    subprocess.run(['git', 'init', '--bare'], cwd=str(remote_path), check=True)
    subprocess.run(['git', 'remote', 'add', 'origin', str(remote_path)], cwd=repo, check=True)

    # Initial commit
    with open(os.path.join(repo, 'README.md'), 'w') as f:
        f.write('# Test Repo\n')
    subprocess.run(['git', 'add', 'README.md'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=repo, check=True)
    subprocess.run(['git', 'branch', '-M', 'main'], cwd=repo, check=True)
    subprocess.run(['git', 'push', '-u', 'origin', 'main'], cwd=repo, check=True)

    yield repo

    # Cleanup (tmp_path handles deletion)

class TestGitIntegration:
    def test_has_unstaged_changes_clean_committed_pushed(self, integration_repo):
        """Test has_unstaged_changes returns False for clean committed/pushed file."""
        # Create and commit a script file
        file_path = os.path.join(integration_repo, 'script.task.py')
        with open(file_path, 'w') as f:
            f.write('def test():\n    pass\n')
        subprocess.run(['git', 'add', 'script.task.py'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add script'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'push'], cwd=integration_repo, check=True)

        # Verify no unstaged changes
        result = git.has_unstaged_changes(integration_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_dirty_after_modify(self, integration_repo):
        """Test has_unstaged_changes returns True after modifying committed file."""
        # Setup committed file (from fixture or create)
        file_path = os.path.join(integration_repo, 'script.task.py')
        if not os.path.exists(file_path):
            with open(file_path, 'w') as f:
                f.write('def test():\n    pass\n')
            subprocess.run(['git', 'add', 'script.task.py'], cwd=integration_repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'Add script'], cwd=integration_repo, check=True)
            subprocess.run(['git', 'push'], cwd=integration_repo, check=True)

        # Modify file (unstaged change)
        with open(file_path, 'a') as f:
            f.write('\n# Modified\n')
        # No add/commit, so unstaged

        result = git.has_unstaged_changes(integration_repo, 'script', 'task')
        assert result is True

    def test_has_unstaged_changes_clean_after_push_no_local_change(self, integration_repo):
        """Test has_unstaged_changes remains False after push for no local changes."""
        # Assume committed/pushed from previous
        file_path = os.path.join(integration_repo, 'script.task.py')
        with open(file_path, 'w') as f:
            f.write('def test():\n    pass\n')
        subprocess.run(['git', 'add', 'script.task.py'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add script'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'push'], cwd=integration_repo, check=True)

        # No local modification, verify False
        result = git.has_unstaged_changes(integration_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_new_uncommitted_file(self, integration_repo):
        """Test has_unstaged_changes True for new uncommitted file."""
        file_path = os.path.join(integration_repo, 'newlib.library.py')
        with open(file_path, 'w') as f:
            f.write('def new():\n    pass\n')

        result = git.has_unstaged_changes(integration_repo, 'newlib', 'library')
        assert result is True

    def test_has_unstaged_changes_pager_error_treated_as_clean(self, integration_repo, monkeypatch):
        """Simulate pager error (rc=1 with stderr) and verify treated as no changes."""
        # Mock subprocess.run to simulate git diff failure with stderr
        def mock_run(cmd, *args, **kwargs):
            if 'git diff --quiet' in ' '.join(cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout='', stderr='pager not found')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        monkeypatch.setattr('subprocess.run', mock_run)

        # Mock subprocess.check_output for git show to simulate committed file
        def mock_check_out(cmd, *args, **kwargs):
            cmd_str = ' '.join(cmd)
            if 'git show HEAD:script.task.py' in cmd_str:
                return b'def test():\n    pass\n'
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr('subprocess.check_output', mock_check_out)

        # Setup a clean committed file (mocks handle git state)
        file_path = os.path.join(integration_repo, 'script.task.py')
        with open(file_path, 'w') as f:
            f.write('def test():\n    pass\n')
        subprocess.run(['git', 'add', 'script.task.py'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add script'], cwd=integration_repo, check=True)

        result = git.has_unstaged_changes(integration_repo, 'script', 'task')
        assert result is False  # Treated as clean due to stderr
