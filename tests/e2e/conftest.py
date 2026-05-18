"""
E2E test fixtures for PyOps Playwright tests.

Each test gets a fresh PyOps server instance with isolated data directories.
The server is started via gunicorn (same as run_dev.sh) and stopped after each test.
Server logs are captured and available for assertion.

Run with:  pytest tests/e2e/ -v
           E2E tests are skipped when collected as part of the full suite
           (pytest tests/) unless --e2e is passed explicitly.
"""

import json
import os
import re
import signal
import subprocess
import time

import psutil
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_addoption(parser):
    parser.addoption("--e2e", action="store_true", default=False, help="Run E2E tests")


def _is_e2e_item(item):
    """Check if a test item belongs to the e2e directory."""
    return "tests/e2e" in str(item.fspath) or "tests\\e2e" in str(item.fspath)


def pytest_collection_modifyitems(config, items):
    """Mark and optionally skip E2E tests.

    All items under tests/e2e/ get the 'e2e' marker applied automatically
    (avoids module-level pytestmark which triggers PytestUnknownMarkWarning
    before marker registration with -W error).

    E2E tests are skipped unless --e2e is passed or tests/e2e/ is targeted directly.
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnknownMarkWarning)
        e2e_marker = pytest.mark.e2e

    # Determine if E2E should run
    run_e2e = config.getoption("--e2e", default=False)
    if not run_e2e:
        args = config.invocation_params.args
        run_e2e = any("e2e" in str(a) for a in args)

    for item in items:
        if _is_e2e_item(item):
            item.add_marker(e2e_marker)
            if not run_e2e:
                item.add_marker(pytest.mark.skip(
                    reason="E2E tests require --e2e flag or direct tests/e2e/ path"
                ))
E2E_PORT = 18321
BASE_URL = f"http://127.0.0.1:{E2E_PORT}"
SERVER_STARTUP_TIMEOUT = int(os.environ.get("E2E_STARTUP_TIMEOUT", 30))
# Graceful shutdown can outlast a pip install — pyops's contract is that
# in-flight `ensure_set_environment` (called from the create/clone HTTP
# BackgroundTask or from runner.py before each Popen) is never killed
# mid-flight, so the worker holds the bootstrap lock for the full pip duration.
# Matches gunicorn's graceful_timeout in app/gunicorn.py (120s).
SERVER_SHUTDOWN_TIMEOUT = 120

# Log patterns that indicate unexpected errors (checked after each test).
# Case-sensitive on purpose: Python logging emits FATAL/CRITICAL in uppercase,
# while git prints lowercase "fatal:" to stderr on normal 404-style failures.
UNEXPECTED_ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\bFATAL\b"),
    re.compile(r"\bCRITICAL\b"),
]

# Log patterns that are expected/acceptable even though they match error patterns
EXPECTED_LOG_PATTERNS = [
    re.compile(r"Worker .* exiting"),
    re.compile(r"Shutting down"),
    re.compile(r"SIGTERM"),
    re.compile(r"signal: term"),
]


def _is_expected_line(line):
    return any(p.search(line) for p in EXPECTED_LOG_PATTERNS)


@pytest.fixture(scope="function")
def e2e_env(tmp_path):
    """Create isolated data directories and test config pointing to tmp_path."""
    data_dir = tmp_path / "data"
    (data_dir / "jobs").mkdir(parents=True)
    (data_dir / "git").mkdir(parents=True)
    (data_dir / "venv").mkdir(parents=True)
    (data_dir / "pkg").mkdir(parents=True)

    config = {
        "web": {
            "bind": f"127.0.0.1:{E2E_PORT}",
            # 2 workers, not 1: controller_proxy makes a synchronous ZMQ
            # call to the bound service, which blocks the async event loop
            # of the handling worker. When the service responds by calling
            # back into pyops (e.g. data.task.py enqueueing a sub-task via
            # control.job.create → PUT /scriptlet/job/item), that callback
            # needs a SECOND worker to be free. worker=1 deadlocks; the
            # production config uses 4.
            "worker": 2,
            "timeout": 300,
        },
        "data": {
            "jobs": str(data_dir / "jobs"),
            "git": str(data_dir / "git"),
            "db": str(data_dir / "app.db"),
        },
        "runner": {
            "venv": str(data_dir / "venv"),
            "pkg": str(data_dir / "pkg"),
            "git_pull_interval": 9999,
            "drop_done_delay_seconds": 9999,
        },
        "log": {"loglevel": "debug"},
        "users": {"anonymous": {"debug_port": 15679}},
    }

    config_path = tmp_path / "pyops_e2e.json"
    config_path.write_text(json.dumps(config, indent=2))

    return {
        "config_path": str(config_path),
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "base_url": BASE_URL,
        "port": E2E_PORT,
    }


@pytest.fixture(scope="function")
def set_factory(e2e_env):
    """Create a user set on disk for tests that need one as a prerequisite.

    Mirrors what `app/internal/set_create.task.py` does (mkdir + git init +
    minimal initial commit) but talks straight to the filesystem so e2e
    fixtures don't pay the data-service spawn cost on every test. Use only
    when set creation is *not* the subject under test — for that, drive
    the controller path (`POST /controller/internal/sets/create`) directly.
    """
    def _make(name):
        repo = e2e_env["data_dir"] / "git" / name
        if (repo / ".git").is_dir():
            # Already created earlier in this test (multiple set_factory(name)
            # calls from a setup helper that runs more than once). Idempotent.
            return name
        repo.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ["PATH"], "HOME": str(e2e_env["tmp_path"])}
        run = lambda cmd: subprocess.check_call(cmd, cwd=str(repo), env=env)
        run(["git", "init", "-q"])
        run(["git", "config", "user.email", "test@example.com"])
        run(["git", "config", "user.name", "e2e"])
        (repo / ".gitignore").write_text("*.bck\n")
        (repo / "README.md").write_text(f"# {name}\n")
        run(["git", "add", ".gitignore", "README.md"])
        run(["git", "commit", "-q", "-m", "init"])
        return name

    return _make


@pytest.fixture(scope="function")
def commit_set(e2e_env):
    """Test-side `git add . && git commit` in a set's repo.

    Pyops's server-side library has no commit/push surface — the only
    sanctioned commit pathway in production is the internal
    `set_commit_drafts` task, enqueued from the Sets-view UI button.
    Tests that need a baseline commit to drive downstream behaviour
    (e.g. `kind=task` runs which read committed content, or
    `has_unstaged=False` assertions) use this helper to do the commit
    directly via subprocess, bypassing the queue.

    Passes `-c user.email` / `-c user.name` overrides on the commit
    invocation so the helper works in CI environments where the global
    git identity is unset and the repo (e.g. one created by the
    `set_create` task) doesn't have a per-repo user.* configured."""
    def _commit(set_name, msg="setup"):
        repo = e2e_env["data_dir"] / "git" / set_name
        env = {"PATH": os.environ["PATH"], "HOME": str(e2e_env["tmp_path"])}
        subprocess.check_call(
            ["git", "-C", str(repo), "add", "."],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [
                "git", "-C", str(repo),
                "-c", "user.email=e2e@example.com",
                "-c", "user.name=e2e",
                "commit", "-m", msg,
            ],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return repo
    return _commit


class ServerLog:
    """Wrapper around the server log file for assertions."""

    def __init__(self, log_path):
        self.log_path = log_path

    def read(self):
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", errors="replace") as f:
                return f.read()
        return ""

    def lines(self):
        return self.read().splitlines()

    def contains(self, pattern):
        """Check if log contains a regex pattern."""
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        return bool(pattern.search(self.read()))

    def assert_no_unexpected_errors(self, allow=None):
        """Assert that the log contains no unexpected error patterns.

        Args:
            allow: Optional list of regex patterns (str or compiled) whose
                   matching lines are treated as expected.
        """
        allow_patterns = []
        if allow:
            allow_patterns = [
                re.compile(p) if isinstance(p, str) else p for p in allow
            ]
        content = self.read()
        for pattern in UNEXPECTED_ERROR_PATTERNS:
            for match in pattern.finditer(content):
                start = content.rfind("\n", 0, match.start()) + 1
                end = content.find("\n", match.end())
                if end == -1:
                    end = len(content)
                line = content[start:end]
                # Grab a generous window of context after the match so that the
                # allow-pattern check sees the full traceback — the identifying
                # keyword (e.g. "no_such_lib") commonly appears in the final
                # "Exception: ..." line, 10+ lines below "Traceback".
                context_end = match.start()
                for _ in range(30):
                    next_nl = content.find("\n", context_end + 1)
                    if next_nl == -1:
                        context_end = len(content)
                        break
                    context_end = next_nl
                context = content[start:context_end]
                if _is_expected_line(line):
                    continue
                if any(ap.search(context) for ap in allow_patterns):
                    continue
                pytest.fail(
                    f"Unexpected error in server log:\n{context}"
                )


def _wait_for_server(url, timeout):
    """Poll the server until it responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    return False


def _pgid_members(pgid):
    """Return the PIDs currently in `pgid`. Empty list if the group is gone.

    Uses psutil rather than shelling out to `pgrep` — the CI image
    (debian:trixie-slim) does not ship `procps`, and psutil is already a
    runtime dependency of the app.
    """
    members = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if os.getpgid(proc.info["pid"]) == pgid:
                members.append(proc.info["pid"])
        except (ProcessLookupError, psutil.NoSuchProcess, PermissionError):
            continue
    return members


def _kill_server(proc, timeout):
    """Gracefully stop the server and verify cleanup.

    Contract:
      - Send SIGTERM to the process group.
      - Wait `timeout` seconds for the master to exit.
      - Poll up to 2s more for all group members to exit.
      - If anything is still alive, SIGKILL the group and raise
        RuntimeError — tests MUST flag leaked processes so they can be
        fixed, not paper over the leak.
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return

    graceful = True
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        graceful = False

    # Even after the master exits, give children a short grace period.
    deadline = time.time() + 2
    leftovers = _pgid_members(pgid)
    while leftovers and time.time() < deadline:
        time.sleep(0.2)
        leftovers = _pgid_members(pgid)

    if leftovers:
        # Escalate to SIGKILL to keep the test host clean, then raise.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(
            f"Server did not shut down gracefully within {timeout}s; "
            f"SIGKILL was required for PIDs {leftovers} in pgid {pgid}. "
            f"Graceful-wait-on-master: {graceful}."
        )

    if not graceful:
        raise RuntimeError(
            f"Server master (pid={proc.pid}, pgid={pgid}) did not exit "
            f"within {timeout}s of SIGTERM — investigate shutdown path."
        )


