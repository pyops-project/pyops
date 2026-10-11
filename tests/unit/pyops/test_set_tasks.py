"""Behaviour of the pyops-set tasks that act on a set's git repository —
set_create, set_pull, set_switch, set_commit_drafts, set_discard_drafts
and set_remove — run as the job runner runs them (`__name__ ==
"__main__"`, the job parameter bound into the globals) against real
repositories and bare remotes under tmp_path. No pip is involved.

These are the in-process twins of tests/e2e/test_sets_via_controller.py,
which drives the same tasks through the Sets view and a running server."""

import fcntl
import os

import pytest


# Tasks acting on an existing set (set_update_deps too; its own behaviour
# is covered in test_set_update_deps.py).
EXISTING_SET_TASKS = [
    "set_pull",
    "set_switch",
    "set_remove",
    "set_update_deps",
    "set_commit_drafts",
    "set_discard_drafts",
]


@pytest.fixture
def remote_clone(hermetic_git, tmp_path):
    """Factory: `remote_clone(remote)` clones the bare `remote` into a
    second working copy (another operator's), with a committer identity.
    Returns `push(files=None, branch=None)`: commits `files` ({path:
    content}) there and pushes, on `branch` when given (created from the
    remote's main). Returns the pushed commit."""
    git = hermetic_git

    def clone(remote):
        work = tmp_path / "other"
        git("clone", str(remote), str(work))
        git("config", "user.name", "Other", cwd=work)
        git("config", "user.email", "other@example.com", cwd=work)

        def push(files=None, branch=None):
            if branch:
                git("checkout", "-b", branch, cwd=work)
            for rel, content in (files or {}).items():
                (work / rel).write_text(content)
            git("add", ".", cwd=work)
            git("commit", "--allow-empty", "-m", "remote change", cwd=work)
            git("push", "origin", branch or "main", cwd=work)
            return git("rev-parse", "HEAD", cwd=work)

        return push

    return clone


def _record_run_cmd(glob):
    calls = []
    glob["run_cmd"] = lambda cmd, cwd=None, check=True: calls.append(list(cmd))
    return calls


class TestRefusals:

    @pytest.mark.parametrize("task", ["set_create"] + EXISTING_SET_TASKS)
    def test_missing_name(self, pyops_job, task):
        with pytest.raises(ValueError, match="^name is required$"):
            pyops_job(task, run=True, name="", branch="main")

    @pytest.mark.parametrize("task, message", [
        ("set_create", "'pyops' is reserved for pyops-managed scriptlets"),
        ("set_pull", "'pyops' is not pullable"),
        ("set_switch", "'pyops' is pyops-managed; branch switching is not supported"),
        ("set_remove", "'pyops' is pyops-managed and cannot be removed"),
        ("set_commit_drafts", "'pyops' is write-protected"),
        ("set_discard_drafts", "'pyops' is write-protected"),
    ])
    def test_bundled_set_refused(self, pyops_job, task, message):
        with pytest.raises(ValueError) as exc:
            pyops_job(task, run=True, name="pyops", branch="main")
        assert str(exc.value) == message

    def test_switch_without_branch(self, pyops_job, make_set, hermetic_git):
        target = make_set("s1")
        with pytest.raises(ValueError, match="^branch is required$"):
            pyops_job("set_switch", run=True, name="s1", branch="")
        assert hermetic_git("branch", "--show-current", cwd=target) == "main"

    @pytest.mark.parametrize("task", EXISTING_SET_TASKS)
    def test_unknown_set_refused(self, pyops_job, task):
        with pytest.raises(ValueError, match=r"^Set 'ghost' not found\.$"):
            pyops_job(task, run=True, name="ghost", branch="main")


