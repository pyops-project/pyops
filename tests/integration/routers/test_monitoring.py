"""Integration tests for monitoring endpoints (`/health`, `/health/failed`, `/metrics`).

These tests drive the FastAPI router via TestClient against the real ORM
backed by an isolated SQLite (`mock_db` fixture). System-level probes
(runner socket, nvidia-smi, ulimits) are exercised against the real test
environment — assertions check structure and the values that ARE
deterministic under that environment, not absolute system values. Tests
that need a specific probe outcome pin it: the runner socket to a per-test
path (`runner_socket`), nvidia-smi and /dev/nvidia* to a faked outcome
(`_nvidia_smi`), a soft rlimit to a faked value.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def client(mock_db):
    """Plain TestClient against the real app — monitoring endpoints take no
    auth, so we don't need the dependency override that other router tests use."""
    from app.web import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def runner_socket(tmp_path, monkeypatch):
    """Point the runner-socket probe at a per-test path instead of the host's
    /tmp/pyops/runner.sock (which a pyops server on the host may hold or have
    left behind). Nothing exists at the returned path yet."""
    from app.routers import monitoring
    path = str(tmp_path / "runner.sock")
    monkeypatch.setattr(monitoring, "_RUNNER_SOCKET", path)
    return path


@pytest.fixture
def bound_runner_socket(runner_socket):
    """A ZMQ ROUTER bound on the probed path, the way the runner daemon binds
    /tmp/pyops/runner.sock (app/runner.py setup_runner_socket)."""
    import zmq
    context = zmq.Context()
    sock = context.socket(zmq.ROUTER)
    sock.bind(f"ipc://{runner_socket}")
    try:
        yield runner_socket
    finally:
        sock.close(linger=0)
        context.term()


@pytest.fixture
def unreachable_db(monkeypatch):
    """The engine cannot open the database (e.g. the volume holding it is
    gone), so the monitoring probe's own `SELECT 1` fails."""
    from sqlalchemy.exc import OperationalError
    from app.routers import monitoring

    def get_db_engine():
        raise OperationalError("SELECT 1", {}, Exception("unable to open database file"))

    monkeypatch.setattr(monitoring, "get_db_engine", get_db_engine)


def _override_conf(monkeypatch, data=None, runner=None):
    """Overlay keys onto the `data` / `runner` config sections for one test."""
    from app.lib import files as files_lib
    original_data, original_runner = files_lib.conf_data, files_lib.conf_runner
    monkeypatch.setattr(files_lib, "conf_data", lambda: {**original_data(), **(data or {})})
    monkeypatch.setattr(files_lib, "conf_runner", lambda: {**original_runner(), **(runner or {})})


def _nvidia_smi(monkeypatch, run, devices):
    """Fake the `nvidia-smi -L` call with `run` and the presence of
    /dev/nvidia* device nodes with `devices`; every other path is real."""
    from app.routers import monitoring
    real_exists = os.path.exists
    monkeypatch.setattr(monitoring.subprocess, "run", run)
    monkeypatch.setattr(
        monitoring.os.path, "exists",
        lambda p: devices if str(p).startswith("/dev/nvidia") else real_exists(p),
    )


def _smi_exits(returncode, stderr):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
    return run


def _smi_raises(exc):
    def run(cmd, **kwargs):
        raise exc
    return run


def _conflicting_update(set_name):
    """`.update_warnings.json` content of an "Update Dependencies" run in
    which two scriptlets of `set_name` pinned different requests versions."""
    return {
        "set": set_name,
        "conflicts": {
            "pip": [{
                "pkg": "requests",
                "winner": {"spec": "==2.32.0", "extra": [], "source": "new.task.py", "mtime": 200},
                "losers": [{"spec": "==2.30.0", "extra": [], "source": "old.task.py", "mtime": 100}],
            }],
            "git": [],
        },
        "installed": {"pip": [], "git": []},
    }


def _write_update_warnings(venv_dir, set_name, text):
    """Write `<venv_dir>/<set_name>/.update_warnings.json` with `text`."""
    set_venv = venv_dir / set_name
    set_venv.mkdir(parents=True)
    (set_venv / ".update_warnings.json").write_text(text)


