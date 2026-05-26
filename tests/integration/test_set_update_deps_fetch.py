"""Integration coverage for `app/pyops/set_update_deps.task._install_git`
fetching before checkout against a real git binary + bare remote.

Pre-3.3.0 the helper went straight to `git checkout <branch>` / `git
pull` on an existing local clone. A directive change that flipped
from `main` to a remote branch added after the original clone hit
`pathspec '<branch>' did not match` because the local clone had no
ref for it. The operator had to do a manual `git fetch` in the
package dir before Update Dependencies would resolve. The fix wires
a `git fetch --prune --tags origin` ahead of the checkout step.

These tests drive the real `_install_git` against a temp bare repo
so the actual order (and the actual fetch semantics) are exercised
end-to-end without the runner.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


PYOPS_SET_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "pyops"
))


def _git(repo: str, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args],
                          check=check, capture_output=True, text=True)


@pytest.fixture
def bare_with_minimal_pkg(tmp_path):
    """Bare git remote at `<tmp>/remote.git` with a minimal pyproject
    + module on `main`, plus a working copy that tests can push new
    branches from to simulate `remote got a new branch after the
    original cami clone`."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    _git(str(work), "remote", "add", "origin", str(bare))
    _git(str(work), "config", "user.email", "integration@example.com")
    _git(str(work), "config", "user.name", "Integration")
    (work / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68.0"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "demo_pkg"\nversion = "0.0.1"\nrequires-python = ">=3.10"\n'
    )
    pkg_dir = work / "demo_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("VALUE = 'main'\n")
    _git(str(work), "add", ".")
    _git(str(work), "commit", "-m", "seed main")
    _git(str(work), "push", "origin", "main")
    return str(bare), str(work)


@pytest.fixture
def loaded_task(tmp_path):
    """Loads `set_update_deps.task.py` into a namespace mirroring the
    runner's globals -- enough for `_install_git` to resolve
    `_pkg_dir(name)` and run_cmd."""
    venv_path = tmp_path / "venv"
    pkg_path = tmp_path / "pkg"
    (pkg_path / "test_set").mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv_path / "test_set")],
                   check=True, capture_output=True)
    glob = {
        "__name__": "set_update_deps_loaded",
        "appspace": str(tmp_path / "app"),
        "config": {
            "data": {"git": str(tmp_path / "data" / "git"), "jobs": str(tmp_path / "data" / "jobs")},
            "runner": {"venv": str(venv_path), "pkg": str(pkg_path)},
        },
        "name": None,
    }
    (tmp_path / "app").mkdir(exist_ok=True)
    for fname in ("set.library.py", "set_update_deps.task.py"):
        path = os.path.join(PYOPS_SET_DIR, fname)
        src = open(path).read().replace('if __name__ == "__main__":', 'if False:')
        exec(compile(src, path, "exec"), glob)
    venv_pip = str(venv_path / "test_set" / "bin" / "pip")
    return glob, venv_pip


class TestInstallGitFetchAgainstRealRemote:

    def test_initial_clone_then_branch_flip_resolves(self, bare_with_minimal_pkg, loaded_task):
        """The original cami workflow that the fix targets:
        1. Set up cami with directive `#! <url> main` -- clones.
        2. A new branch is added to the remote.
        3. Operator flips the directive to that branch + runs
           Update Dependencies. Pre-fix: `git checkout newbranch`
           failed because the local clone had never fetched. Post-
           fix: the fetch in `_install_git` brings the branch in
           before checkout."""
        bare, work = bare_with_minimal_pkg
        glob, pip_bin = loaded_task
        install_git = glob["_install_git"]

        install_git(pip_bin, {"url": f"file://{bare}", "branch": "main"}, "test_set")

        # Operator adds `feature` to remote between Update-Dependencies runs.
        _git(work, "checkout", "-b", "feature")
        (Path(work) / "demo_pkg" / "__init__.py").write_text("VALUE = 'feature'\n")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "feature work")
        _git(work, "push", "origin", "feature")

        install_git(pip_bin, {"url": f"file://{bare}", "branch": "feature"}, "test_set")

        repo_dir = os.path.join(
            glob["config"]["runner"]["pkg"], "test_set", "remote"
        )
        current = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert current == "feature", f"clone did not flip to feature; on {current!r}"
        body = (Path(repo_dir) / "demo_pkg" / "__init__.py").read_text()
        assert "VALUE = 'feature'" in body, (
            "checkout landed on feature but the working tree still has "
            "main-branch content; pull did not advance the local ref"
        )

    def test_existing_repo_fetch_picks_up_new_tags(self, bare_with_minimal_pkg, loaded_task):
        """`--tags` on the fetch is what keeps tag-pinned releases
        reachable across Update-Dependencies runs."""
        bare, work = bare_with_minimal_pkg
        glob, pip_bin = loaded_task
        install_git = glob["_install_git"]

        install_git(pip_bin, {"url": f"file://{bare}", "branch": "main"}, "test_set")

        # Tag a release on the remote after the original clone.
        _git(work, "tag", "v1.0.0")
        _git(work, "push", "origin", "v1.0.0")

        install_git(pip_bin, {"url": f"file://{bare}", "branch": "main"}, "test_set")

        repo_dir = os.path.join(
            glob["config"]["runner"]["pkg"], "test_set", "remote"
        )
        tags = _git(repo_dir, "tag", "-l").stdout.split()
        assert "v1.0.0" in tags, f"fetch with --tags should have brought v1.0.0 in; got {tags!r}"