class TestCreate:

    def test_creates_set_with_initial_commit(self, pyops_job, pyops_install, hermetic_git, capfd):
        pyops_job("set_create", run=True, name="fresh", git_name="Ops Bot", git_email="ops@example.com")

        target = pyops_install["git"] / "fresh"
        git = hermetic_git
        assert git("log", "--format=%an <%ae>|%s", cwd=target) == "Ops Bot <ops@example.com>|Initial commit for fresh"
        assert git("ls-tree", "--name-only", "HEAD", cwd=target).splitlines() == [".gitignore", "README.md"]
        assert (target / ".gitignore").read_text() == "*.bck\n"
        assert (target / "README.md").read_text() == "# fresh\n"
        assert git("status", "--porcelain", cwd=target) == ""
        assert "Created set 'fresh'" in capfd.readouterr().out

    def test_default_identity_persisted(self, pyops_job, pyops_install, hermetic_git):
        """With no identity given, a default one is set in the repository —
        the initial commit, and a later commit-drafts, need one."""
        pyops_job("set_create", run=True, name="fresh")
        target = pyops_install["git"] / "fresh"
        assert hermetic_git("config", "user.name", cwd=target) == "pyops"
        assert hermetic_git("config", "user.email", cwd=target) == "pyops@localhost"
        assert hermetic_git("log", "--format=%an <%ae>", cwd=target) == "pyops <pyops@localhost>"

    def test_existing_set_refused(self, pyops_job, make_set, hermetic_git):
        target = make_set("taken")
        head = hermetic_git("rev-parse", "HEAD", cwd=target)
        with pytest.raises(ValueError, match=r"^Set 'taken' already exists\.$"):
            pyops_job("set_create", run=True, name="taken")
        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head