@pytest.fixture(scope="function")
def pyops_server(e2e_env):
    """Start a fresh PyOps server, yield ServerLog, then stop and clean up.

    The server runs in a new process group so all children (gunicorn workers,
    runner daemon) can be terminated together.
    """
    log_path = str(e2e_env["tmp_path"] / "server.log")
    log_file = open(log_path, "w")

    env = os.environ.copy()
    env["PYOPS_CONFIG"] = e2e_env["config_path"]

    proc = subprocess.Popen(
        [
            os.path.join(PROJECT_ROOT, ".venv", "bin", "gunicorn"),
            "-c", "app/gunicorn.py",
            "--access-logfile", "-",
            "--error-logfile", "-",
            "--capture-output",
        ],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,  # new process group for clean shutdown
    )

    server_log = ServerLog(log_path)

    try:
        if not _wait_for_server(e2e_env["base_url"], SERVER_STARTUP_TIMEOUT):
            log_file.flush()
            content = server_log.read()
            _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
            log_file.close()
            pytest.fail(
                f"PyOps server did not start within {SERVER_STARTUP_TIMEOUT}s.\n"
                f"Log output:\n{content[-2000:]}"
            )
        yield server_log
    finally:
        _kill_server(proc, SERVER_SHUTDOWN_TIMEOUT)
        log_file.close()


