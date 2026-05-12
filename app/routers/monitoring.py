"""Monitoring endpoints (`/health`, `/health/failed`, `/metrics`).

Designed for unauthenticated scraping by an external monitoring system (e.g.
Zabbix). Read-only, no side effects, cheap enough to poll every few seconds.

Responsibilities:
- `/health` returns a status + summary JSON suitable for human + machine
  consumption. HTTP 503 when the database is unreachable; otherwise 200 with
  status `ok` or `degraded`.
- `/health/failed` returns every job currently in `failed` state with full
  detail (set, name, kind, owner, error message, timestamps). Monitoring
  filters as it sees fit; we never paginate or limit here so alerts can use
  per-set / per-name / per-error-substring rules.
- `/metrics` exposes Prometheus exposition format (text/plain;
  version=0.0.4) with the same data plus extra dimensions.
"""

import json
import logging
import os
import resource
import subprocess
import time
from datetime import datetime

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_200_OK,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from ..lib import files
from ..orm.database import get_db_engine
from ..orm.job import Job
from ..orm.schedule import Schedule

logger = logging.getLogger(__name__)
router = APIRouter()

_PROCESS_START_TIME = time.time()
_RUNNER_SOCKET = "/tmp/pyops/runner.sock"

# Canonical job state list. Keep aligned with the runner state machine
# (see app/runner.py); states the runner ever writes must appear here so
# /metrics emits a `pyops_jobs_total{state=...} 0` for each even when the
# DB has no rows of that state — Zabbix gauges break otherwise.
_JOB_STATES = (
    "todo", "dequeued", "loading", "running",
    "done", "failed", "cancel", "cancelling", "cancelled",
    "remove", "removed",
)

# When ulimit usage exceeds these fractions of the soft limit, /health flips
# to degraded. The thresholds are intentionally conservative so an operator
# can react before the next subprocess.fork or socket() actually fails.
_ULIMIT_WARN_FRACTION = 0.8


def _check_database() -> bool:
    """Cheap reachability probe: SELECT 1."""
    try:
        with Session(get_db_engine()) as session:
            session.execute(select(1)).scalar()
        return True
    except Exception as e:
        logger.warning("monitoring: database check failed: %s", e)
        return False


def _check_runner_socket() -> bool:
    """The runner subprocess holds /tmp/pyops/runner.sock when alive.

    `os.path.exists` alone is not enough — a SIGKILL'd runner leaves a stale
    socket file behind. We also confirm a process is bound to it.
    """
    if not os.path.exists(_RUNNER_SOCKET):
        return False
    try:
        for proc in psutil.process_iter(["pid"]):
            try:
                for c in proc.net_connections(kind="unix"):
                    if c.laddr == _RUNNER_SOCKET:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.warning("monitoring: runner socket scan failed: %s", e)
    return False


def _check_data_dirs() -> bool:
    """Configured data dirs exist and are writable."""
    try:
        data = files.conf_data()
        for key in ("jobs", "git"):
            d = data.get(key)
            if not d or not os.path.isdir(d) or not os.access(d, os.W_OK):
                return False
        return True
    except Exception as e:
        logger.warning("monitoring: data-dirs check failed: %s", e)
        return False


def _check_ulimits() -> dict:
    """Track file-descriptor and process-count headroom against the soft limit.

    The historical incident this guards against: pyops's runner spawned more
    job_runner subprocesses + their per-job ZMQ sockets than the gunicorn
    process group's RLIMIT_NOFILE allowed, after which `accept()` started
    failing silently.
    """
    out = {"status": "ok", "fd": {}, "nproc": {}}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        # /proc/self/fd is the most accurate count (psutil.num_fds is Linux-equivalent)
        try:
            current = len(os.listdir("/proc/self/fd"))
        except Exception:
            current = psutil.Process().num_fds()
        out["fd"] = {"current": current, "soft": soft, "hard": hard}
        if soft and current >= soft * _ULIMIT_WARN_FRACTION:
            out["status"] = "degraded"
    except Exception as e:
        out["fd"] = {"error": str(e)}
        out["status"] = "unknown"

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        # nproc-limit applies per real-uid; count this process's threads as a
        # cheap proxy. A more accurate count would walk /proc but that's expensive.
        current = psutil.Process().num_threads()
        out["nproc"] = {"current_threads": current, "soft": soft, "hard": hard}
        if soft and soft != resource.RLIM_INFINITY and current >= soft * _ULIMIT_WARN_FRACTION:
            out["status"] = "degraded"
    except Exception as e:
        out["nproc"] = {"error": str(e)}

    return out


