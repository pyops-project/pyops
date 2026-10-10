import os
import subprocess

import pytest


@pytest.fixture
def set_repo(mock_conf):
    """Factory for real git set repos under the configured git base.

    `set_repo(name, committed={}, drafts={})` creates `<git base>/<name>`,
    commits `committed` ({repo-relative path: content}) together with the
    README.md and `*.bck` .gitignore that `git.get_repo_path` would otherwise
    commit on first access, then writes `drafts` to the working tree without
    committing. Returns the repo path. The repo lives under the per-test
    tmp_path, so it goes with it."""
    from app.lib import files

    def git(repo, *args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def write(repo, tree):
        for rel, content in tree.items():
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def make(name, committed=None, drafts=None):
        repo = os.path.join(files.conf_data()["git"], name)
        os.makedirs(repo)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "tests@pyops.local")
        git(repo, "config", "user.name", "pyops tests")
        git(repo, "config", "commit.gpgsign", "false")
        write(repo, {"README.md": f"# {name}\n", ".gitignore": "*.bck\n", **(committed or {})})
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "initial")
        write(repo, drafts or {})
        return repo

    return make
