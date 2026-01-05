import pytest
import os
import subprocess
from unittest.mock import patch, MagicMock, mock_open, ANY
from app.lib import git
from app.lib import files
import shutil

@pytest.fixture
def tmp_cache(tmp_path):
    cache_dir = tmp_path / '.cache'
    cache_dir.mkdir(exist_ok=True)
    git_dir = cache_dir / 'git'
    git_dir.mkdir(exist_ok=True)
    return tmp_path

@pytest.fixture
def mock_repo(tmp_cache, request):
    set_name = getattr(request, 'param', 'test_set')
    repo_path = tmp_cache / '.cache' / 'git' / set_name
    repo_path.mkdir(parents=True)
    git_dir = repo_path / '.git'
    git_dir.mkdir()
    # Mock .gitignore and README.md
    gitignore = repo_path / '.gitignore'
    gitignore.write_text('*.bck\n')
    readme = repo_path / 'README.md'
    readme.write_text(f'# {set_name}\n')
    return str(repo_path)

@pytest.fixture
def mock_subprocess():
    with patch('subprocess.run') as mock_run, \
         patch('subprocess.check_output') as mock_check:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        mock_check.return_value = b''
        yield mock_run, mock_check

@pytest.fixture
def mock_files_conf(tmp_path):
    test_git_base = str(tmp_path / '.cache/git')
    os.makedirs(test_git_base, exist_ok=True)
    with patch.object(files, 'conf_data', return_value={'git': test_git_base}), \
         patch.object(files, 'conf_runner', return_value={'git_pull_interval': 0}):
        yield

@pytest.fixture
def mock_fcntl():
    with patch('fcntl.flock') as mock_flock, \
         patch('os.unlink'):
        mock_flock.return_value = None
        yield mock_flock

@pytest.fixture
def mock_logging():
    mock_instance = MagicMock()
    with patch.object(git, 'logger', mock_instance):
        yield mock_instance