@pytest.fixture(scope="session")
def base_url():
    """Provide the base URL for the test server (overrides pytest-base-url)."""
    return BASE_URL


@pytest.fixture(scope="function")
def api(pyops_server, e2e_env):
    """Provide a requests.Session pre-configured for the test server API."""
    server_url = e2e_env["base_url"]
    session = requests.Session()
    session.headers["X-User"] = "e2e_test_user"

    original_request = session.request

    def patched_request(method, url, **kwargs):
        if url.startswith("/"):
            url = server_url + url
        return original_request(method, url, **kwargs)

    session.request = patched_request
    yield session
    session.close()


@pytest.fixture(scope="function")
def api_anon(pyops_server, e2e_env):
    """Like api, but without X-User — defaults to 'anonymous', matching browser requests.

    Use this fixture when the test also drives the browser (e2e_page), so that
    jobs and schedules created here are owned by 'anonymous' and therefore
    visible in the monitor/scheduler pages that the browser renders.
    """
    server_url = e2e_env["base_url"]
    session = requests.Session()
    # Intentionally NO X-User header; server resolves to 'anonymous'.

    original_request = session.request

    def patched_request(method, url, **kwargs):
        if url.startswith("/"):
            url = server_url + url
        return original_request(method, url, **kwargs)

    session.request = patched_request
    yield session
    session.close()


@pytest.fixture(scope="function")
def e2e_page(pyops_server, base_url, page):
    """Provide a Playwright page pointing at the test server.

    Depends on pyops_server so the server is running before the page is used.
    """
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    return page
