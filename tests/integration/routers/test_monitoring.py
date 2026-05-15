"""Integration tests for monitoring endpoints (`/health`, `/health/failed`, `/metrics`).

These tests drive the FastAPI router via TestClient against the real ORM
backed by an isolated SQLite (`mock_db` fixture). System-level probes
(runner socket, nvidia-smi, ulimits) are exercised against the real test
environment — assertions check structure and the values that ARE
deterministic under that environment, not absolute system values.
"""

import json
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
        import json as _json
        from app.lib import files as files_lib
        git_dir = tmp_path / "fake_git"
        venv_dir = tmp_path / "fake_venv"
        (git_dir / "myset").mkdir(parents=True)
        set_venv = venv_dir / "myset"
        set_venv.mkdir(parents=True)
        warnings_path = set_venv / ".update_warnings.json"
        warnings_path.write_text(_json.dumps({
            "set": "myset",
            "conflicts": {
                "pip": [{
                    "pkg": "requests",
                    "winner": {"spec": "==2.32.0", "extra": [], "source": "new.task.py", "mtime": 200},
                    "losers": [{"spec": "==2.30.0", "extra": [], "source": "old.task.py", "mtime": 100}],
                }],
                "git": [],
            },
            "installed": {"pip": [], "git": []},
        }))

        original_conf_data = files_lib.conf_data
        original_conf_runner = files_lib.conf_runner
        def fake_conf_data():
            d = dict(original_conf_data())
            d["git"] = str(git_dir)
            return d
        def fake_conf_runner():
            d = dict(original_conf_runner())
            d["venv"] = str(venv_dir)
            return d
        monkeypatch.setattr(files_lib, "conf_data", fake_conf_data)
        monkeypatch.setattr(files_lib, "conf_runner", fake_conf_runner)

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
        import json as _json
        from app.lib import files as files_lib
        git_dir = tmp_path / "fake_git"
        venv_dir = tmp_path / "fake_venv"
        (git_dir / "cleanset").mkdir(parents=True)
        set_venv = venv_dir / "cleanset"
        set_venv.mkdir(parents=True)
        (set_venv / ".update_warnings.json").write_text(_json.dumps({
            "set": "cleanset",
            "conflicts": {"pip": [], "git": []},
            "installed": {"pip": [], "git": []},
        }))
        original_conf_data = files_lib.conf_data
        original_conf_runner = files_lib.conf_runner
        def fake_conf_data():
            d = dict(original_conf_data())
            d["git"] = str(git_dir)
            return d
        def fake_conf_runner():
            d = dict(original_conf_runner())
            d["venv"] = str(venv_dir)
            return d
        monkeypatch.setattr(files_lib, "conf_data", fake_conf_data)
        monkeypatch.setattr(files_lib, "conf_runner", fake_conf_runner)

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