class TestPullIfNeeded:
    def test_pull_if_needed_happy_path(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging, mock_fcntl):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct check_output tag, _check status, _check branch, _check pre, run fetch, _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote _check
            b'',  # tag direct
            b'',  # status _check
            b'main\n',  # branch _check
            b'abc123\n',  # pre _check
            b'def456\n',  # post _check
            b'new commit msg\n',  # log _check
            b'file1.py\nfile2.html\nnewfile.py\n',  # ls-tree _check
        ]
        def run_side_effect(cmd, *args, **kwargs):
            cmd_str = ' '.join(cmd)
            if 'fetch' in cmd_str:
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            if 'reset --soft' in cmd_str:
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            if 'checkout' in cmd_str and 'HEAD' in cmd_str and '--' in cmd_str:
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        mock_run.side_effect = run_side_effect
        result = git.pull_if_needed(mock_repo)
        assert result is True
        mock_logging.info.assert_any_call('Reset test_set to origin/HEAD: new commit msg')
        assert any(call.args[0] == ['git', 'fetch'] for call in mock_run.call_args_list)
        assert any(call.args[0] == ['git', 'reset', '--soft', 'origin/main'] for call in mock_run.call_args_list)
        assert any(call.args[0] == ['git', 'checkout', 'HEAD', '--', 'newfile.py'] for call in mock_run.call_args_list)
        mock_logging.info.assert_any_call('Added 3 new files to test_set working directory')

    def test_pull_if_needed_no_changes(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status, _check branch, _check pre, run fetch, _check post (same, no log/ls-tree)
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'abc123\n',  # post
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull_if_needed(mock_repo)
        assert result is True
        mock_logging.debug.assert_any_call('No changes reset for test_set')

    def test_pull_if_needed_no_remote(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [b'']  # remote _check empty
        result = git.pull_if_needed(mock_repo)
        assert result is False
        mock_logging.info.assert_called_with('No remote for test_set; skipping reset')

    def test_pull_if_needed_on_tag(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag non-empty, _check describe succeeds
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'tag123\n',  # tag
            b'tag123\n',  # describe
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull_if_needed(mock_repo)
        assert result is False
        mock_logging.info.assert_called_with('On tag in test_set; skipping reset')

    def test_pull_if_needed_dirty_repo(self, mock_repo, mock_subprocess, mock_files_conf):
        # Note: Code always resets soft, preserves unstaged
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status dirty, _check branch, _check pre, run fetch, _check post, _check log, _check ls-tree (no new)
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b' M file.py\n',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file1.py\n',  # ls-tree
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull_if_needed(mock_repo)
        assert result is True
        # Reset still called despite dirty

    def test_pull_if_needed_retry_success(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status, _check branch, _check pre, run fetch (retry 2 fail, 3 success), _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file1.py\n',  # ls-tree
        ]
        fetch_count = [0]
        def run_side_effect(cmd, *args, **kwargs):
            cmd_str = ' '.join(cmd)
            if 'fetch' in cmd_str:
                fetch_count[0] += 1
                if fetch_count[0] < 3:
                    raise subprocess.CalledProcessError(1, cmd, output=b'retry', stderr='temp fail')
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            if 'reset --soft' in cmd_str:
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        mock_run.side_effect = run_side_effect
        result = git.pull_if_needed(mock_repo)
        assert result is True
        assert fetch_count[0] == 3
        assert mock_logging.warning.call_count == 2  # 2 retries

    def test_pull_if_needed_final_failure(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
        ]
        fetch_count = [0]
        def run_side_effect(cmd, *args, **kwargs):
            cmd_str = ' '.join(cmd)
            if 'fetch' in cmd_str:
                fetch_count[0] += 1
                raise subprocess.CalledProcessError(128, cmd, output=b'fail', stderr='permanent')
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        mock_run.side_effect = run_side_effect
        with pytest.raises(subprocess.CalledProcessError):
            git.pull_if_needed(mock_repo)
        assert fetch_count[0] == 3
        mock_logging.error.assert_called()

    def test_pull_if_needed_new_file_failure(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status, _check branch, _check pre, run fetch, _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file.py\nnewfile.py\n',  # ls-tree
        ]
        def run_side_effect(cmd, *args, **kwargs):
            cmd_str = ' '.join(cmd)
            if 'fetch' in cmd_str or 'reset --soft' in cmd_str:
                return subprocess.CompletedProcess(args=cmd, returncode=0)
            if 'checkout' in cmd_str and 'newfile.py' in cmd_str:
                raise subprocess.CalledProcessError(1, cmd, output=b'', stderr='checkout fail')
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        mock_run.side_effect = run_side_effect
        result = git.pull_if_needed(mock_repo)
        assert result is True
        mock_logging.warning.assert_called()

    def test_pull_if_needed_no_git_dir(self, tmp_path, mock_files_conf):
        repo = str(tmp_path / 'no_git')
        result = git.pull_if_needed(repo)
        assert result is False

class TestRepoPathAndFiles:
    def test_get_repo_path_new(self, tmp_path, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value.returncode = 0
        with patch('os.path.exists') as mock_exists:
            def exists_side(p):
                if '.git' in p or p.endswith('.gitignore') or p.endswith('README.md'):
                    return False
                return True
            mock_exists.side_effect = exists_side
            repo = git.get_repo_path('new_set')
            assert str(tmp_path / '.cache/git/new_set') in repo
            mock_run.assert_any_call(['git', 'init'], cwd=ANY, capture_output=True, text=True, check=True)
            # .gitignore and README commits
            assert any(call.args[0] == ['git', 'commit', '-m', 'Initial .gitignore for new_set'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'commit', '-m', 'Initial README for new_set'] for call in mock_run.call_args_list)

    def test_get_repo_path_existing(self, mock_repo, mock_subprocess, mock_files_conf):
        # Assume already exists, no init
        with patch('os.path.exists', return_value=True):
            with patch('subprocess.run') as mock_run:
                git.get_repo_path('test_set')
                mock_run.assert_not_called()  # No init if .git exists

    @pytest.mark.parametrize('type_, name, expected', [
        ('task', 'script', 'script.task.py'),
        ('view', 'view', 'view.html'),
        ('markdown', 'README', 'README.md'),
    ])
    def test_get_file_path(self, mock_repo, type_, name, expected):
        file_path = git.get_file_path(mock_repo, name, type_)
        assert expected in os.path.basename(file_path)

    @pytest.mark.parametrize('type_, name', [('invalid', 'script')])
    def test_get_file_path_invalid_type(self, mock_repo, type_, name):
        with pytest.raises(ValueError):
            git.get_file_path(mock_repo, name, type_)

    @pytest.mark.parametrize('filename, expected', [
        ('script.task.py', 'script'),
        ('view.html', 'view'),
        ('README.md', 'README'),
    ])
    def test_get_name_from_file(self, filename, expected):
        assert git.get_name_from_file(filename) == expected

    @pytest.mark.parametrize('filename, expected', [
        ('script.task.py', 'task'),
        ('view.html', 'view'),
        ('README.md', 'markdown'),
    ])
    def test_infer_type_from_filename(self, filename, expected):
        assert git.infer_type_from_filename(filename) == expected

    @pytest.mark.parametrize('filename', ['invalid.py', 'script.invalid.py'])
    def test_infer_type_from_filename_invalid(self, filename):
        with pytest.raises(ValueError):
            git.infer_type_from_filename(filename)

class TestListAndUnique:
    def test_list_scriptlets(self, tmp_path, mock_files_conf, mock_subprocess):
        # Mock sets dir with repos
        def listdir_side_effect(path):
            if '.cache/git' in path:
                return ['set1']
            else:
                return ['script.task.py', 'view.html', 'README.md', 'invalid.txt']
        with patch('os.listdir', side_effect=listdir_side_effect), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('app.lib.git.get_repo_path', return_value=str(tmp_path / 'repo')):
            result = git.list_scriptlets()
            assert len(result) == 3
            # Adjust for sorted sets; expect set1 first
            expected = [
                {'set': 'set1', 'name': 'script', 'type': 'task'},
                {'set': 'set1', 'name': 'view', 'type': 'view'},
                {'set': 'set1', 'name': 'README', 'type': 'markdown'}
            ]
            for e in expected:
                assert e in result

    def test_check_name_unique(self, mock_repo):
        with patch('os.listdir', return_value=[]):
            assert git.check_name_unique(mock_repo, 'unique') is True
        with patch('os.listdir', return_value=['unique.task.py']):
            assert git.check_name_unique(mock_repo, 'unique') is False

class TestContentOps:
    def test_read_content_unstaged_false(self, mock_repo):
        content = 'test code'
        mock_file = mock_open(read_data=content)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_file):
            result = git.read_content(mock_repo, 'script', 'task', committed=False)
            assert result == content
            mock_file.assert_called_once_with(ANY, 'r', encoding='utf-8')

    def test_read_content_unstaged_true(self, mock_repo, mock_subprocess, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, [], output=b'')
        content = 'test code'
        mock_file = mock_open(read_data=content)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_file):
            result = git.read_content(mock_repo, 'script', 'task', committed=True)
            assert result == content
            mock_file.assert_called_once_with(ANY, 'r', encoding='utf-8')
            mock_logging.warning.assert_called()


    def test_read_content_committed_git_show(self, mock_repo, mock_subprocess):
        mock_run, mock_check = mock_subprocess
        mock_check.return_value = b'committed content'
        with patch('os.path.exists', return_value=False):
            result = git.read_content(mock_repo, 'script', 'task', committed=True)
            assert result == 'committed content'

    def test_read_content_committed_fallback(self, mock_repo, mock_subprocess, mock_logging):
        content = 'fallback'
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, [], output=b'')
        mock_file = mock_open(read_data=content)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_file):
            result = git.read_content(mock_repo, 'script', 'task', committed=True)
            assert result == content
            mock_file.assert_called_once_with(ANY, 'r', encoding='utf-8')
            mock_logging.warning.assert_called()

    def test_read_content_not_found(self, mock_repo, mock_subprocess):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, [], output=b'')
        with patch('os.path.exists', return_value=False):
            with pytest.raises(FileNotFoundError):
                git.read_content(mock_repo, 'missing', 'task', committed=True)

    def test_read_content_committed_strict_success(self, mock_repo, mock_subprocess):
        mock_run, mock_check = mock_subprocess
        mock_check.return_value = b'committed strict'
        result = git.read_content(mock_repo, 'script', 'task', committed=True, strict=True)
        assert result == 'committed strict'

    def test_read_content_committed_strict_raise(self, mock_repo, mock_subprocess):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, [], output=b'')
        with patch('os.path.exists', return_value=True):
            with pytest.raises(FileNotFoundError, match="No committed version"):
                git.read_content(mock_repo, 'script', 'task', committed=True, strict=True)

    def test_write_content(self, tmp_path, mock_files_conf):
        repo = str(tmp_path / '.cache/git/test_repo')
        os.makedirs(repo, exist_ok=True)
        content = 'written content'
        git.write_content(repo, 'script', 'task', content)
        file_path = os.path.join(repo, 'script.task.py')
        assert os.path.exists(file_path)
        with open(file_path, 'r') as f:
            assert f.read() == content

