"""Session state directory: layout, perms, stale-PID handling."""
import os


def test_sessions_root_honors_env_var(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    assert paths.sessions_root() == str(tmp_path)


def test_sessions_root_falls_back_to_xdg_runtime_then_cache(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.delenv("PYOPS_SESSION_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert paths.sessions_root() == os.path.join(str(tmp_path), "pyops", "sessions")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert paths.sessions_root() == os.path.expanduser("~/.cache/pyops/sessions")


def test_for_job_returns_paths_under_root(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    sp = paths.for_job("anonymous_test_scr_2026")
    assert sp.root == os.path.join(str(tmp_path), "anonymous_test_scr_2026")
    assert sp.sock_file.endswith("/sock")
    assert sp.pid_file.endswith("/pid")
    assert sp.meta_file.endswith("/meta.json")


def test_ensure_dir_creates_with_0700(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    sp = paths.for_job("j1")
    paths.ensure_dir(sp)
    mode = os.stat(sp.root).st_mode & 0o777
    assert mode == 0o700


def test_pid_roundtrip(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    sp = paths.for_job("j1")
    paths.ensure_dir(sp)
    paths.write_pid(sp, 12345)
    assert paths.read_pid(sp) == 12345


def test_is_alive_for_own_pid_and_not_for_missing(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    assert paths.is_alive(os.getpid()) is True
    # PID 0 isn't a real process; PID 1 might be. Pick something likely-dead.
    assert paths.is_alive(2_000_000_000) is False


def test_cleanup_removes_pid_sock_meta_and_dir(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    sp = paths.for_job("j1")
    paths.ensure_dir(sp)
    paths.write_pid(sp, os.getpid())
    paths.write_meta(sp, {"job_id": "j1"})
    open(sp.sock_file, "w").close()  # touch
    paths.cleanup(sp)
    assert not os.path.isdir(sp.root)


def test_list_sessions_skips_non_dirs(monkeypatch, tmp_path):
    from pyopsctl.session import paths

    monkeypatch.setenv("PYOPS_SESSION_DIR", str(tmp_path))
    (tmp_path / "stray-file").write_text("ignored")
    (tmp_path / "job_a").mkdir()
    (tmp_path / "job_b").mkdir()
    names = sorted(sp.job_id for sp in paths.list_sessions())
    assert names == ["job_a", "job_b"]