def _add_job(state, set_="s1", name="t1", kind="task", owner="anonymous",
             detail=None, end=None):
    from app.orm.database import get_db_engine
    from app.orm.job import Job
    with Session(get_db_engine()) as session:
        # ID format mirrors the runner's generator (timestamp suffix is
        # parsed by /health stuck-detection).
        suffix = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        job = Job(
            id=f"{owner}_{set_}_{name}_{suffix}",
            set=set_, name=name, owner=owner, kind=kind, state=state,
            param=None, detail=detail, pid=None, drop_done=False,
        )
        if end is not None:
            job.end = end
        session.add(job)
        session.commit()
        return job.id


def _add_schedule(active, set_="s1", name="t1"):
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        sched = Schedule(
            set=set_, name=name, owner="anonymous", schedule="* * * * *",
            active=active, drop_done=False, param=None, last=None,
        )
        session.add(sched)
        session.commit()


class TestHealthEndpoint:
    def test_health_empty_db_is_ok_or_degraded(self, client):
        """No jobs, no schedules — the only thing that can flip status off ok
        in this env is the runner_socket / gpu check, which depends on the
        host. Verify the structure is right and status is one of ok/degraded.
        """
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        # Required top-level keys
        assert set(body.keys()) >= {
            "status", "version", "uptime_seconds", "checks",
            "ulimits", "gpu", "summary", "stuck",
        }
        assert body["status"] in ("ok", "degraded")
        assert body["uptime_seconds"] >= 0
        # checks subsystems
        assert set(body["checks"].keys()) >= {
            "database", "runner_socket", "data_dirs", "ulimits", "gpu",
        }
        assert body["checks"]["database"] == "ok"
        # ulimits structure
        assert "fd" in body["ulimits"]
        assert "current" in body["ulimits"]["fd"]
        assert "soft" in body["ulimits"]["fd"]
        # gpu structure
        assert body["gpu"]["status"] in (
            "ok", "no_gpu_expected", "missing_smi", "fail",
            "reboot_required", "timeout", "unknown",
        )
        # summary present
        assert body["summary"]["jobs"]["failed"] == 0
        assert body["summary"]["sets"] == 0

    def test_health_degraded_when_failed_jobs_present(self, client):
        """A failed job must flip status to `degraded` regardless of other
        checks — this is the alerting signal the operator cares about."""
        _add_job("failed", set_="alpha", name="bad", detail="boom")
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["summary"]["jobs"]["failed"] == 1
        assert body["summary"]["sets"] == 1

    def test_health_summary_counts_per_state(self, client):
        """Job state counters reflect DB content; missing states are 0."""
        _add_job("todo")
        _add_job("running", name="t2")
        _add_job("running", name="t3")
        _add_job("done", name="t4")
        r = client.get("/health")
        body = r.json()
        jobs = body["summary"]["jobs"]
        assert jobs["todo"] == 1
        assert jobs["running"] == 2
        assert jobs["done"] == 1
        assert jobs["failed"] == 0
        assert jobs["cancelled"] == 0

    def test_health_schedule_counts(self, client):
        _add_schedule(True)
        _add_schedule(True, name="t2")
        _add_schedule(False, name="t3")
        r = client.get("/health")
        body = r.json()
        assert body["summary"]["schedules"] == {"active": 2, "inactive": 1}

    def test_health_stuck_oldest_todo_age(self, client):
        """A `todo` job's age is derived from its ID timestamp suffix."""
        _add_job("todo")
        r = client.get("/health")
        body = r.json()
        assert body["stuck"]["oldest_todo_age_seconds"] is not None
        assert body["stuck"]["oldest_todo_age_seconds"] >= 0
        # No completed jobs yet
        assert body["stuck"]["last_completed_at"] is None

    def test_health_stuck_last_completed(self, client):
        """`last_completed_at` reflects the most recent done/failed/cancelled `end`."""
        end_time = datetime.now() - timedelta(minutes=5)
        _add_job("done", end=end_time)
        r = client.get("/health")
        body = r.json()
        assert body["stuck"]["last_completed_at"] is not None
        assert body["stuck"]["last_completed_seconds"] >= 290  # ~5 min

    def test_health_dependency_conflicts_surface_from_warnings_file(self, client, tmp_path, monkeypatch):
        """The 'Update Dependencies' flow writes
        `<venv_base>/{set}/.update_warnings.json` when scriptlets pin
        conflicting versions. /health reads those (the set itself is
        identified by its presence under <git_base>) and surfaces
        non-empty conflicts so a monitoring system can alert.

        The warnings file lives under <venv_base> rather than inside the
        set's git tree on purpose — it's pyops runtime state, keeping it
        out of the user's git repo avoids surprise untracked files."""
        git_dir = tmp_path / "fake_git"
        venv_dir = tmp_path / "fake_venv"
        (git_dir / "myset").mkdir(parents=True)
        _write_update_warnings(venv_dir, "myset", json.dumps(_conflicting_update("myset")))
        _override_conf(monkeypatch, data={"git": str(git_dir)}, runner={"venv": str(venv_dir)})

        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["dependency_conflicts"] == "conflicts"
        conflicts = body["dependency_conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["set"] == "myset"
        assert conflicts[0]["pip"][0]["pkg"] == "requests"

    def test_health_clean_when_warnings_file_empty(self, client, tmp_path, monkeypatch):
        """An update that resolved cleanly writes an empty conflicts list —
        /health must NOT flip to degraded on that."""
        git_dir = tmp_path / "fake_git"
        venv_dir = tmp_path / "fake_venv"
        (git_dir / "cleanset").mkdir(parents=True)
        _write_update_warnings(venv_dir, "cleanset", json.dumps({
            "set": "cleanset",
            "conflicts": {"pip": [], "git": []},
            "installed": {"pip": [], "git": []},
        }))
        _override_conf(monkeypatch, data={"git": str(git_dir)}, runner={"venv": str(venv_dir)})

        r = client.get("/health")
        body = r.json()
        assert body["checks"]["dependency_conflicts"] == "ok"
        assert body["dependency_conflicts"] == []

    def test_health_returns_503_when_db_unavailable(self, client, monkeypatch):
        """When the DB engine is unreachable, /health surfaces fail with 503
        so monitoring can distinguish 'pyops can't introspect itself' from
        'pyops sees a problem'."""
        from app.routers import monitoring
        monkeypatch.setattr(monitoring, "_check_database", lambda: False)

        r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "fail"
        assert r.json()["checks"]["database"] == "fail"

    def test_health_503_when_database_probe_fails(self, client, unreachable_db):
        """The probe itself (a `SELECT 1` through the engine) catches the
        engine failing: /health answers 503 / fail and carries no summary."""
        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "fail"
        assert body["checks"]["database"] == "fail"
        assert body["summary"] == {}

    def test_health_ok_when_every_check_passes(self, client, bound_runner_socket, monkeypatch):
        """A healthy container without a GPU: database reachable, runner bound
        to its socket, data dirs writable, ulimits fine, no nvidia-smi and no
        nvidia devices (expected), no dependency conflicts -> status ok."""
        _nvidia_smi(monkeypatch, _smi_raises(FileNotFoundError("nvidia-smi")), devices=False)
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["checks"] == {
            "database": "ok",
            "runner_socket": "ok",
            "data_dirs": "ok",
            "ulimits": "ok",
            "gpu": "no_gpu_expected",
            "dependency_conflicts": "ok",
        }
        assert body["status"] == "ok"

    @pytest.mark.parametrize("key", ["jobs", "git"])
    def test_health_degraded_when_data_dir_missing(self, client, bound_runner_socket, tmp_path, monkeypatch, key):
        """A configured jobs/git dir that does not exist (e.g. an unmounted
        volume) is reported as missing and degrades /health and /metrics.
        Every other check passes (runner bound, no GPU expected), so the
        missing dir alone flips the status."""
        _nvidia_smi(monkeypatch, _smi_raises(FileNotFoundError("nvidia-smi")), devices=False)
        _override_conf(monkeypatch, data={key: str(tmp_path / f"missing-{key}")})
        body = client.get("/health").json()
        assert body["checks"]["data_dirs"] == "missing"
        assert body["status"] == "degraded"
        assert "pyops_data_dirs_ok 0" in client.get("/metrics").text

    @pytest.mark.parametrize("limit, section", [
        ("RLIMIT_NOFILE", "fd"),
        ("RLIMIT_NPROC", "nproc"),
    ])
    def test_health_degraded_when_ulimit_usage_reaches_threshold(
            self, client, bound_runner_socket, monkeypatch, limit, section):
        """Usage at or above 80% of the soft limit degrades /health — here a
        soft limit of 1, which the process's own fds / threads exceed. Every
        other check passes (runner bound, no GPU expected), so the ulimit
        alone flips the status."""
        from app.routers import monitoring
        _nvidia_smi(monkeypatch, _smi_raises(FileNotFoundError("nvidia-smi")), devices=False)
        real_getrlimit = monitoring.resource.getrlimit
        watched = getattr(monitoring.resource, limit)

        def getrlimit(resource_id):
            if resource_id == watched:
                return 1, real_getrlimit(resource_id)[1]
            return real_getrlimit(resource_id)

        monkeypatch.setattr(monitoring.resource, "getrlimit", getrlimit)
        body = client.get("/health").json()
        assert body["ulimits"][section]["soft"] == 1
        assert body["checks"]["ulimits"] == "degraded"
        assert body["status"] == "degraded"

    @pytest.mark.parametrize("run, devices, status, gpu_ok", [
        (_smi_exits(9, "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."),
         True, "fail", 0),
        (_smi_raises(FileNotFoundError("nvidia-smi")), True, "missing_smi", 0),
        (_smi_raises(FileNotFoundError("nvidia-smi")), False, "no_gpu_expected", 1),
        (_smi_raises(subprocess.TimeoutExpired(["nvidia-smi", "-L"], 5)), True, "timeout", 0),
    ], ids=["driver_not_loaded", "smi_missing_with_devices", "no_gpu", "smi_hangs"])
    def test_health_gpu_status(self, client, bound_runner_socket, monkeypatch, run, devices, status, gpu_ok):
        """Each nvidia-smi outcome maps to its GPU status in /health and to
        the pyops_gpu_ok gauge; every status but no_gpu_expected degrades.
        Every other check passes (runner bound), so the GPU status alone
        decides between ok and degraded."""
        _nvidia_smi(monkeypatch, run, devices)
        body = client.get("/health").json()
        assert body["gpu"]["status"] == status
        assert body["checks"]["gpu"] == status
        assert body["status"] == ("ok" if gpu_ok else "degraded")
        if status == "fail":
            assert body["gpu"]["detail"].startswith("NVIDIA-SMI has failed")
        assert f'pyops_gpu_ok{{status="{status}"}} {gpu_ok}' in client.get("/metrics").text

    def test_health_dependency_scan_skips_what_is_not_a_scannable_set(self, client, tmp_path, monkeypatch):
        """Only user sets with a readable warnings file count. Skipped: a
        plain file in the git base, the bundled pyops set, a set never
        updated (no warnings file) and a set whose warnings file is corrupt.
        Relative git/venv paths resolve against the project root, not the
        process's working directory."""
        from app.lib import files as files_lib
        git_dir = tmp_path / "git"
        venv_dir = tmp_path / "venv"
        for set_name in ("pyops", "never_updated", "corrupt", "conflicted"):
            (git_dir / set_name).mkdir(parents=True)
        (git_dir / "notes.txt").write_text("not a set")
        _write_update_warnings(venv_dir, "notes.txt", json.dumps(_conflicting_update("notes.txt")))
        _write_update_warnings(venv_dir, "pyops", json.dumps(_conflicting_update("pyops")))
        _write_update_warnings(venv_dir, "corrupt", "{not json")
        _write_update_warnings(venv_dir, "conflicted", json.dumps(_conflicting_update("conflicted")))
        _override_conf(
            monkeypatch,
            data={"git": os.path.relpath(git_dir, files_lib.project_root)},
            runner={"venv": os.path.relpath(venv_dir, files_lib.project_root)},
        )
        elsewhere = tmp_path / "cwd"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        body = client.get("/health").json()
        conflicts = _conflicting_update("conflicted")["conflicts"]
        assert body["dependency_conflicts"] == [
            {"set": "conflicted", "pip": conflicts["pip"], "git": conflicts["git"]},
        ]
        assert body["checks"]["dependency_conflicts"] == "conflicts"

    def test_health_dependency_scan_without_git_base(self, client, tmp_path, monkeypatch):
        """No git base directory (no set was ever cloned) means no conflicts,
        even when a stale warnings file is still under the venv base."""
        venv_dir = tmp_path / "venv"
        _write_update_warnings(venv_dir, "gone", json.dumps(_conflicting_update("gone")))
        _override_conf(monkeypatch, data={"git": str(tmp_path / "no-git-base")}, runner={"venv": str(venv_dir)})
        body = client.get("/health").json()
        assert body["dependency_conflicts"] == []
        assert body["checks"]["dependency_conflicts"] == "ok"


class TestRunnerSocketProbe:
    """The runner daemon binds /tmp/pyops/runner.sock; /health and /metrics
    report it alive only while a process is actually bound to that path."""

    def test_missing_without_socket_file(self, client, runner_socket):
        body = client.get("/health").json()
        assert body["checks"]["runner_socket"] == "missing"
        assert body["status"] == "degraded"
        assert "pyops_runner_alive 0" in client.get("/metrics").text

    def test_missing_with_stale_socket_file(self, client, runner_socket):
        """A SIGKILL'd runner leaves its socket file behind; with no process
        bound to it, the runner still counts as missing."""
        import socket
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(runner_socket)
        stale.close()
        assert os.path.exists(runner_socket)

        body = client.get("/health").json()
        assert body["checks"]["runner_socket"] == "missing"
        assert "pyops_runner_alive 0" in client.get("/metrics").text

    def test_ok_while_a_process_is_bound(self, client, bound_runner_socket):
        body = client.get("/health").json()
        assert body["checks"]["runner_socket"] == "ok"
        assert "pyops_runner_alive 1" in client.get("/metrics").text


class TestHealthFailedEndpoint:
    def test_failed_empty(self, client):
        r = client.get("/health/failed")
        assert r.status_code == 200
        assert r.json() == {"count": 0, "jobs": []}

    def test_failed_returns_full_detail(self, client):
        """Each failed job must include set, name, kind, owner, detail and
        timestamps so monitoring can filter on any of them. Per spec there
        is no limit — we return all."""
        end1 = datetime(2026, 5, 1, 10, 0, 0)
        end2 = datetime(2026, 5, 12, 10, 0, 0)
        id1 = _add_job("failed", set_="alpha", name="bad1", owner="user_a",
                       detail="zero division", end=end1)
        id2 = _add_job("failed", set_="beta", name="bad2", kind="service",
                       owner="user_b", detail="OOM", end=end2)
        # Add some non-failed jobs that must NOT show up
        _add_job("done")
        _add_job("running")

        r = client.get("/health/failed")
        body = r.json()
        assert body["count"] == 2
        # ordered newest-first by Job.end
        assert body["jobs"][0]["id"] == id2
        assert body["jobs"][1]["id"] == id1
        # full detail per row
        first = body["jobs"][0]
        assert first["set"] == "beta"
        assert first["name"] == "bad2"
        assert first["kind"] == "service"
        assert first["owner"] == "user_b"
        assert first["detail"] == "OOM"
        assert first["end"] == "2026-05-12T10:00:00"

    def test_failed_503_when_db_unavailable(self, client, monkeypatch):
        from app.routers import monitoring
        monkeypatch.setattr(monitoring, "_check_database", lambda: False)
        r = client.get("/health/failed")
        assert r.status_code == 503


class TestMetricsEndpoint:
    def test_metrics_content_type_and_basics(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        # Required metric names emitted unconditionally
        for name in (
            "pyops_uptime_seconds",
            "pyops_info",
            "pyops_database_alive",
            "pyops_runner_alive",
            "pyops_data_dirs_ok",
            "pyops_jobs_total",
            "pyops_schedules",
            "pyops_sets",
            "pyops_gpu_ok",
            "pyops_gpu_reboot_required",
            "pyops_open_fds",
            "pyops_max_fds_soft",
        ):
            assert f"# TYPE {name} gauge" in body, f"{name} missing in /metrics output"
        # Database must be alive in the test env
        assert "pyops_database_alive 1" in body

    def test_metrics_state_counters(self, client):
        """Per-state job counts are emitted for every state in the canonical
        list, even when zero — Zabbix gauge configs assume the series exists."""
        _add_job("todo")
        _add_job("failed", set_="x", name="y", detail="boom")
        r = client.get("/metrics")
        body = r.text
        assert 'pyops_jobs_total{state="todo"} 1' in body
        assert 'pyops_jobs_total{state="failed"} 1' in body
        # zero-count state still emitted
        assert 'pyops_jobs_total{state="cancelled"} 0' in body
        # per-(set,name,state) detail
        assert 'pyops_jobs{set="x",name="y",state="failed"} 1' in body

    def test_metrics_service_jobs_separate(self, client):
        """Service-kind jobs go in their own metric so absence of `running`
        for a known service is alertable independently of task jobs."""
        _add_job("running", kind="service", set_="srv", name="watcher")
        r = client.get("/metrics")
        body = r.text
        assert 'pyops_service_jobs{set="srv",name="watcher",state="running"} 1' in body

    def test_metrics_seconds_since_last_completed_job(self, client):
        """The throughput-stall gauge counts from the newest end time of a
        done/failed/cancelled job; it is absent while no job has completed."""
        assert "pyops_last_completed_seconds" not in client.get("/metrics").text
        _add_job("done", end=datetime.now() - timedelta(minutes=10))
        _add_job("failed", name="t2", end=datetime.now() - timedelta(seconds=30))
        body = client.get("/metrics").text
        assert "# TYPE pyops_last_completed_seconds gauge" in body
        line = next(ln for ln in body.splitlines() if ln.startswith("pyops_last_completed_seconds "))
        assert 30 <= int(line.split()[1]) < 600

    def test_metrics_without_database(self, client, unreachable_db):
        """When the database probe fails, /metrics still answers: the database
        gauge is 0 and the DB-derived series are left out."""
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "pyops_database_alive 0" in r.text
        assert "pyops_jobs_total" not in r.text
        assert "pyops_schedules" not in r.text

    def test_metrics_label_quoting(self, client):
        """Set/name with quotes/backslashes must be escaped per Prometheus spec."""
        _add_job("done", set_='quoted"set', name="back\\slash")
        r = client.get("/metrics")
        body = r.text
        assert 'pyops_jobs{set="quoted\\"set",name="back\\\\slash",state="done"} 1' in body

    def test_metrics_schedules(self, client):
        _add_schedule(True)
        _add_schedule(False)
        _add_schedule(False, name="t2")
        r = client.get("/metrics")
        body = r.text
        assert 'pyops_schedules{active="true"} 1' in body
        assert 'pyops_schedules{active="false"} 2' in body

    def test_metrics_gpu_reboot_required_flag(self, client, monkeypatch):
        """Force the version-mismatch path and verify the dedicated
        gauge flips to 1 (Zabbix can alert on this single number)."""
        from app.routers import monitoring

        def fake_run(cmd, **kwargs):
            class R:
                returncode = 9
                stdout = ""
                stderr = "Failed to initialize NVML: Driver/library version mismatch"
            return R()

        monkeypatch.setattr(monitoring.subprocess, "run", fake_run)
        r = client.get("/metrics")
        body = r.text
        assert "pyops_gpu_reboot_required 1" in body
        assert 'pyops_gpu_ok{status="reboot_required"} 0' in body

    def test_metrics_ulimits_present(self, client):
        """fd/nproc gauges expose current vs soft limit so monitoring can
        compute headroom server-side."""
        r = client.get("/metrics")
        body = r.text
        # Numeric value follows the metric name + space; non-empty.
        for name in ("pyops_open_fds", "pyops_max_fds_soft", "pyops_threads",
                     "pyops_max_nproc_soft"):
            line = next((ln for ln in body.splitlines()
                         if ln.startswith(name + " ")), None)
            assert line is not None, f"{name} missing"
            value = line.split()[1]
            # int parseable, non-negative
            assert int(value) >= 0 or value == "-1"  # -1 = RLIM_INFINITY