def _check_gpu() -> dict:
    """Detect GPU availability and the specific 'host reboot needed' state.

    Recognized failure modes inside the pyops container:
      - `nvidia-smi` works → ok
      - `Driver/library version mismatch` → kernel module updated but the
        running kernel still has the previous version mapped; needs host
        reboot. This is the case the operator hit before.
      - `nvidia-smi` not in PATH but /dev/nvidia* present → toolchain incomplete
      - no nvidia devices at all → no_gpu_expected (treated as ok if the
        container was never supposed to have a GPU)
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "GPU" in r.stdout:
            return {"status": "ok", "detail": r.stdout.strip().splitlines()[0]}
        msg = (r.stderr or r.stdout or "").strip()
        if "Driver/library version mismatch" in msg:
            return {
                "status": "reboot_required",
                "detail": "kernel-module ↔ userspace driver mismatch — host reboot required",
            }
        return {"status": "fail", "detail": msg[:500] or f"nvidia-smi exit {r.returncode}"}
    except FileNotFoundError:
        if os.path.exists("/dev/nvidia0") or os.path.exists("/dev/nvidiactl"):
            return {"status": "missing_smi", "detail": "/dev/nvidia* present but nvidia-smi not in PATH"}
        return {"status": "no_gpu_expected", "detail": "no nvidia-smi binary, no nvidia devices"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "detail": "nvidia-smi did not respond within 5s"}
    except Exception as e:
        return {"status": "unknown", "detail": str(e)[:200]}


def _job_state_counts(session) -> dict:
    rows = session.execute(
        select(Job.state, func.count(Job.id)).group_by(Job.state)
    ).all()
    counts = {state: 0 for state in _JOB_STATES}
    for state, count in rows:
        counts[state] = counts.get(state, 0) + count
    return counts


def _job_set_name_state_counts(session) -> list:
    """For per-(set,name,state) Prometheus labels."""
    rows = session.execute(
        select(Job.set, Job.name, Job.state, func.count(Job.id))
            .group_by(Job.set, Job.name, Job.state)
    ).all()
    return [(s, n, st, c) for (s, n, st, c) in rows]


def _service_job_counts(session) -> list:
    """Service-kind jobs broken down by (set, name, state). They're meant to
    run continuously; absence of `running` for a known service is suspicious."""
    rows = session.execute(
        select(Job.set, Job.name, Job.state, func.count(Job.id))
            .where(Job.kind == "service")
            .group_by(Job.set, Job.name, Job.state)
    ).all()
    return [(s, n, st, c) for (s, n, st, c) in rows]


def _schedule_counts(session) -> tuple:
    rows = session.execute(
        select(Schedule.active, func.count(Schedule.id)).group_by(Schedule.active)
    ).all()
    active = inactive = 0
    for is_active, count in rows:
        if is_active:
            active += count
        else:
            inactive += count
    return active, inactive


def _set_count(session) -> int:
    return session.execute(select(func.count(distinct(Job.set)))).scalar() or 0


def _stuck_indicators(session) -> dict:
    """Anomaly indicators that don't fit a single counter:
    - oldest_todo_age_seconds — how long the head of the queue has waited.
      We derive `created` from the trailing timestamp suffix in `Job.id`
      because the schema doesn't have a `created` column; the runner's id
      generator embeds a `_YYYYMMDDTHHMMSSffffff` tail.
    - last_completed_at / last_completed_seconds — gap since last terminal
      transition. A long gap on a busy queue is a throughput stall signal.
    """
    now = datetime.utcnow()

    oldest_todo_age = None
    head = session.execute(
        select(Job.id).where(Job.state == "todo").order_by(Job.id.asc()).limit(1)
    ).scalar()
    if head:
        try:
            ts_part = head.split("_")[-1][:15]  # YYYYMMDDTHHMMSS
            created = datetime.strptime(ts_part, "%Y%m%dT%H%M%S")
            oldest_todo_age = int((now - created).total_seconds())
        except Exception:
            pass

    last_end = session.execute(
        select(func.max(Job.end)).where(Job.state.in_(("done", "failed", "cancelled")))
    ).scalar()
    last_completed_at = last_end.isoformat() if last_end else None
    last_completed_seconds = int((now - last_end).total_seconds()) if last_end else None

    return {
        "oldest_todo_age_seconds": oldest_todo_age,
        "last_completed_at": last_completed_at,
        "last_completed_seconds": last_completed_seconds,
    }


def _version_safe() -> str:
    try:
        return files.version()
    except Exception:
        return "unknown"


@router.get("/health", tags=["monitoring"])
def health():
    """Liveness/readiness summary.

    Returns 503 when the database is unreachable — that's the only
    fail-state where pyops cannot answer ANY useful question. All other
    degradations (failed jobs present, runner socket missing, ulimit close
    to exhaustion, GPU mismatch) surface as `status: degraded` with HTTP
    200 so the monitoring system can grade severity itself.
    """
    db_ok = _check_database()
    runner_ok = _check_runner_socket()
    dirs_ok = _check_data_dirs()
    ulimits = _check_ulimits()
    gpu = _check_gpu()

    summary = {}
    stuck = {}
    failed_count = 0
    if db_ok:
        try:
            with Session(get_db_engine()) as session:
                jobs = _job_state_counts(session)
                failed_count = jobs.get("failed", 0)
                active, inactive = _schedule_counts(session)
                sets = _set_count(session)
                stuck = _stuck_indicators(session)
                summary = {
                    "jobs": jobs,
                    "schedules": {"active": active, "inactive": inactive},
                    "sets": sets,
                }
        except Exception as e:
            logger.exception("monitoring: /health DB summary failed: %s", e)
            db_ok = False

    degraded = (
        not runner_ok
        or not dirs_ok
        or failed_count > 0
        or ulimits["status"] != "ok"
        or gpu["status"] not in ("ok", "no_gpu_expected")
    )

    if not db_ok:
        status_str = "fail"
    elif degraded:
        status_str = "degraded"
    else:
        status_str = "ok"

    body = {
        "status": status_str,
        "version": _version_safe(),
        "uptime_seconds": int(time.time() - _PROCESS_START_TIME),
        "checks": {
            "database": "ok" if db_ok else "fail",
            "runner_socket": "ok" if runner_ok else "missing",
            "data_dirs": "ok" if dirs_ok else "missing",
            "ulimits": ulimits["status"],
            "gpu": gpu["status"],
        },
        "ulimits": ulimits,
        "gpu": gpu,
        "summary": summary,
        "stuck": stuck,
    }

    code = HTTP_503_SERVICE_UNAVAILABLE if status_str == "fail" else HTTP_200_OK
    return JSONResponse(content=body, status_code=code)


@router.get("/health/failed", tags=["monitoring"])
def health_failed():
    """All failed jobs in full detail. Monitoring filters as needed.

    Intentionally unpaginated — the use case is alerting/triage, not UI
    rendering. If the failed count grows large enough that response size
    becomes a concern, the operator should be acting on the alert anyway.
    """
    if not _check_database():
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable")

    with Session(get_db_engine()) as session:
        rows = session.execute(
            select(
                Job.id, Job.set, Job.name, Job.kind, Job.owner,
                Job.detail, Job.start, Job.end,
            )
            .where(Job.state == "failed")
            .order_by(Job.end.desc().nullslast(), Job.id.desc())
        ).all()
    return {
        "count": len(rows),
        "jobs": [
            {
                "id": r.id,
                "set": r.set,
                "name": r.name,
                "kind": r.kind,
                "owner": r.owner,
                "detail": r.detail,
                "start": r.start.isoformat() if r.start else None,
                "end": r.end.isoformat() if r.end else None,
            }
            for r in rows
        ],
    }


def _q(s) -> str:
    """Quote a Prometheus label value per exposition format spec."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", tags=["monitoring"])
