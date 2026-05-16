"""Shared pyopsctl test plumbing.

- sys.path: make the `pyopsctl` package importable without installing
  it (the launcher does the same trick at runtime via realpath).
- Fixtures used by both `integration/` and `e2e/` subdirectories:
  isolated pyops + pyopsctl config (`pyopsctl_env`), a real running
  pyops server (`pyops_server_for_ctl`), a git-initialized set
  (`pyops_set`), the path to the pyopsctl launcher (`pyopsctl_bin`).
  Pytest inherits these into both subdirs automatically.

`integration/` tests verify the pyopsctl ↔ pyops API contract — what
the CLI sends, what it does with the response. `e2e/` tests verify
the *interplay of systems*: pyopsctl, pyops, and a third actor (a
debugger client, the web UI, etc.) cooperating on the same state.
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest
import requests


_HERE = os.path.dirname(os.path.realpath(__file__))
_OUTER_CTL = os.path.normpath(os.path.join(_HERE, "..", "..", "ctl"))
if _OUTER_CTL not in sys.path:
    sys.path.insert(0, _OUTER_CTL)

PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
SERVER_PORT = 18322
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
STARTUP_TIMEOUT = 60.0
SHUTDOWN_TIMEOUT = 30.0


def _wait_ready(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(url + "/version", timeout=2)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def _kill(proc, timeout: float):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=5)


@pytest.fixture(scope="function")
def pyopsctl_env(tmp_path, monkeypatch):
    """Isolated data dirs, a pyops config that points at them, and a
    pyopsctl config that points at a local profile.

    The pyopsctl config sits in `$HOME/.config/pyops/config.toml`; we
    point `HOME` at tmp_path so the test's view of the operator home is
    self-contained.
    """
    data = tmp_path / "data"
    for sub in ("jobs", "git", "venv", "pkg"):
        (data / sub).mkdir(parents=True)

    pyops_conf = {
        "web": {"bind": f"127.0.0.1:{SERVER_PORT}", "worker": 2, "timeout": 300},
        "data": {
            "jobs": str(data / "jobs"),
            "git": str(data / "git"),
            "db": str(data / "app.db"),
        },
        "runner": {
            "venv": str(data / "venv"),
            "pkg": str(data / "pkg"),
            "git_pull_interval": 9999,
            "drop_done_delay_seconds": 9999,
        },
        "log": {"loglevel": "debug"},
        "users": {"pyopsctl": {"debug_port": 45679}, "anonymous": {"debug_port": 45680}},
    }
    pyops_conf_path = tmp_path / "pyops.json"
    pyops_conf_path.write_text(json.dumps(pyops_conf, indent=2))

    home = tmp_path / "home"
    cfg_dir = home / ".config" / "pyops"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(
        'default_profile = "local"\n'
        '\n'
        '[profiles.local]\n'
        f'url = "{SERVER_URL}"\n'
        '# no api_key, no user → X-User defaults to "pyopsctl"\n'
    )
    cfg_path.chmod(0o600)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYOPSCTL_CONFIG", str(cfg_path))

    return {
        "tmp_path": tmp_path,
        "data_dir": data,
        "pyops_conf_path": str(pyops_conf_path),
        "pyopsctl_conf_path": str(cfg_path),
        "url": SERVER_URL,
        "debug_port": 45679,  # the port the runner will open for X-User=pyopsctl
    }


@pytest.fixture(scope="function")
def pyops_server_for_ctl(pyopsctl_env):
    """Start a pyops server, yield, kill on teardown. Mirrors
    tests/e2e/conftest.py:pyops_server but on its own port + simpler
    config."""
    log_path = str(pyopsctl_env["tmp_path"] / "server.log")
    log = open(log_path, "w")
    env = os.environ.copy()
    env["PYOPS_CONFIG"] = pyopsctl_env["pyops_conf_path"]

    proc = subprocess.Popen(
        [
            os.path.join(PROJECT_ROOT, ".venv", "bin", "gunicorn"),
            "-c", "app/gunicorn.py",
            "--access-logfile", "-",
            "--error-logfile", "-",
            "--capture-output",
        ],
        cwd=PROJECT_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    try:
        if not _wait_ready(pyopsctl_env["url"], STARTUP_TIMEOUT):
            log.flush()
            with open(log_path) as f:
                tail = f.read()[-2000:]
            _kill(proc, SHUTDOWN_TIMEOUT)
            pytest.fail(f"pyops server didn't come up in {STARTUP_TIMEOUT}s:\n{tail}")
        yield {"url": pyopsctl_env["url"], "log_path": log_path,
               "data_dir": pyopsctl_env["data_dir"],
               "debug_port": pyopsctl_env["debug_port"]}
    finally:
        _kill(proc, SHUTDOWN_TIMEOUT)
        log.close()


@pytest.fixture(scope="function")
def pyops_set(pyopsctl_env):
    """Create a git-initialized set on disk so scriptlet/inventory
    endpoints have something to operate on. Mirrors the helper from
    tests/e2e/conftest.py:set_factory but eager (always creates 'test')."""
    repo = pyopsctl_env["data_dir"] / "git" / "test"
    repo.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(pyopsctl_env["tmp_path"])}
    run = lambda cmd: subprocess.check_call(cmd, cwd=str(repo), env=env)
    run(["git", "init", "-q"])
    run(["git", "config", "user.email", "ctl@test.local"])
    run(["git", "config", "user.name", "pyopsctl-integration"])
    (repo / ".gitignore").write_text("*.bck\n")
    (repo / "README.md").write_text("# test\n")
    run(["git", "add", ".gitignore", "README.md"])
    run(["git", "commit", "-q", "-m", "init"])
    return "test"


@pytest.fixture(scope="function")
def pyopsctl_bin():
    """Absolute path to the launcher."""
    return os.path.join(PROJECT_ROOT, "ctl", "bin", "pyopsctl")


@pytest.fixture(scope="function")
def run_ctl():
    """Helper that invokes pyopsctl as a subprocess. Returned as a
    fixture so it auto-inherits into the `integration/` and `e2e/`
    subdirectories without per-dir conftest copies.

    Usage:
        def test_foo(pyopsctl_bin, run_ctl):
            r = run_ctl(pyopsctl_bin, "version", "--json")

    Defaults to capturing stdout/stderr and asserting exit 0. Tests that
    expect a non-zero exit pass `check=False`.
    """
    def _run(pyopsctl_bin, *argv, check=True, capture=True, input_text=None):
        result = subprocess.run(
            [pyopsctl_bin, *argv],
            capture_output=capture,
            text=True,
            input=input_text,
            env=os.environ.copy(),
            timeout=120,
        )
        if check and result.returncode != 0:
            pytest.fail(
                f"pyopsctl {' '.join(argv)} exited {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result
    return _run
