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
    def test_pull_happy_path(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging, mock_fcntl):
        mock_run, mock_check = mock_subprocess
        # Calls (after the pull() reorg): _check remote, direct check_output tag,
        # _check branch, _check pre, _check ls-remote, run fetch, _check post,
        # _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote _check
            b'',  # tag direct
            b'main\n',  # branch _check
            b'abc123\n',  # pre _check
            b'def456\n',  # ls-remote _check
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
        result = git.pull(mock_repo)
        assert result is True
        mock_logging.info.assert_any_call('Reset test_set to origin/HEAD: new commit msg')
        assert any(call.args[0] == ['git', 'fetch'] for call in mock_run.call_args_list)
        assert any(call.args[0] == ['git', 'reset', '--soft', 'origin/main'] for call in mock_run.call_args_list)
        # Assert single batch checkout call
        checkout_calls = [call for call in mock_run.call_args_list if call.args[0][0] == 'git' and call.args[0][1] == 'checkout']
        assert len(checkout_calls) == 1
        assert checkout_calls[0].args[0] == ['git', 'checkout', 'HEAD', '--', 'file1.py', 'file2.html', 'newfile.py']
        mock_logging.info.assert_any_call('Added 3 new files to test_set working directory')

    def test_pull_no_changes(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check branch, _check pre, _check ls-remote (match), no fetch
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'abc123\n',  # ls-remote
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull(mock_repo)
        assert result is True

    def test_pull_no_remote(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [b'']  # remote _check empty
        result = git.pull(mock_repo)
        assert result is False
        mock_logging.info.assert_called_with('No remote for test_set; skipping reset')

    def test_pull_on_tag(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag non-empty, _check describe succeeds
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'tag123\n',  # tag
            b'tag123\n',  # describe
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull(mock_repo)
        assert result is False
        mock_logging.info.assert_called_with('On tag in test_set; skipping reset')

    def test_pull_dirty_repo(self, mock_repo, mock_subprocess, mock_files_conf):
        # Note: Code always resets soft, preserves unstaged
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status dirty, _check branch, _check pre, _check ls-remote, run fetch, _check post, _check log, _check ls-tree (no new)
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b' M file.py\n',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # ls-remote
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file1.py\n',  # ls-tree
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull(mock_repo)
        assert result is True
        # Reset still called despite dirty

    def test_pull_retry_success(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check status, _check branch, _check pre, _check ls-remote, run fetch (retry 2 fail, 3 success), _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # ls-remote
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
        result = git.pull(mock_repo)
        assert result is True
        assert fetch_count[0] == 3
        assert mock_logging.warning.call_count == 2  # 2 retries

    def test_pull_final_failure(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'',  # status
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # ls-remote
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
            git.pull(mock_repo)
        assert fetch_count[0] == 3
        mock_logging.error.assert_called()

    def test_pull_new_file_failure(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check branch, _check pre, _check ls-remote, run fetch, _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456\n',  # ls-remote
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
        result = git.pull(mock_repo)
        assert result is True
        mock_logging.warning.assert_called()

    def test_pull_skip_fetch_match(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'main\n',  # branch
            b'abc123\n',  # pre_head
            b'abc123 abc123\n',  # ls-remote --heads origin main
        ]
        result = git.pull(mock_repo)
        assert result is True
        # No fetch called
        assert not any('fetch' in str(call) for call in mock_run.call_args_list)
        mock_logging.debug.assert_any_call('Up-to-date: local HEAD matches origin/main for test_set')

    def test_pull_skip_fetch_mismatch(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check branch, _check pre, _check ls-remote, run fetch, _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'main\n',  # branch
            b'abc123\n',  # pre
            b'def456 def456\n',  # ls-remote different
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file1.py\n',  # ls-tree
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull(mock_repo)
        assert result is True
        assert any('fetch' in str(call) for call in mock_run.call_args_list)

    def test_pull_skip_fetch_lsremote_error(self, mock_repo, mock_subprocess, mock_files_conf, mock_logging):
        mock_run, mock_check = mock_subprocess
        # Calls: _check remote, direct tag, _check branch, _check pre, _check ls-remote fails, run fetch, _check post, _check log, _check ls-tree
        mock_check.side_effect = [
            b'origin\n',  # remote
            b'',  # tag
            b'main\n',  # branch
            b'abc123\n',  # pre
            subprocess.CalledProcessError(1, [], output=b''),  # ls-remote fails
            b'def456\n',  # post
            b'new commit msg\n',  # log
            b'file1.py\n',  # ls-tree
        ]
        mock_run.side_effect = lambda cmd, *args, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0)
        result = git.pull(mock_repo)
        assert result is True
        assert any('fetch' in str(call) for call in mock_run.call_args_list)
        mock_logging.debug.assert_any_call('ls-remote failed for test_set; proceeding to fetch')

    def test_pull_no_git_dir(self, tmp_path, mock_files_conf):
        repo = str(tmp_path / 'no_git')
        result = git.pull(repo)
        assert result is False

class TestRepoPathAndFiles:
    def test_get_repo_path_directory_without_git_is_no_set(self, tmp_path, mock_files_conf):
        """A plain directory under the git base is not a set (no .git): a read
        path neither initialises nor commits anything in it."""
        plain = tmp_path / '.cache/git/plain'
        plain.mkdir(parents=True)
        with patch('subprocess.run') as mock_run, pytest.raises(git.SetNotFoundError, match="Set 'plain' does not exist"):
            git.get_repo_path('plain')
        mock_run.assert_not_called()
        assert list(plain.iterdir()) == []

    def test_get_repo_path_lock_file_is_no_set(self, tmp_path, mock_files_conf):
        """repo_lock leaves a `<git base>/<set>.lock` file per set: its name is
        no valid set name (400, not a 500 'Not a directory'), and a plain file
        under a valid name is no set either (404)."""
        (tmp_path / '.cache/git/s1.lock').write_text('')
        (tmp_path / '.cache/git/s2').write_text('')
        with pytest.raises(git.InvalidSetNameError):
            git.get_repo_path('s1.lock')
        with pytest.raises(git.SetNotFoundError):
            git.get_repo_path('s2')

    def test_get_repo_path_directory_not_exist(self, mock_files_conf):
        with pytest.raises(git.SetNotFoundError, match="Set 'nonexistent' does not exist"):
            git.get_repo_path('nonexistent')

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

    def test_get_file_path_docs_markdown(self, mock_repo):
        # Non-README markdown goes under docs/{name}.md
        file_path = git.get_file_path(mock_repo, "intro", "markdown")
        assert file_path.endswith(os.path.join("docs", "intro.md"))

    @pytest.mark.parametrize('type_, name', [('invalid', 'script')])
    def test_get_file_path_invalid_type(self, mock_repo, type_, name):
        with pytest.raises(ValueError):
            git.get_file_path(mock_repo, name, type_)

    @pytest.mark.parametrize('filename, expected', [
        ('script.task.py', 'script'),
        ('view.html', 'view'),
        ('README.md', 'README'),
        ('mylib.js', 'mylib'),
        ('docs/intro.md', 'intro'),
        ('docs/api_reference.md', 'api_reference'),
    ])
    def test_get_name_from_file(self, filename, expected):
        assert git.get_name_from_file(filename) == expected

    @pytest.mark.parametrize('filename, expected', [
        ('script.task.py', 'task'),
        ('view.html', 'view'),
        ('README.md', 'markdown'),
        ('mylib.js', 'jslibrary'),
        ('docs/intro.md', 'markdown'),
        ('docs/api_reference.md', 'markdown'),
    ])
    def test_infer_type_from_filename(self, filename, expected):
        assert git.infer_type_from_filename(filename) == expected

    @pytest.mark.parametrize('filename', ['invalid.py', 'script.invalid.py'])
    def test_infer_type_from_filename_invalid(self, filename):
        with pytest.raises(ValueError):
            git.infer_type_from_filename(filename)

class TestListAndUnique:
    def test_list_scriptlets(self, tmp_path, mock_files_conf, mock_subprocess):
        # Mock sets dir with repos + per-path listing (top-level vs docs/)
        def listdir_side_effect(path):
            if '.cache/git' in path:
                return ['set1']
            if path.endswith('docs') or '/docs' in path:
                return ['intro.md', 'api.md', 'irrelevant.txt']
            return ['script.task.py', 'view.html', 'README.md', 'invalid.txt']

        def isdir_side_effect(path):
            # Treat repo and docs subdir as directories; everything else
            # (specific files we don't care about) is irrelevant since the
            # other code paths don't call isdir on them here.
            return True

        with patch('os.listdir', side_effect=listdir_side_effect), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', side_effect=isdir_side_effect), \
             patch('app.lib.git.get_repo_path', return_value=str(tmp_path / 'repo')):
            result = git.list_scriptlets()
            # Every entry carries `has_unstaged` (False here — mock_subprocess
            # returns empty `git status --porcelain` output).
            expected = [
                {'set': 'set1', 'name': 'script', 'type': 'task', 'has_unstaged': False},
                {'set': 'set1', 'name': 'view', 'type': 'view', 'has_unstaged': False},
                {'set': 'set1', 'name': 'README', 'type': 'markdown', 'has_unstaged': False},
                {'set': 'set1', 'name': 'intro', 'type': 'markdown', 'has_unstaged': False},
                {'set': 'set1', 'name': 'api', 'type': 'markdown', 'has_unstaged': False},
            ]
            for e in expected:
                assert e in result
            assert len(result) == len(expected)

    def test_list_scriptlets_stamps_has_unstaged_for_dirty_files(self, tmp_path, mock_files_conf):
        """When `git status --porcelain` lists a file, its scriptlet
        entry must carry `has_unstaged=True`; clean entries stay False.
        Drives the dirty-set parse + path-match logic in one shot."""
        def listdir_side_effect(path):
            if '.cache/git' in path:
                return ['set1']
            if path.endswith('docs') or '/docs' in path:
                return []
            return ['clean.task.py', 'dirty.task.py']

        with patch('os.listdir', side_effect=listdir_side_effect), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('app.lib.git.get_repo_path', return_value=str(tmp_path / 'repo')), \
             patch('subprocess.check_output',
                   return_value=b" M dirty.task.py\n"):
            result = git.list_scriptlets()
            statuses = {e['name']: e['has_unstaged'] for e in result}
            assert statuses == {'dirty': True, 'clean': False}

    def test_list_scriptlets_handles_porcelain_rename(self, tmp_path, mock_files_conf):
        """Renamed entries (`R  old -> new`) flag both endpoints as
        dirty so a UI lookup by either current or previous name shows
        the warning."""
        def listdir_side_effect(path):
            if '.cache/git' in path:
                return ['set1']
            if path.endswith('docs') or '/docs' in path:
                return []
            return ['new_name.task.py']

        with patch('os.listdir', side_effect=listdir_side_effect), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('app.lib.git.get_repo_path', return_value=str(tmp_path / 'repo')), \
             patch('subprocess.check_output',
                   return_value=b"R  old_name.task.py -> new_name.task.py\n"):
            result = git.list_scriptlets()
            assert any(e['name'] == 'new_name' and e['has_unstaged'] for e in result)

    def test_list_scriptlets_git_status_failure_yields_clean(self, tmp_path, mock_files_conf):
        """If `git status` errors (locked repo, no .git, …), the dirty
        set is empty and every entry shows as clean rather than the
        listing 500-ing. Worst case: a brief window where the Manager
        UI under-reports drafts; the operator triggers refresh."""
        def listdir_side_effect(path):
            if '.cache/git' in path:
                return ['set1']
            if path.endswith('docs') or '/docs' in path:
                return []
            return ['x.task.py']

        with patch('os.listdir', side_effect=listdir_side_effect), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('app.lib.git.get_repo_path', return_value=str(tmp_path / 'repo')), \
             patch('subprocess.check_output',
                   side_effect=subprocess.CalledProcessError(128, ["git", "status"])):
            result = git.list_scriptlets()
            assert result == [{'set': 'set1', 'name': 'x', 'type': 'task', 'has_unstaged': False}]

    def test_check_name_unique(self, mock_repo):
        assert git.check_name_unique(mock_repo, 'unique') is True
        with open(os.path.join(mock_repo, 'unique.task.py'), 'w') as f:
            f.write('pass\n')
        assert git.check_name_unique(mock_repo, 'unique') is False

    def test_check_name_unique_matches_exact_names(self, mock_repo):
        """A file of another name that merely starts with `<name>.` (here
        `c.x.task.py`, cloned in) does not take the name `c`."""
        with open(os.path.join(mock_repo, 'c.x.task.py'), 'w') as f:
            f.write('pass\n')
        assert git.check_name_unique(mock_repo, 'c') is True

    @staticmethod
    def _set_with_docs_and_data(repo):
        os.makedirs(os.path.join(repo, 'docs'))
        os.makedirs(os.path.join(repo, 'data'))
        with open(os.path.join(repo, 'docs', 'guide.md'), 'w') as f:
            f.write('# Guide\n')
        with open(os.path.join(repo, 'data', 'cfg.json'), 'w') as f:
            f.write('{}\n')
        with open(os.path.join(repo, 'job.task.py'), 'w') as f:
            f.write('pass\n')

    def test_check_name_unique_spans_docs_and_data(self, mock_repo):
        """Scriptlets, docs/ pages and data/ files share one namespace per set."""
        from app.lib import git
        self._set_with_docs_and_data(mock_repo)
        assert git.check_name_unique(mock_repo, 'guide') is False
        assert git.check_name_unique(mock_repo, 'cfg') is False
        assert git.check_name_unique(mock_repo, 'job') is False
        assert git.check_name_unique(mock_repo, 'fresh') is True

    def test_resolve_scriptlet_resolves_location(self, mock_repo):
        """Repo-relative paths and types across top level, README.md, docs/ and data/."""
        from app.lib import git
        self._set_with_docs_and_data(mock_repo)
        resolved = {name: git.resolve_scriptlet(mock_repo, name) for name in ('README', 'guide', 'cfg', 'job')}
        assert {n: (r.rel_path, r.type) for n, r in resolved.items()} == {
            'README': ('README.md', 'markdown'),
            'guide': ('docs/guide.md', 'markdown'),
            'cfg': ('data/cfg.json', 'data'),
            'job': ('job.task.py', 'task'),
        }
        assert resolved['cfg'].ext == 'json'
        assert resolved['job'].path == os.path.join(mock_repo, 'job.task.py')
        with pytest.raises(git.ScriptletNotFoundError, match="Scriptlet test_set::fresh not existing"):
            git.resolve_scriptlet(mock_repo, 'fresh')

    def test_resolve_scriptlet_by_type(self, mock_repo):
        """With a type, the file of that type wins over another of the same
        name; a name of another type only is no scriptlet of that type."""
        from app.lib import git
        for filename in ('c.task.py', 'c.controller.py', 'v.html'):
            with open(os.path.join(mock_repo, filename), 'w') as f:
                f.write('x\n')
        assert git.resolve_scriptlet(mock_repo, 'c', 'controller').rel_path == 'c.controller.py'
        assert git.resolve_scriptlet(mock_repo, 'c', ('library', 'task')).rel_path == 'c.task.py'
        with pytest.raises(git.WrongScriptletTypeError, match="Scriptlet test_set::v is not a controller"):
            git.resolve_scriptlet(mock_repo, 'v', 'controller')

    def test_resolve_scriptlet_matches_exact_filenames(self, mock_repo):
        """`c.x.task.py` (another, invalid name) never answers for `c`,
        whatever the directory order — the old prefix scans picked it."""
        from app.lib import git
        for filename in ('c.x.task.py', 'c.controller.py'):
            with open(os.path.join(mock_repo, filename), 'w') as f:
                f.write('x\n')
        with patch('os.listdir', return_value=['c.x.task.py', 'c.controller.py']):
            assert git.resolve_scriptlet(mock_repo, 'c').rel_path == 'c.controller.py'
            assert git.resolve_scriptlet(mock_repo, 'c', 'controller').type == 'controller'

    @pytest.mark.parametrize('name', ['', '../../../../etc/pyops', 'a/b', 't.x', 'a\x00b', 'sp ace', 'ü', 'x\n'])
    def test_resolve_scriptlet_rejects_invalid_names(self, mock_repo, tmp_path, name):
        """A name outside [A-Za-z0-9_-]+ never reaches the filesystem — not
        even a traversal to an existing data file outside the set."""
        from app.lib import git
        os.makedirs(os.path.join(mock_repo, 'data'))
        (tmp_path / 'pyops.json').write_text('{}')
        with pytest.raises(git.InvalidScriptletNameError, match="Invalid scriptlet name"):
            git.resolve_scriptlet(mock_repo, name)
        with pytest.raises(git.InvalidScriptletNameError):
            git.get_file_path(mock_repo, name, 'data')
        with pytest.raises(git.InvalidScriptletNameError):
            git.check_name_unique(mock_repo, name)

    def test_get_file_path_rejects_unknown_type(self, mock_repo):
        from app.lib import git
        with pytest.raises(ValueError, match="Invalid type: bogus"):
            git.get_file_path(mock_repo, 'x', 'bogus')

    def test_get_file_path_data_uses_existing_format(self, mock_repo):
        """A data file resolves to its on-disk format; a new one defaults to .yaml."""
        from app.lib import git
        self._set_with_docs_and_data(mock_repo)
        assert git.get_file_path(mock_repo, 'cfg', 'data') == os.path.join(mock_repo, 'data', 'cfg.json')
        assert git.get_file_path(mock_repo, 'new', 'data') == os.path.join(mock_repo, 'data', 'new.yaml')

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



class TestDeleteFileUnstaged:
    def test_delete_file_unstaged_removes_from_working_tree(self, mock_repo, mock_files_conf, mock_fcntl, tmp_path):
        """The new delete contract: working-tree only, no git rm, no commit, no push."""
        # Create a real file on disk inside the mock repo so the test
        # can observe the removal.
        os.makedirs(mock_repo, exist_ok=True)
        file_path = os.path.join(mock_repo, 'test.py')
        with open(file_path, 'w') as f:
            f.write("pass\n")
        assert os.path.exists(file_path)

        git.delete_file_unstaged(mock_repo, file_path)

        assert not os.path.exists(file_path)

    def test_delete_file_unstaged_missing_file_is_noop(self, mock_repo, mock_files_conf, mock_fcntl):
        """Idempotent: deleting an already-missing file does not raise."""
        os.makedirs(mock_repo, exist_ok=True)
        file_path = os.path.join(mock_repo, 'ghost.py')
        assert not os.path.exists(file_path)
        git.delete_file_unstaged(mock_repo, file_path)


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
        mock_check.return_value = b''  # clean
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_dirty(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_check.return_value = b' M script.task.py\n'  # modified
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is True

    def test_has_unstaged_changes_error(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(128, [], stderr='fatal error')
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_new_file(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_check.return_value = b'?? newfile.task.py\n'  # untracked
        result = git.has_unstaged_changes(mock_repo, 'newfile', 'task')
        assert result is True

    def test_has_unstaged_changes_unexpected_stderr(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_check.side_effect = subprocess.CalledProcessError(1, [], stderr='pager error message')
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is False

    def test_has_unstaged_changes_untracked_file(self, mock_repo, mock_subprocess, mock_files_conf):
        mock_run, mock_check = mock_subprocess
        mock_check.return_value = b'?? script.task.py\n'  # untracked
        result = git.has_unstaged_changes(mock_repo, 'script', 'task')
        assert result is True


class TestPyopsSetResolver:
    """get_repo_path / read_content special-case for the pyops-managed
    `pyops` set: resolves to <project_root>/app/pyops/ (not data/git/),
    no .git init, direct FS reads."""

    def test_get_repo_path_pyops_returns_app_pyops(self, tmp_path):
        fake_pyops = tmp_path / "app" / "pyops"
        fake_pyops.mkdir(parents=True)
        with patch.object(files, "bundled_set_path", str(fake_pyops)):
            result = git.get_repo_path("pyops")
        assert result == str(fake_pyops)

    def test_get_repo_path_pyops_raises_if_missing(self, tmp_path):
        missing = tmp_path / "app" / "pyops_missing"
        with patch.object(files, "bundled_set_path", str(missing)):
            with pytest.raises(git.SetNotFoundError):
                git.get_repo_path("pyops")

    def test_get_repo_path_pyops_skips_git_init(self, tmp_path):
        fake_pyops = tmp_path / "app" / "pyops"
        fake_pyops.mkdir(parents=True)
        with patch.object(files, "bundled_set_path", str(fake_pyops)), \
             patch("app.lib.git._call") as mock_call:
            git.get_repo_path("pyops")
        # Pyops set never gets `git init` / commit dance
        mock_call.assert_not_called()
        assert not (fake_pyops / ".git").exists()

    def test_read_content_pyops_direct_fs_read(self, tmp_path):
        fake_pyops = tmp_path / "app" / "pyops"
        fake_pyops.mkdir(parents=True)
        (fake_pyops / "set_clone.task.py").write_text("print('hi')\n")
        with patch.object(files, "bundled_set_path", str(fake_pyops)):
            # committed=True/strict=True are intentionally ignored for the pyops set
            content = git.read_content(str(fake_pyops), "set_clone", "task", committed=True, strict=True)
        assert content == "print('hi')\n"

    def test_read_content_pyops_missing_raises_filenotfound(self, tmp_path):
        fake_pyops = tmp_path / "app" / "pyops"
        fake_pyops.mkdir(parents=True)
        with patch.object(files, "bundled_set_path", str(fake_pyops)):
            with pytest.raises(FileNotFoundError):
                git.read_content(str(fake_pyops), "no_such", "task", committed=False)


class TestSetNameValidation:
    """get_repo_path only resolves names of a directory directly under the
    git base: "" / "." resolve to the git base itself, ".." to its parent,
    a separator to any other directory — all of which it would git-init.
    A NUL byte cannot be part of a path at all."""

    @pytest.mark.parametrize("name", ["", ".", "..", "../outside", "nul\x00byte"])
    def test_get_repo_path_rejects_invalid_name_without_touching_fs(self, tmp_path, mock_files_conf, name):
        from app.lib import git as git_lib
        (tmp_path / ".cache" / "outside").mkdir()
        before = sorted(tmp_path.rglob("*"))
        with pytest.raises(git_lib.InvalidSetNameError, match="Invalid set name"):
            git_lib.get_repo_path(name)
        assert sorted(tmp_path.rglob("*")) == before

    @pytest.mark.parametrize("name", ["ops.tools", "a b", "a'b", 'a"b', "x<y", "ü", "s1\n", "s1.lock", 5, None])
    def test_validate_set_name_rejects_outside_charset(self, name):
        """Set names are [A-Za-z0-9_-]+ — the charset of `#!{set::name}`: a
        dotted name (e.g. from cloning `ops.tools.git`), markup or quote
        characters, non-ASCII letters, a trailing newline and non-strings
        are refused."""
        from app.lib import git as git_lib
        with pytest.raises(git_lib.InvalidSetNameError, match="Invalid set name"):
            git_lib.validate_set_name(name)

    @pytest.mark.parametrize("name", ["s1", "my_set", "My-Set-2", "_", "-"])
    def test_validate_set_name_accepts_charset(self, name):
        from app.lib import git as git_lib
        git_lib.validate_set_name(name)
        git_lib.validate_scriptlet_name(name)

    def test_list_sets_is_the_one_set_rule(self, tmp_path, mock_files_conf):
        """A set is a directory with .git and a valid name; lock files, plain
        directories, invalid names and the bundled set's name are not."""
        from app.lib import git as git_lib
        base = tmp_path / ".cache" / "git"
        for name in ("s1", "s2", "bad.name", "pyops"):
            (base / name / ".git").mkdir(parents=True)
        (base / "plain").mkdir()
        (base / "s1.lock").write_text("")
        assert git_lib.list_sets() == ["s1", "s2"]


# Additional edge cases can be added as needed