def metrics():
    """Prometheus exposition format (`text/plain; version=0.0.4`).

    Cardinality note: `pyops_jobs` carries `set` AND `name` labels, which
    grows with the catalog of scriptlets times job-state. For modest
    deployments (<10k unique (set,name) pairs) this is fine for both
    Prometheus and Zabbix scrapers. If a deployment ever blows up there,
    we'd add a coarser `pyops_jobs_total{state}` only fallback flag.
    """
    lines = []

    def emit(help_text, metric_type, name):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

    emit("Process uptime in seconds", "gauge", "pyops_uptime_seconds")
    lines.append(f"pyops_uptime_seconds {int(time.time() - _PROCESS_START_TIME)}")

    emit("Build/runtime info", "gauge", "pyops_info")
    lines.append(f'pyops_info{{version="{_q(_version_safe())}"}} 1')

    db_ok = _check_database()
    runner_ok = _check_runner_socket()
    dirs_ok = _check_data_dirs()
    ulimits = _check_ulimits()
    gpu = _check_gpu()

    emit("Database reachable (1=yes, 0=no)", "gauge", "pyops_database_alive")
    lines.append(f"pyops_database_alive {1 if db_ok else 0}")

    emit("Runner subprocess holding /tmp/pyops/runner.sock (1=yes, 0=no)", "gauge", "pyops_runner_alive")
    lines.append(f"pyops_runner_alive {1 if runner_ok else 0}")

    emit("Configured data dirs exist and writable (1=yes, 0=no)", "gauge", "pyops_data_dirs_ok")
    lines.append(f"pyops_data_dirs_ok {1 if dirs_ok else 0}")

    fd = ulimits.get("fd", {})
    if "current" in fd:
        emit("Open file descriptors", "gauge", "pyops_open_fds")
        lines.append(f"pyops_open_fds {fd['current']}")
        emit("RLIMIT_NOFILE soft limit", "gauge", "pyops_max_fds_soft")
        lines.append(f"pyops_max_fds_soft {fd['soft']}")
        emit("RLIMIT_NOFILE hard limit", "gauge", "pyops_max_fds_hard")
        lines.append(f"pyops_max_fds_hard {fd['hard']}")

    nproc = ulimits.get("nproc", {})
    if "current_threads" in nproc:
        emit("Threads in current process (proxy for nproc usage)", "gauge", "pyops_threads")
        lines.append(f"pyops_threads {nproc['current_threads']}")
        emit("RLIMIT_NPROC soft limit", "gauge", "pyops_max_nproc_soft")
        lines.append(f"pyops_max_nproc_soft {nproc['soft']}")

    # GPU: emit ok/reboot_required/fail/missing as numeric for scalar alerts
    gpu_status_map = {
        "ok": 1, "no_gpu_expected": 1,
        "reboot_required": 0, "fail": 0,
        "missing_smi": 0, "timeout": 0, "unknown": 0,
    }
    emit("GPU usable (1=yes, 0=fail/mismatch/missing/timeout)", "gauge", "pyops_gpu_ok")
    lines.append(
        f'pyops_gpu_ok{{status="{_q(gpu["status"])}"}} {gpu_status_map.get(gpu["status"], 0)}'
    )
    emit("GPU host reboot required (driver/library mismatch detected)", "gauge", "pyops_gpu_reboot_required")
    lines.append(f"pyops_gpu_reboot_required {1 if gpu['status'] == 'reboot_required' else 0}")

    if db_ok:
        try:
            with Session(get_db_engine()) as session:
                state_counts = _job_state_counts(session)
                emit("Job count by state (no per-set/per-name breakdown)", "gauge", "pyops_jobs_total")
                for state, count in state_counts.items():
                    lines.append(f'pyops_jobs_total{{state="{_q(state)}"}} {count}')

                emit("Job count by set, name and state", "gauge", "pyops_jobs")
                for s, n, st, c in _job_set_name_state_counts(session):
                    lines.append(
                        f'pyops_jobs{{set="{_q(s)}",name="{_q(n)}",state="{_q(st)}"}} {c}'
                    )

                emit("Service-kind jobs (long-running) by set, name, state", "gauge", "pyops_service_jobs")
                for s, n, st, c in _service_job_counts(session):
                    lines.append(
                        f'pyops_service_jobs{{set="{_q(s)}",name="{_q(n)}",state="{_q(st)}"}} {c}'
                    )

                active, inactive = _schedule_counts(session)
                emit("Schedule count by activation", "gauge", "pyops_schedules")
                lines.append(f'pyops_schedules{{active="true"}} {active}')
                lines.append(f'pyops_schedules{{active="false"}} {inactive}')

                emit("Distinct sets seen in job table", "gauge", "pyops_sets")
                lines.append(f"pyops_sets {_set_count(session)}")

                stuck = _stuck_indicators(session)
                if stuck["oldest_todo_age_seconds"] is not None:
                    emit("Age of oldest queued (todo) job in seconds", "gauge", "pyops_oldest_todo_age_seconds")
                    lines.append(f"pyops_oldest_todo_age_seconds {stuck['oldest_todo_age_seconds']}")
                if stuck["last_completed_seconds"] is not None:
                    emit("Seconds since last completed (done/failed/cancelled) job", "gauge", "pyops_last_completed_seconds")
                    lines.append(f"pyops_last_completed_seconds {stuck['last_completed_seconds']}")
        except Exception as e:
            logger.exception("monitoring: /metrics DB section failed: %s", e)

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
