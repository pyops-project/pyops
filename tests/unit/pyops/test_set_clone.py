"""Unit tests for app/pyops/set_clone.task.py — credential handling
in particular.

The task normally runs inside the pyops runner with `appspace`,
`config`, and the param-injected globals (`url`, `username`,
`password`, `git_name`, `git_email`); `#!{::set}` adds set.library's
symbols to the exec namespace. The fixture below mirrors that setup
and replaces `run_cmd` + the file-write side-effect so the credential
rewrite logic can be inspected in isolation."""

import os

import pytest


PYOPS_SET_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
))


@pytest.fixture
def task(tmp_path, monkeypatch):
    git_base = tmp_path / "data" / "git"
    git_base.mkdir(parents=True)

    glob = {
        "__name__": "set_clone_loaded",
        "appspace": str(tmp_path / "app"),
        "config": {
            "data": {"git": str(git_base), "jobs": str(tmp_path / "data" / "jobs")},
            "runner": {
                "venv": str(tmp_path / "data" / "venv"),
                "pkg": str(tmp_path / "data" / "pkg"),
            },
        },
        "name": None,
    }
    (tmp_path / "app").mkdir(exist_ok=True)
    for fname in ("set.library.py", "set_clone.task.py"):
        path = os.path.join(PYOPS_SET_DIR, fname)
        with open(path) as f:
            src = f.read()
        src = src.replace('if __name__ == "__main__":', 'if False:')
        exec(compile(src, path, "exec"), glob)
    glob["_tmp_path"] = tmp_path
    glob["_git_base_path"] = git_base
    return glob


def _install_recording_run_cmd(task):
    """Replace `run_cmd` with a recorder. Returns the list it appends to."""
    calls = []

    def fake_run_cmd(cmd, cwd=None, check=True):
        calls.append({"cmd": list(cmd), "cwd": cwd})
        # Simulate `git clone <url> <name>`: create the target dir + .git/
        # so the post-clone fs-touches in the task body don't AttributeError.
        if cmd[:2] == ["git", "clone"]:
            target = os.path.join(cwd, cmd[-1])
            os.makedirs(os.path.join(target, ".git"), exist_ok=True)

    task["run_cmd"] = fake_run_cmd
    return calls


class TestSetCloneCredentialRewrite:
    """Regression: clones with embedded HTTPS credentials must rewrite
    the remote URL to the credential-free form after cloning. Editing
    `.git/credentials` later only helps if git resolves the password
    via the credential helper — and that only happens when origin's
    URL is the clean form, not `https://user:pass@host/…`."""

    def _run_clone(self, task, **params):
        for k in ("url", "username", "password", "git_name", "git_email"):
            task[k] = params.get(k, "")
        task["main"]()

    def test_origin_url_rewritten_to_clean_form(self, task):
        calls = _install_recording_run_cmd(task)
        self._run_clone(task,
                        url="https://example.com/group/myrepo.git",
                        username="bot",
                        password="t0k3n")
        # Find the `git remote set-url origin <url>` invocation. It must
        # use the original (credential-free) URL — not the embedded form.
        set_url = [c for c in calls if c["cmd"][:3] == ["git", "remote", "set-url"]]
        assert len(set_url) == 1, f"expected one set-url call, got {calls!r}"
        assert set_url[0]["cmd"] == [
            "git", "remote", "set-url", "origin",
            "https://example.com/group/myrepo.git",
        ]

    def test_set_url_happens_after_clone_before_helper_config(self, task):
        """Order matters: clone with embedded URL, immediately overwrite
        origin to the clean URL, only then configure the credential
        helper. Otherwise a pull-during-clone retry would race against
        the half-configured credentials."""
        calls = _install_recording_run_cmd(task)
        self._run_clone(task,
                        url="https://example.com/g/r.git",
                        username="u",
                        password="p")
        kinds = [c["cmd"][:3] for c in calls]
        clone_idx = kinds.index(["git", "clone", "https://u:p@example.com/g/r.git"])
        set_url_idx = kinds.index(["git", "remote", "set-url"])
        helper_idx = next(
            i for i, c in enumerate(calls)
            if c["cmd"][:3] == ["git", "config", "credential.helper"]
        )
        assert clone_idx < set_url_idx < helper_idx

    def test_no_set_url_when_no_credentials(self, task):
        calls = _install_recording_run_cmd(task)
        self._run_clone(task, url="https://example.com/g/r.git")
        set_url = [c for c in calls if c["cmd"][:3] == ["git", "remote", "set-url"]]
        assert set_url == [], "no credentials provided: origin URL must not be rewritten"

    def test_no_set_url_when_only_username(self, task):
        calls = _install_recording_run_cmd(task)
        self._run_clone(task, url="https://example.com/g/r.git", username="bot")
        set_url = [c for c in calls if c["cmd"][:3] == ["git", "remote", "set-url"]]
        assert set_url == []

    def test_no_set_url_for_ssh_url(self, task):
        """SSH URLs don't carry HTTPS credentials; the embedded-credentials
        path doesn't fire for them, and neither does the rewrite."""
        calls = _install_recording_run_cmd(task)
        self._run_clone(task,
                        url="git@example.com:group/myrepo.git",
                        username="u", password="p")
        set_url = [c for c in calls if c["cmd"][:3] == ["git", "remote", "set-url"]]
        assert set_url == []

    def test_credentials_file_written_with_full_url(self, task):
        _install_recording_run_cmd(task)
        self._run_clone(task,
                        url="https://example.com/g/r.git",
                        username="bot", password="t0k")
        target = task["_git_base_path"] / "r"
        cred_file = target / ".git" / "credentials"
        assert cred_file.is_file()
        assert cred_file.read_text().strip() == "https://bot:t0k@example.com"