class TestCommitOps:
    def test_perform_git_add_commit_push(self, mock_repo, mock_subprocess, mock_files_conf, mock_fcntl, mock_logging):
        mock_run, mock_check = mock_subprocess
        # For perform: direct tag empty (no describe)
        mock_check.side_effect = [b'', b'origin\n']  # tag, remote
        file_path = os.path.join(mock_repo, 'test.py')
        with patch('app.lib.git.pull_if_needed', return_value=True):
            git.perform_git_add_commit_push(mock_repo, file_path, 'test commit')
            # Assert add, commit, push called
            assert any(call.args[0] == ['git', 'add', 'test.py'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'commit', '-m', 'test commit'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'push'] for call in mock_run.call_args_list)

    def test_perform_git_add_commit_push_on_tag(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        # For perform: direct tag1 non-empty, _check describe2 succeeds
        mock_check.side_effect = [
            b'tag123\n',  # tag
            b'tag123\n',  # describe
        ]
        with patch('app.lib.git.pull_if_needed', return_value=True):
            file_path = os.path.join(mock_repo, 'test.py')
            with pytest.raises(git.HTTPException, match='Cannot commit on a tag.'):
                git.perform_git_add_commit_push(mock_repo, file_path, 'test')

    def test_batch_commit(self, mock_repo, mock_subprocess, mock_files_conf, mock_fcntl):
        mock_run, mock_check = mock_subprocess
        # For batch: direct tag1 empty
        mock_check.side_effect = [b'', b'origin\n']  # tag, remote
        rel_files = ['file1.py', 'file2.html']
        with patch('app.lib.git.pull_if_needed', return_value=True):
            git.batch_commit(mock_repo, 'batch msg', rel_files)
            assert any(call.args[0] == ['git', 'add', 'file1.py'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'add', 'file2.html'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'commit', '-m', 'batch msg'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'push'] for call in mock_run.call_args_list)

    def test_delete_file_and_commit(self, mock_repo, mock_subprocess, mock_files_conf, mock_fcntl):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [b'origin\n']  # remote for push check
        # No tag check in delete, only pull (patched)
        file_path = os.path.join(mock_repo, 'test.py')
        with patch('app.lib.git.pull_if_needed', return_value=True):
            git.delete_file_and_commit(mock_repo, file_path, 'delete msg')
            assert any(call.args[0] == ['git', 'rm', '-f', 'test.py'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'commit', '-m', 'delete msg'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'push'] for call in mock_run.call_args_list)

    def test_delete_file_and_commit_no_remote(self, mock_repo, mock_subprocess, mock_files_conf, mock_fcntl):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [b'']  # _check git remote empty
        file_path = os.path.join(mock_repo, 'test.py')
        with patch('app.lib.git.pull_if_needed', return_value=True):
            git.delete_file_and_commit(mock_repo, file_path, 'delete msg')
            assert any(call.args[0] == ['git', 'rm', '-f', 'test.py'] for call in mock_run.call_args_list)
            assert any(call.args[0] == ['git', 'commit', '-m', 'delete msg'] for call in mock_run.call_args_list)
            assert not any(call.args[0] == ['git', 'push'] for call in mock_run.call_args_list)

class TestStatusAndRollback:
    def test_get_repo_status_not_tag(self, tmp_path, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        # Calls: _check branch, direct tag empty
        mock_check.side_effect = [b'main\n', b'']
        repo = str(tmp_path / '.cache/git/test_set')
        os.makedirs(repo, exist_ok=True)
        os.makedirs(os.path.join(repo, '.git'), exist_ok=True)
        result = git.get_repo_status('test_set')
        assert result == {'branch': 'main', 'is_tag': False, 'flag': None}

    def test_get_repo_status_on_tag(self, tmp_path, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        # Calls: _check branch, direct tag non-empty, _check describe succeeds
        mock_check.side_effect = [b'main\n', b'tag123\n', b'tag123\n']
        repo = str(tmp_path / '.cache/git/test_set')
        os.makedirs(repo, exist_ok=True)
        os.makedirs(os.path.join(repo, '.git'), exist_ok=True)
        result = git.get_repo_status('test_set')
        assert result == {'branch': 'main', 'is_tag': True, 'flag': 'TAG'}

    def test_rollback_staged(self, tmp_path, mock_subprocess, mock_files_conf):
        repo = str(tmp_path / '.cache/git/set')
        os.makedirs(repo, exist_ok=True)
        os.makedirs(os.path.join(repo, '.git'), exist_ok=True)
        file_path = os.path.join(repo, 'script.task.py')
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            f.write('unstaged')
        bck_path = file_path + '.bck'
        with patch('shutil.copy2') as mock_copy:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value.returncode = 0
                git.rollback_staged('set', 'script', 'task')
                mock_copy.assert_called_once_with(file_path, bck_path)
                mock_run.assert_called_with(['git', 'checkout', 'HEAD', '--', 'script.task.py'], cwd=ANY, check=True, capture_output=True, text=True)

    def test_rollback_committed(self, tmp_path, mock_subprocess, mock_files_conf):
        repo = str(tmp_path / '.cache/git/set')
        os.makedirs(repo, exist_ok=True)
        os.makedirs(os.path.join(repo, '.git'), exist_ok=True)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.returncode = 0
            git.rollback_committed('set', 'script', 'task')
            mock_run.assert_called_with(['git', 'checkout', 'HEAD', '--', 'script.task.py'], cwd=ANY, check=True, capture_output=True, text=True)

    def test_repo_lock_contention(self, tmp_path, mock_files_conf):
        git_base = files.conf_data()["git"]
        with patch('app.lib.git.repo_lock') as mock_lock:
            mock_lock.return_value.__enter__.side_effect = BlockingIOError()
            with pytest.raises(BlockingIOError):
                with git.repo_lock(git_base, 'test_set'):
                    pass

    def test_has_unstaged_changes_clean(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value.returncode = 0  # no diff
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_dirty(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value.returncode = 1  # diff
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is True

    def test_has_unstaged_changes_error(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout='', stderr='fatal error'
        )
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False
        mock_logging.warning.assert_called_once()
        assert 'Unexpected git diff result for script.task.py: rc=128, stderr=\'fatal error\'; treating as no changes' in mock_logging.warning.call_args[0][0]

    def test_has_unstaged_changes_new_file(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value.returncode = 1  # new file not in HEAD
        result = git.has_unstaged_changes(mock_repo, 'newfile', 'task')
        assert result is True

    def test_has_unstaged_changes_unexpected_stderr(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='pager error message'
        )
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False
        mock_logging.warning.assert_called_once()
        assert 'Unexpected git diff result' in mock_logging.warning.call_args[0][0]

    def test_has_unstaged_changes_untracked_file(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, ['git', 'show', 'HEAD:script.task.py'])
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            result = git.has_unstaged_changes(mock_repo, 'script', 'task')
            assert result is True
        mock_logging.debug.assert_any_call('File script.task.py is not committed, considered as having unstaged changes')

# Additional edge cases can be added as needed