class TestPull:

    def test_resets_to_remote_and_restores_new_scriptlets(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        pushed = remote_clone(remote)(files={"fresh.task.py": "print('new')\n"})

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == pushed
        assert (target / "fresh.task.py").read_text() == "print('new')\n"
        out = capfd.readouterr().out
        assert "Restored 1 file(s) from HEAD." in out
        assert "Reset 's1' to origin/main." in out

    def test_up_to_date(self, pyops_job, make_set, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        head = hermetic_git("rev-parse", "HEAD", cwd=target)

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert "Already up-to-date with origin/main." in capfd.readouterr().out

    def test_drafts_kept_and_reset_skipped(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        head = hermetic_git("rev-parse", "HEAD", cwd=target)
        remote_clone(remote)(files={"fresh.task.py": "print('new')\n"})
        (target / "README.md").write_text("my draft\n")

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert (target / "README.md").read_text() == "my draft\n"
        assert "Working tree has unstaged changes — skipping reset." in capfd.readouterr().out

    def test_tag_pinned_set_not_reset(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        hermetic_git("tag", "v1", cwd=target)
        head = hermetic_git("rev-parse", "HEAD", cwd=target)
        remote_clone(remote)(files={"fresh.task.py": "print('new')\n"})

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert "HEAD is on a tag — skipping reset." in capfd.readouterr().out

    def test_tagged_set_off_tag_is_reset(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path):
        """Tags elsewhere in the history do not pin the set."""
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        hermetic_git("tag", "v0", "HEAD", cwd=target)
        hermetic_git("commit", "--allow-empty", "-m", "after v0", cwd=target)
        hermetic_git("push", "origin", "main", cwd=target)
        pushed = remote_clone(remote)(files={"fresh.task.py": "print('new')\n"})

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == pushed

    def test_set_without_remote(self, pyops_job, make_set, hermetic_git, capfd):
        target = make_set("s1")
        head = hermetic_git("rev-parse", "HEAD", cwd=target)

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert "No remote configured — nothing to pull." in capfd.readouterr().out

    def test_embedded_credentials_origin_migrated(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path):
        """A pre-3.3.0 clone keeps `user:token@` in its origin URL; the pull
        rewrites origin to the clean URL and then pulls through it (the
        clean https URL is mapped to the local bare remote by
        `url.<remote>.insteadOf`, so no network is involved)."""
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        hermetic_git("remote", "set-url", "origin", "https://bot:t0k3n@git.example/g/s1.git", cwd=target)
        with open(hermetic_git.config_path, "a") as f:
            f.write(f'[url "{remote}"]\n\tinsteadOf = https://git.example/g/s1.git\n')
        pushed = remote_clone(remote)(files={"fresh.task.py": "print('new')\n"})

        pyops_job("set_pull", run=True, name="s1")

        assert hermetic_git("config", "remote.origin.url", cwd=target) == "https://git.example/g/s1.git"
        assert hermetic_git("rev-parse", "HEAD", cwd=target) == pushed


class TestSwitch:

    def _branch(self, git, target):
        return git("branch", "--show-current", cwd=target)

    def test_existing_local_branch(self, pyops_job, make_set, hermetic_git, capfd):
        target = make_set("s1")
        hermetic_git("branch", "feature", cwd=target)

        pyops_job("set_switch", run=True, name="s1", branch="feature")

        assert self._branch(hermetic_git, target) == "feature"
        assert "Switched to existing local branch 'feature'." in capfd.readouterr().out

    def test_branch_only_on_origin_is_tracked(self, pyops_job, make_set, remote_clone, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        pushed = remote_clone(remote)(files={"wip.task.py": "pass\n"}, branch="wip")

        pyops_job("set_switch", run=True, name="s1", branch="wip")

        assert self._branch(hermetic_git, target) == "wip"
        assert hermetic_git("rev-parse", "HEAD", cwd=target) == pushed
        assert hermetic_git("rev-parse", "--abbrev-ref", "wip@{upstream}", cwd=target) == "origin/wip"
        assert "Checked out 'wip' from origin." in capfd.readouterr().out

    def test_new_branch_pushed_to_origin(self, pyops_job, make_set, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)

        pyops_job("set_switch", run=True, name="s1", branch="release")

        assert self._branch(hermetic_git, target) == "release"
        assert hermetic_git("rev-parse", "refs/heads/release", cwd=remote) == hermetic_git("rev-parse", "HEAD", cwd=target)
        assert "Created and pushed new branch 'release'." in capfd.readouterr().out

    def test_new_branch_without_remote(self, pyops_job, make_set, hermetic_git, capfd):
        target = make_set("s1")

        pyops_job("set_switch", run=True, name="s1", branch="release")

        assert self._branch(hermetic_git, target) == "release"
        assert "Created new branch 'release' (no remote to push to)." in capfd.readouterr().out

    def test_new_branch_kept_when_push_fails(self, pyops_job, make_set, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        hermetic_git("remote", "set-url", "origin", str(tmp_path / "remotes" / "gone.git"), cwd=target)

        pyops_job("set_switch", run=True, name="s1", branch="release")

        assert self._branch(hermetic_git, target) == "release"
        assert "Created 'release' locally; push failed:" in capfd.readouterr().out


class TestCommitDrafts:

    def test_no_drafts_is_noop(self, pyops_job, make_set, hermetic_git, capfd):
        target = make_set("s1")
        head = hermetic_git("rev-parse", "HEAD", cwd=target)

        pyops_job("set_commit_drafts", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert "No unstaged scriptlets — nothing to commit." in capfd.readouterr().out

    def test_commits_every_draft_in_one_commit(self, pyops_job, make_set, hermetic_git, capfd):
        target = make_set("s1")
        (target / "README.md").write_text("# s1, edited\n")
        (target / "new.task.py").write_text("print('new')\n")
        (target / "docs").mkdir()
        (target / "docs" / "guide.md").write_text("# Guide\n")

        pyops_job("set_commit_drafts", run=True, name="s1", user="bob", message="")

        git = hermetic_git
        assert git("log", "-1", "--format=%s", cwd=target) == "Commit drafts in s1 by bob"
        assert git("show", "--name-only", "--format=", "HEAD", cwd=target).splitlines() == [
            "README.md", "docs/guide.md", "new.task.py",
        ]
        assert git("status", "--porcelain", cwd=target) == ""
        assert "Committed 3 file(s); no remote configured — skipped push." in capfd.readouterr().out

    def test_commits_and_pushes_to_origin(self, pyops_job, make_set, hermetic_git, tmp_path, capfd):
        remote = tmp_path / "remotes" / "s1.git"
        target = make_set("s1", remote=remote)
        (target / "new.task.py").write_text("print('new')\n")

        pyops_job("set_commit_drafts", run=True, name="s1", message="  add new task  ")

        assert hermetic_git("log", "-1", "--format=%s", cwd=target) == "add new task"
        assert hermetic_git("rev-parse", "refs/heads/main", cwd=remote) == hermetic_git("rev-parse", "HEAD", cwd=target)
        assert "Committed and pushed 1 file(s) to origin." in capfd.readouterr().out

    def test_tag_pinned_set_refused(self, pyops_job, make_set, hermetic_git):
        target = make_set("s1")
        hermetic_git("tag", "v1", cwd=target)
        head = hermetic_git("rev-parse", "HEAD", cwd=target)
        (target / "new.task.py").write_text("print('new')\n")

        with pytest.raises(RuntimeError, match="HEAD is on a tag — refusing to commit."):
            pyops_job("set_commit_drafts", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert (target / "new.task.py").exists()


class TestDiscardDrafts:

    def test_clean_set_is_noop(self, pyops_job, make_set, capfd):
        make_set("s1")
        pyops_job("set_discard_drafts", run=True, name="s1")
        assert "Working tree clean — nothing to discard." in capfd.readouterr().out

    def test_restores_last_commit(self, pyops_job, make_set, hermetic_git, capfd):
        """Edited, staged and new drafts are gone, ignored files stay and
        HEAD does not move."""
        target = make_set("s1", files={"README.md": "# s1\n", ".gitignore": "*.bck\n"})
        head = hermetic_git("rev-parse", "HEAD", cwd=target)
        (target / "README.md").write_text("edited\n")
        (target / "staged.task.py").write_text("pass\n")
        hermetic_git("add", "staged.task.py", cwd=target)
        (target / "docs").mkdir()
        (target / "docs" / "page.md").write_text("# Page\n")
        (target / "README.md.bck").write_text("backup\n")

        pyops_job("set_discard_drafts", run=True, name="s1")

        assert hermetic_git("rev-parse", "HEAD", cwd=target) == head
        assert (target / "README.md").read_text() == "# s1\n"
        assert not (target / "staged.task.py").exists()
        assert not (target / "docs").exists()
        assert (target / "README.md.bck").read_text() == "backup\n"
        assert hermetic_git("status", "--porcelain", cwd=target) == ""
        assert "Discarded 3 draft change(s); set restored to the last commit." in capfd.readouterr().out


class TestRemove:

    def test_removes_repo_venv_and_pkg_under_locks(self, pyops_job, pyops_install, make_set):
        """The venv and pkg dirs go under the set's install lock (so a job
        installing into them finishes first), the repository under its
        repo lock; another set is untouched."""
        from types import SimpleNamespace
        target = make_set("s1")
        other = make_set("s2")
        for base in ("venv", "pkg"):
            for set_name in ("s1", "s2"):
                (pyops_install[base] / set_name / "bin").mkdir(parents=True)
        glob = pyops_job("set_remove", name="s1")
        lock_files = {
            "install": glob["os"].path.join(pyops_install["runner"]["venv"], ".locks", "s1.lock"),
            "repo": glob["os"].path.join(pyops_install["data"]["git"], "s1.lock"),
        }
        removed = []

        def held(lock_file):
            if not os.path.exists(lock_file):
                return False
            with open(lock_file, "a") as fd:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                return False

        real_rmtree = glob["shutil"].rmtree

        def rmtree(path):
            removed.append((str(path), sorted(k for k, f in lock_files.items() if held(f))))
            real_rmtree(path)

        glob["shutil"] = SimpleNamespace(rmtree=rmtree)
        glob["main"]()

        assert removed == [
            (str(pyops_install["venv"] / "s1"), ["install"]),
            (str(pyops_install["pkg"] / "s1"), ["install"]),
            (str(target), ["repo"]),
        ]
        assert not target.exists()
        assert other.is_dir()
        assert (pyops_install["venv"] / "s2").is_dir() and (pyops_install["pkg"] / "s2").is_dir()

    def test_set_without_venv_or_pkg(self, pyops_job, pyops_install, make_set, capfd):
        target = make_set("s1")
        pyops_job("set_remove", run=True, name="s1")
        assert not target.exists()
        out = capfd.readouterr().out
        assert "Removed git repo" in out and "Removed venv" not in out and "Removed pkg dir" not in out
        assert "Removed set 's1'." in out

    def test_uninitialised_set_removed(self, pyops_job, pyops_install, capfd):
        """A set directory without `.git` (not initialised) is removed like
        any set — the Sets view offers only Remove for it."""
        plain = pyops_install["git"] / "plain"
        plain.mkdir()
        (plain / "keep.txt").write_text("not initialised\n")
        pyops_job("set_remove", run=True, name="plain")
        assert not plain.exists()
        assert "Removed set 'plain'." in capfd.readouterr().out
