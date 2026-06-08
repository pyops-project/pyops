"""Unit tests for app/pyops/set.library.py — helpers shared by every
`set_*.task.py`. `normalize_origin_url` in particular: the migration
hook that rewrites legacy embedded-credentials origin URLs to the
credential-free form on next push/pull."""

import os

import pytest


PYOPS_SET_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
))


@pytest.fixture
def lib(tmp_path):
    """Exec set.library.py into a controlled namespace, with the same
    `appspace` / `config` globals the runner injects for the pyops
    set."""
    glob = {
        "__name__": "set_library_loaded",
        "appspace": str(tmp_path / "app"),
        "config": {
            "data": {"git": str(tmp_path / "git"), "jobs": str(tmp_path / "jobs")},
            "runner": {"venv": str(tmp_path / "venv"), "pkg": str(tmp_path / "pkg")},
        },
        "name": None,
    }
    (tmp_path / "app").mkdir(exist_ok=True)
    path = os.path.join(PYOPS_SET_DIR, "set.library.py")
    with open(path) as f:
        src = f.read()
    exec(compile(src, path, "exec"), glob)
    return glob


class TestNormalizeOriginUrl:
    """`normalize_origin_url(set_dir)` strips embedded HTTPS
    user:pass@ from origin's URL. Mock-driven coverage; the real-git
    flow is exercised in tests/integration/lib/test_git_integration.py."""

    def _install_recorders(self, lib, monkeypatch, get_url_output):
        """Patch the `subprocess` module the library captured at exec
        time + replace `run_cmd` with a recorder. `get_url_output` is
        what `git remote get-url origin` returns (or an Exception to
        simulate "no origin"). monkeypatch handles teardown so the
        global stdlib subprocess module is not permanently mutated
        across the test session."""

        def fake_check_output(cmd, **kwargs):
            if cmd[:3] == ["git", "remote", "get-url"]:
                if isinstance(get_url_output, Exception):
                    raise get_url_output
                return get_url_output + "\n"
            raise AssertionError(f"unexpected check_output call: {cmd}")

        calls = []
        def fake_run_cmd(cmd, cwd=None, check=True):
            calls.append({"cmd": list(cmd), "cwd": cwd})
        monkeypatch.setattr(lib["subprocess"], "check_output", fake_check_output)
        monkeypatch.setitem(lib, "run_cmd", fake_run_cmd)
        return calls

    def test_strips_user_pass_from_https(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "https://bot:t0k3n@example.com/g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "https://example.com/g/r.git"
        assert calls == [
            {"cmd": ["git", "remote", "set-url", "origin",
                     "https://example.com/g/r.git"], "cwd": "/repo"}
        ]

    def test_preserves_port_in_clean_url(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "https://u:p@host.example:8443/g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "https://host.example:8443/g/r.git"
        assert calls[0]["cmd"][-1] == "https://host.example:8443/g/r.git"

    def test_strips_user_pass_from_http(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "http://u:p@example.com/g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "http://example.com/g/r.git"
        assert len(calls) == 1

    def test_noop_when_origin_already_clean(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "https://example.com/g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "https://example.com/g/r.git"
        assert calls == []

    def test_noop_when_ssh_origin(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "git@example.com:g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "git@example.com:g/r.git"
        assert calls == []

    def test_noop_when_ssh_url_scheme(self, lib, monkeypatch):
        calls = self._install_recorders(lib, monkeypatch, "ssh://git@example.com:22/g/r.git")
        result = lib["normalize_origin_url"]("/repo")
        assert result == "ssh://git@example.com:22/g/r.git"
        assert calls == []

    def test_returns_none_when_no_origin(self, lib, monkeypatch):
        import subprocess
        err = subprocess.CalledProcessError(1, ["git", "remote", "get-url", "origin"])
        calls = self._install_recorders(lib, monkeypatch, err)
        result = lib["normalize_origin_url"]("/repo")
        assert result is None
        assert calls == []

    def test_idempotent_on_second_call(self, lib, monkeypatch):
        """Run normalise twice in sequence — second call sees the
        already-cleaned URL the first call wrote, takes the no-op
        path. Used by the migration hook so set_pull / set_commit_drafts
        can call it unconditionally without double-rewriting."""
        states = ["https://u:p@example.com/r.git", "https://example.com/r.git"]
        def fake_check_output(cmd, **kwargs):
            return states.pop(0) + "\n"
        calls = []
        def fake_run_cmd(cmd, cwd=None, check=True):
            calls.append(list(cmd))
        monkeypatch.setattr(lib["subprocess"], "check_output", fake_check_output)
        monkeypatch.setitem(lib, "run_cmd", fake_run_cmd)

        lib["normalize_origin_url"]("/repo")
        lib["normalize_origin_url"]("/repo")
        # Only the first call writes; the second sees the clean URL.
        assert len(calls) == 1
        assert calls[0][:3] == ["git", "remote", "set-url"]
