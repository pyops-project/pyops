import pytest
import subprocess
import os
from unittest.mock import Mock
from app.lib import git
from app.lib import files

@pytest.fixture
def integration_repo(tmp_path, monkeypatch):
    """Fixture for a real Git repo with remote for push tests."""
    # Mock config to use tmp_path for git base
    test_git_base = tmp_path / 'git_base'
    test_git_base.mkdir()
    test_venv = tmp_path / 'venv'
    test_pkg = tmp_path / 'pkg'
    
    # Mock the config global to avoid initialization check
    mock_config = Mock()
    mock_config.runner.return_value = {'git_pull_interval': 0, 'venv': str(test_venv), 'pkg': str(test_pkg)}
    monkeypatch.setattr(files, 'config', mock_config)
    monkeypatch.setattr(files, 'conf_data', lambda: {'git': str(test_git_base)})
    monkeypatch.setattr(files, 'conf_runner', lambda: {'git_pull_interval': 0, 'venv': str(test_venv), 'pkg': str(test_pkg)})

    # Create local repo. After the sets-management migration, set CRUD
    # lives in app/pyops/ scriptlets — for this fixture we just need
    # an empty dir; get_repo_path runs `git init` and writes the standard
    # .gitignore + README on first access.
    set_name = 'test_set'
    (test_git_base / set_name).mkdir()
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

    @pytest.mark.parametrize("kind,expected_payload", [
        # Iterate-on-local-code kinds — must see the unstaged draft.
        ("debug", "drafted"),
        ("draft", "drafted"),
        ("test", "drafted"),
        ("testdebug", "drafted"),
        # Production kinds — must see the committed snapshot.
        ("task", "committed"),
        ("service", "committed"),
    ])
    def test_load_lib_reads_unstaged_for_iterate_kinds(self, integration_repo, kind, expected_payload):
        """Regression test for the alignment of `loadLib`'s unstaged
        set with `loadJob`'s. Before the fix, `test` and `testdebug`
        were missing from `loadLib`'s set, so a drafted library was
        invisible to those kinds even though the consuming task's body
        was already read unstaged — silent run-against-wrong-code.

        Drives the real flow through `app.lib.job.loadLib` against a
        real git repo with one committed lib version and an unstaged
        edit; the returned source must match the unstaged copy for
        debug/draft/test/testdebug, and the committed copy for
        task/service."""
        from app.lib import job as job_lib
        # Commit lib v1, then overwrite with v2 (unstaged).
        file_path = os.path.join(integration_repo, 'mylib.library.py')
        with open(file_path, 'w') as f:
            f.write('X = "committed"\n')
        subprocess.run(['git', 'add', 'mylib.library.py'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add mylib v1'], cwd=integration_repo, check=True)
        subprocess.run(['git', 'push'], cwd=integration_repo, check=True)
        with open(file_path, 'w') as f:
            f.write('X = "drafted"\n')

        code = job_lib.loadLib("test_set", "mylib", kind, git)
        assert f'X = "{expected_payload}"' in code, (
            f"kind={kind!r}: expected payload {expected_payload!r} in {code!r}"
        )

    @pytest.mark.parametrize("dirty_url,clean_expected", [
        ("https://bot:t0k3n@example.invalid/group/myrepo.git",
         "https://example.invalid/group/myrepo.git"),
        ("http://u:p@127.0.0.1:8080/g/r.git",
         "http://127.0.0.1:8080/g/r.git"),
    ])
    def test_normalize_origin_url_strips_embedded_credentials(self, integration_repo, tmp_path, dirty_url, clean_expected):
        """Regression test for the legacy-credentials migration: real
        git repo with origin URL `https://user:pass@host/...`,
        normalize_origin_url runs, origin URL ends up clean. Exercises
        the real `git remote get-url` + `git remote set-url` flow via
        the subprocess wrapper in set.library.py."""
        # Set origin to the dirty URL using the real git binary.
        subprocess.run(['git', 'remote', 'set-url', 'origin', dirty_url],
                       cwd=integration_repo, check=True)
        # Sanity: real git agrees the URL is dirty.
        actual = subprocess.check_output(
            ['git', 'remote', 'get-url', 'origin'], cwd=integration_repo, text=True,
        ).strip()
        assert actual == dirty_url

        # Exec set.library.py in a namespace shaped like a pyops job.
        pyops_set_dir = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
        ))
        glob = {
            "__name__": "set_library_loaded",
            "appspace": str(tmp_path / "app"),
            "config": {
                "data": {"git": str(tmp_path / "git")},
                "runner": {"venv": str(tmp_path / "venv"), "pkg": str(tmp_path / "pkg")},
            },
            "name": None,
        }
        (tmp_path / "app").mkdir(exist_ok=True)
        with open(os.path.join(pyops_set_dir, "set.library.py")) as f:
            exec(compile(f.read(), "set.library.py", "exec"), glob)

        glob["normalize_origin_url"](integration_repo)

        normalised = subprocess.check_output(
            ['git', 'remote', 'get-url', 'origin'], cwd=integration_repo, text=True,
        ).strip()
        assert normalised == clean_expected

    def test_normalize_origin_url_no_remote_returns_none(self, tmp_path):
        """No-remote repo: `git remote get-url origin` exits non-zero;
        normalize_origin_url must absorb that and return None instead
        of propagating the CalledProcessError into the calling task."""
        bare = tmp_path / "scratch"
        bare.mkdir()
        subprocess.run(['git', 'init', '-b', 'main'], cwd=bare, check=True, capture_output=True)
        pyops_set_dir = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
        ))
        glob = {
            "__name__": "set_library_loaded",
            "appspace": str(tmp_path / "app"),
            "config": {
                "data": {"git": str(tmp_path / "git")},
                "runner": {"venv": str(tmp_path / "venv"), "pkg": str(tmp_path / "pkg")},
            },
            "name": None,
        }
        (tmp_path / "app").mkdir(exist_ok=True)
        with open(os.path.join(pyops_set_dir, "set.library.py")) as f:
            exec(compile(f.read(), "set.library.py", "exec"), glob)

        result = glob["normalize_origin_url"](str(bare))
        assert result is None

    def test_list_scriptlets_stamps_has_unstaged_against_real_git_status(self, integration_repo):
        """Integration coverage for the Manager UI dirty indicator:
        clean + dirty scriptlets in the same set, real
        `git status --porcelain` driven through `_per_set_dirty_paths`
        and `_stamp_has_unstaged`. The end-state list must carry the
        flag matching what an operator would expect from looking at
        the working tree."""
        # Two scriptlets: clean.task.py committed clean; dirty.task.py
        # committed then modified (unstaged change).
        clean_file = os.path.join(integration_repo, "clean.task.py")
        dirty_file = os.path.join(integration_repo, "dirty.task.py")
        with open(clean_file, "w") as f:
            f.write("X = 1\n")
        with open(dirty_file, "w") as f:
            f.write("Y = 1\n")
        subprocess.run(["git", "add", "."], cwd=integration_repo, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=integration_repo, check=True)
        subprocess.run(["git", "push"], cwd=integration_repo, check=True)
        # Now make dirty.task.py unstaged.
        with open(dirty_file, "a") as f:
            f.write("# unstaged edit\n")
        # Plus a brand-new untracked file — must also surface as dirty.
        untracked = os.path.join(integration_repo, "fresh.task.py")
        with open(untracked, "w") as f:
            f.write("Z = 1\n")

        listing = git.list_scriptlets()
        same_set = [e for e in listing if e["set"] == "test_set"]
        by_name = {e["name"]: e for e in same_set}
        assert "clean" in by_name and by_name["clean"]["has_unstaged"] is False
        assert "dirty" in by_name and by_name["dirty"]["has_unstaged"] is True
        assert "fresh" in by_name and by_name["fresh"]["has_unstaged"] is True

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
