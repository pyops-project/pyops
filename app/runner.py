import signal, time, os, sys, re
import traceback
import croniter
import subprocess
import json
from datetime import datetime, timedelta
from io import TextIOWrapper
import threading
from typing import Any

import zmq, msgpack
import psutil

from sqlalchemy import null, select, delete, and_, or_, case, func
from sqlalchemy.orm import Session
import sqlalchemy.exc

from app.orm.database import get_db_engine
from app.orm.job import Job
from app.orm.schedule import Schedule

from app.lib import runner as lib_runner, files
# Import Git helpers for code loading.
from app.lib import git

def commit_with_retry(session, max_retries=5, backoff=0.1):
    for attempt in range(max_retries):
        try:
            session.commit()
            return
        except sqlalchemy.exc.OperationalError as e:
            if 'database is locked' in str(e).lower():
                if attempt < max_retries - 1:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                else:
                    raise
            else:
                raise

from app.lib import pyops
from app.lib.sets import ensure_set_environment
from app.lib import job as job_lib


# Persistent services that run without a `Schedule` row. The schedule loop
# re-enqueues each entry whenever it isn't already running, gated by a
# fixed `<3 failed` rule (schedule services use their `max_fail_n`). The operator
# unblocks a stuck essential service by deleting its failed records from
# the Monitor (which removes them from `find(..., "service", ["failed"])`).
PYOPS_ESSENTIAL_SERVICES = [
    {"set": "pyops", "name": "data", "owner": "system"},
]

import logging
from contextlib import redirect_stdout, redirect_stderr
logger = logging.getLogger(__name__)

class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        # Drop the shutdown flag immediately so any enqueue() call from a
        # gunicorn worker (HTTP) or the pyops data service (HTTP back-
        # channel) gets refused from this point on. Created here, removed in
        # main()'s exit path.
        try:
            os.makedirs(os.path.dirname(lib_runner.SHUTDOWN_FLAG_PATH), exist_ok=True)
            with open(lib_runner.SHUTDOWN_FLAG_PATH, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            # The flag is best-effort: if /tmp is unwritable, schedule() is
            # still gated by kill_now and the rest of the shutdown sequence
            # works. Don't crash the signal handler over it.
            pass
        self.kill_now = True


SHUTDOWN_SIGKILL_DETAIL = "did not respond to shutdown SIGTERM"


def _shutdown_job_processes(processes: dict[int, subprocess.Popen], timeout: float,
                             runner_socket=None, runner_poller=None):
    """Bring all running job subprocesses to a stop, synchronously.

    Approach: let the natural completion flow drive state updates. We
    SIGTERM each process tree and then wait, draining ZMQ completion
    messages from the runner socket as services exit. A service that
    handles SIGTERM via its ServiceListener loops out of `listen()`,
    runs through job_runner's wrap-up which calls
    `job_lib.send_completion`, and that completion arrives over ZMQ
    here — finishJob updates the DB to 'done'/'failed' on its own.

    Every live process the runner started is stopped, whether or not a
    job row still names it by pid: a job whose cancel is in flight (its
    row's pid is cleared, and the termination thread dies with the
    runner) or whose row is gone must not outlive the runner. Rows are
    updated only for the processes a row names.

    The processes still alive past `timeout` (runner.shutdown_timeout_seconds)
    are SIGKILL'd together; a row of such a process is marked 'failed'
    (see the final sweep below).

    Note on zombies: psutil.Process.is_running() returns True for
    zombie processes — we use the subprocess.Popen object's poll()
    instead, which reaps the zombie and reports exit cleanly.

    Required: runner_socket+runner_poller. Without them, services
    block on send_completion's REQ recv() forever and we always end
    up SIGKILL'ing.
    """
    if not processes:
        return

    # Snapshot the live processes and the job rows naming them — no DB
    # writes here, just identification. A live process without such a row
    # (None) is stopped all the same.
    live: dict[int, str | None] = {}
    with Session(get_db_engine()) as session:
        for pid, p in list(processes.items()):
            if p.poll() is not None:
                continue
            job = session.query(Job).filter_by(pid=pid).first()
            if job is None:
                logger.info("shutdown: pid %d has no job row naming it; stopped without a row update", pid)
            live[pid] = job.id if job is not None else None
    targets: dict[int, str] = {pid: job_id for pid, job_id in live.items() if job_id is not None}

    if not live:
        return

    # Termination starts now: a completion a job reports from here on
    # cannot override a cancel committed on its row (see finishJob).
    if targets:
        with Session(get_db_engine()) as session:
            session.query(Job).filter(Job.id.in_(list(targets.values()))).update(
                {Job.term_start: datetime.now()}, synchronize_session=False)
            commit_with_retry(session)

    for pid, job_id in live.items():
        logger.info("shutdown: SIGTERM group of job %s pid %d", job_id, pid)
        try:
            job_lib.kill_proc_group(pid, signal.SIGTERM)
        except Exception as e:
            logger.warning("shutdown: SIGTERM failed for %s (pid %d): %s", job_id, pid, e)

    deadline = time.time() + timeout
    still_alive: dict[int, str | None] = dict(live)
    while still_alive and time.time() < deadline:
        if runner_socket is not None and runner_poller is not None:
            events = runner_poller.poll(0)
            while events:
                for _sock, _ in events:
                    try:
                        receive_and_process(runner_socket)
                    except Exception as e:
                        logger.warning("shutdown: ZMQ drain failed: %s", e)
                events = runner_poller.poll(0)

        # A group is considered drained when killpg(0) reports
        # ProcessLookupError. We check both the leader's Popen.poll()
        # (cheap, reaps zombies) and the group as a whole (catches
        # any subprocess the user's `param` spawned which we don't
        # have a Popen handle for, e.g. a debugpy adapter).
        survivors: dict[int, str | None] = {}
        for pid, job_id in still_alive.items():
            p = processes.get(pid)
            leader_dead = (p is not None and p.poll() is not None)
            if leader_dead and not job_lib.proc_group_alive(pid):
                continue
            survivors[pid] = job_id
        still_alive = survivors
        if still_alive:
            time.sleep(0.05)

    sigkilled: set[int] = set()
    if still_alive:
        for pid, job_id in still_alive.items():
            logger.warning("shutdown: SIGKILL group of job %s pid %d (graceful timeout %.1fs exceeded)", job_id, pid, timeout)
            try:
                job_lib.kill_proc_group(pid, signal.SIGKILL)
            except Exception as e:
                logger.warning("shutdown: SIGKILL failed for %s (pid %d): %s", job_id, pid, e)
            sigkilled.add(pid)

        sigkill_deadline = time.time() + 2.0
        while still_alive and time.time() < sigkill_deadline:
            leftover: dict[int, str | None] = {}
            for pid, job_id in still_alive.items():
                p = processes.get(pid)
                leader_dead = (p is not None and p.poll() is not None)
                if leader_dead and not job_lib.proc_group_alive(pid):
                    continue
                leftover[pid] = job_id
            still_alive = leftover
            if still_alive:
                time.sleep(0.05)

    # Final sweep — drives each row to the right state for the next boot:
    #   • SIGKILL'd: the service didn't respond to SIGTERM. Mark `failed`
    #     so it's visible in the monitor and counts toward the per-service
    #     failed-count gate. A job that had already reported `failed` keeps
    #     its message; the kill is appended to it.
    #   • `cancel` / `cancelling`: an operator cancel was mid-flight when
    #     shutdown started. Finalise to `cancelled` so the next-start
    #     gate honours the operator intent (cancelled blocks restart).
    #   • Already terminal in a "preserve across restarts" sense
    #     (`cancelled`, `failed`): leave alone.
    #   • Everything else (`done`, `remove`, `running` without completion,
    #     etc.): a clean container shutdown shouldn't leave behind state
    #     that blocks the schedule loop from re-enqueueing — DELETE the
    #     row so the service auto-restarts on next boot. The workspace
    #     under `data/jobs/{set}/{job_id}/` stays on disk for post-mortem.
    with Session(get_db_engine()) as session:
        for pid, job_id in targets.items():
            job = session.get(Job, job_id)
            if job is None:
                continue
            if pid in sigkilled and job.state == "failed":
                # Its failure was already reported: keep that outcome and
                # its message, note the kill below it.
                session.query(Job).filter(Job.id == job_id).update({
                    Job.detail: appended_detail(SHUTDOWN_SIGKILL_DETAIL),
                    Job.pid: None,
                }, synchronize_session=False)
            elif pid in sigkilled:
                job.state = "failed"
                job.detail = SHUTDOWN_SIGKILL_DETAIL
                job.end = datetime.now()
                job.pid = None
            elif job.state in ("cancel", "cancelling"):
                job.state = "cancelled"
                job.end = datetime.now()
                job.pid = None
            elif job.state in ("cancelled", "failed"):
                pass
            else:
                session.delete(job)
        commit_with_retry(session)

def terminate_job_processes(job_id: str, pid: int, owner: str, graceful_timeout: float = 15.0):
    """Terminate a job and every subprocess it spawned, then update DB.

    The job was started with `preexec_fn=os.setsid`, so `pid` is also
    its process-group id. SIGTERM to the group reaches the job process
    AND any descendants (e.g. a debugpy adapter started by the user's
    `param`, a gdbserver, etc.) atomically — `os.killpg` is one syscall
    and the kernel walks its own group table, so there's no window
    where the parent dies first and children re-parent to PID 1 with
    the kill missing them.

    Args:
        job_id (str): The job ID.
        pid (int): The process ID of the job (== PGID).
        owner (str): The job owner.
        graceful_timeout (float): Timeout in seconds for graceful termination.
    """
    logger.info("ENTER terminate_job_processes job_id=%s pid=%s owner=%s", job_id, pid, owner)
    # Guard: Skip if job already in terminal state or pid None
    with Session(get_db_engine()) as session:
        job = session.get(Job, job_id)
        if job and job.state in ['failed', 'done', 'cancelled', 'remove']:
            logger.debug("terminate_job_processes: skip %s state=%s pid=%s", job_id, job.state, job.pid)
            return

    job_lib.kill_proc_group(pid, signal.SIGTERM)

    # Wait for the whole group to drain (not just the leader). A child
    # that survives the parent is exactly the orphan-port-hold case we
    # added killpg to prevent — keep polling killpg(0) until the group
    # is empty.
    import time
    start_time = time.time()
    while (time.time() - start_time) < graceful_timeout:
        if not job_lib.proc_group_alive(pid):
            postTerm(job_id)
            return
        time.sleep(0.5)

    # Force kill the whole group.
    job_lib.kill_proc_group(pid, signal.SIGKILL)
    postTerm(job_id, killed=True)
    
def setup_runner_socket(sock_path="/tmp/pyops/runner.sock"):
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(f"ipc://{sock_path}")
    poller = zmq.Poller()
    poller.register(socket, flags=zmq.POLLIN)
    return context, socket, poller

def receive_and_process(socket):
    identity = socket.recv()
    socket.recv()  # empty frame
    packed = socket.recv()
    msg = msgpack.unpackb(packed)
    try:
        logger.debug(f"main: processing ZMQ msg {msg}")
        # `state=running` is the param-done signal: the job's `param` block
        # has finished executing (which for kind=debug means the client-
        # supplied debugger bootstrap is up). It's not a terminal state and
        # must not run through finishJob (which clears pid and sets end).
        if msg.get("state") == "running":
            markRunning(msg["id"])
        else:
            finishJob(msg["id"], msg["state"], msg["detail"],
                      finished=datetime.fromtimestamp(msg["time"]))
    except Exception as e:
        logger.exception("Failed to process completion for job %s: %s", msg.get("id"), e)
    # Send REP
    socket.send(identity, zmq.SNDMORE)
    socket.send(b'', zmq.SNDMORE)
    socket.send(b'ack')

INCOMPLETE_ON_STARTUP_DETAIL = "incomplete execution detected on startup"


class JobStateChanged(Exception):
    """The claimed job left `dequeued` before it was loaded — a cancel or remove
    landed in between. Not an error: the job is not spawned and keeps its state
    and detail; the cancel path (getNextToTerm) finalises it."""


def handleOddJobs():
    """Reconcile non-terminal rows left behind by a previous unclean run.

    Transient states (`dequeued`, `loading`) get reset to `todo` so the
    new runner can pick them up. A `running` job that wrote its result file
    (`result.json`, see app.lib.job.send_completion) finished but was never
    recorded: it gets that result (state, detail, end). Any other `running`
    job gets marked `failed` with a sentinel detail
    (`INCOMPLETE_ON_STARTUP_DETAIL`) and `end` set to the detection time,
    so it ranks as the newest failure. `cancel`/`cancelling`
    get finalised to `cancelled` — the daemon-thread terminate from a
    cancel HTTP got killed by an immediately-following shutdown, or the
    previous run crashed mid-terminate; the process is gone by the time
    we see this on startup.

    To keep the per-service failed-count gate from accumulating ghosts
    across repeated unclean container restarts (docker stop_grace_period
    exhausted before the runner could clean up), we deduplicate after
    the sweep: at most ONE row with the incomplete-on-startup detail
    survives per `(set, name, owner, kind)` tuple. Real failures with
    real tracebacks are untouched.

    Terminal states (`done`, `failed`-with-other-detail, `cancelled`,
    `remove`, `removed`) are NOT touched — they're either history we
    want to preserve (for the failed-count gate and cancelled-blocks-restart) or
    transient cleanup states that purgeLeftoverServiceJobs handles.
    """
    new_incomplete = []  # (id, set, name, owner, kind) of rows we just marked incomplete
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            or_(Job.state == "dequeued", Job.state == "loading",
                Job.state == "running",
                Job.state == "cancel", Job.state == "cancelling")
        ).order_by(Job.id.asc())

        jobs = session.scalars(stmt).all()

        for job in jobs:
            if job.state in ("dequeued", "loading"):
                job.state = "todo"
            elif job.state in ("cancel", "cancelling"):
                job.state = "cancelled"
                job.detail = (job.detail or "") + " (orphan cancel cleaned on startup)"
            else:  # running
                result = job_lib.read_result(os.path.join(files.conf_data()["jobs"], job.set, job.id))
                if result is not None:
                    # The job finished; the previous runner never recorded it.
                    job.state = result["state"]
                    job.detail = result["detail"]
                    job.end = datetime.fromtimestamp(result["time"])
                    continue
                job.state = "failed"
                job.detail = INCOMPLETE_ON_STARTUP_DETAIL
                job.end = datetime.now()
                new_incomplete.append((job.id, job.set, job.name, job.owner, job.kind))
        commit_with_retry(session)

    if new_incomplete:
        with Session(get_db_engine()) as session:
            for job_id, set_, name_, owner_, kind_ in new_incomplete:
                # Delete prior `failed` rows for the same identity tuple
                # whose detail is exactly the incomplete-on-startup sentinel.
                # Real failures (different detail) survive.
                session.execute(
                    delete(Job).where(
                        and_(Job.id != job_id,
                             Job.set == set_,
                             Job.name == name_,
                             Job.owner == owner_,
                             Job.kind == kind_,
                             Job.state == "failed",
                             Job.detail == INCOMPLETE_ON_STARTUP_DETAIL)
                    )
                )
            commit_with_retry(session)

def purgeLeftoverServiceJobs():
    """Clean up stuck `remove`/`removed` service rows from a previous run.

    A service exiting cleanly with drop_done=True goes through
    `done` → `remove` → `removed` → row deleted (postTerm + disposeJobs),
    but the daemon thread that drives that finalisation can be killed
    when the runner exits within its own shutdown window. The row gets
    stuck in `remove` and then later blocks the schedule loop from
    re-enqueueing the service. This sweep finishes that cleanup.

    Terminal `failed` / `done` / `cancelled` rows are preserved — the
    schedule-loop's failed-count gate and the cancelled-blocks-restart
    rule both rely on those rows surviving across restarts.
    """
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            and_(Job.kind == "service",
                 or_(Job.state == "remove", Job.state == "removed"))
        ).order_by(Job.id.asc())

        jobs = session.scalars(stmt).all()

        for job in jobs:
            logger.debug(f"Purging stuck-cleanup service job {job.id} in state {job.state}")
            session.delete(job)
            lib_runner.drop(job.id, job.set)
        commit_with_retry(session)

def getNextToDo():
    with Session(get_db_engine()) as session:
        logger.debug(f"getNextToDo: session ID {id(session)} at {datetime.now()}")
        stmt = select(Job.id).where(
            Job.state == "todo"
        ).order_by(Job.id.asc())

        job_id = session.scalar(stmt)

        if job_id is None:
            return None

        # Conditional claim: only flip 'todo' → 'dequeued'. If a cancel raced in
        # between SELECT and UPDATE, the WHERE clause excludes us, affected=0, and
        # we skip — getNextToTerm will pick the cancel up next iteration.
        affected = session.query(Job).filter(
            Job.id == job_id,
            Job.state == "todo",
        ).update({Job.state: "dequeued"}, synchronize_session=False)
        commit_with_retry(session)
        logger.debug(f"getNextToDo: CAS claim for {job_id} affected={affected} at {datetime.now()}")

        if affected == 0:
            return None
        return job_id

def assignJob(job_id: str, pid: int):
    """Record the spawned subprocess pid + start time. State stays at
    `loading` here — the flip to `running` happens later, when the job
    process sends a `state=running` ZMQ completion after its `param`
    block finished executing. That signal lives in `markRunning`.

    The cancel-race protection is implicit: this UPDATE doesn't touch
    `Job.state`, so a cancel that committed between loadJob and here is
    preserved untouched.
    """
    logger.debug(f"assignJob(job_id='{job_id}' pid={pid})")

    with Session(get_db_engine()) as session:
        logger.debug(f"assignJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        old_pid = job.pid

        session.query(Job).filter(Job.id == job_id).update({
            Job.pid: pid,
            Job.start: datetime.now(),
        }, synchronize_session=False)
        logger.debug(f"assignJob: before commit, session ID {id(session)}, job.pid {old_pid} to {pid} for {job_id} at {datetime.now()}")
        commit_with_retry(session)
        logger.debug(f"assignJob: after commit, session ID {id(session)}, committed pid for {job_id} at {datetime.now()}")


def markRunning(job_id: str):
    """Flip `loading` → `running` on the param-done signal from the job
    process. Idempotent: a duplicate signal (or a signal arriving after
    a cancel has already committed) leaves a terminal/cancel state
    untouched via the same CASE protection finishJob uses."""
    logger.debug(f"markRunning(job_id='{job_id}')")
    with Session(get_db_engine()) as session:
        session.query(Job).filter(Job.id == job_id).update({
            Job.state: case(
                (Job.state.in_((
                    "cancel", "cancelling", "cancelled",
                    "done", "failed", "remove", "removed",
                )), Job.state),
                else_="running",
            ),
        }, synchronize_session=False)
        commit_with_retry(session)

def loadJob(job_id: str):
    """Loads job code from Git (unstaged if kind=="draft", else committed).

    Uses an atomic conditional UPDATE on (state == 'dequeued') to flip to 'loading'.
    If a cancel races in while we read the code from disk, the WHERE clause excludes
    us and we raise — leaving the cancel state intact for the term-loop to honour.
    """
    logger.debug(f"loadJob(job_id={job_id})")

    with Session(get_db_engine()) as session:
        logger.debug(f"loadJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        if job is None:
            raise Exception(f"Job {job_id} not existing")
        if job.state != "dequeued":
            raise JobStateChanged(f"Job {job_id} state changed to {job.state} before loadJob")
        # Snapshot fields needed for the return tuple — read before we release the
        # ORM identity for this session via the conditional UPDATE.
        owner, setname, name, kind, param, overrides, max_retries = (
            job.owner, job.set, job.name, job.kind, job.param, job.overrides, job.max_retries
        )
        repo = git.get_repo_path(setname)
        main_key = f"{setname}::{name}"
        override_code = None
        override_dict = None
        if overrides:
            try:
                override_dict = json.loads(overrides)
                if not isinstance(override_dict, dict):
                    raise Exception("Overrides must be a valid JSON dict")
            except json.JSONDecodeError:
                raise Exception("Invalid overrides JSON")
            if main_key in override_dict:
                override_code = override_dict[main_key]
        # debug/draft/test/testdebug all read unstaged: the operator is
        # iterating on local code that may not be committed yet, exactly
        # the same use case across the four preview/diagnostic kinds.
        committed = kind not in ("debug", "draft", "test", "testdebug")
        if override_code is not None:
            code = override_code
        else:
            git.resolve_scriptlet(repo, name, "task")
            code = git.read_content(repo, name, "task", committed=committed)

        logger.debug(f"loadJob: before CAS commit for {job_id} at {datetime.now()}")
        affected = session.query(Job).filter(
            Job.id == job_id,
            Job.state == "dequeued",
        ).update({
            Job.state: "loading",
            Job.start: datetime.now(),
        }, synchronize_session=False)
        commit_with_retry(session)
        logger.debug(f"loadJob: after CAS commit, affected={affected} for {job_id} at {datetime.now()}")

        if affected == 0:
            session.expire_all()
            current = session.get(Job, job_id)
            current_state = current.state if current else "<missing>"
            raise JobStateChanged(f"Job {job_id} state changed to {current_state} during loadJob")

        return owner, setname, name, kind, param, code, overrides, max_retries

def cleanup_drop_done_jobs():
    logger.debug("cleanup_drop_done_jobs: starting")
    now = datetime.now()
    delay_seconds = files.conf_runner()["drop_done_delay_seconds"]
    cutoff = now - timedelta(seconds=delay_seconds)

    # keep_n: a cron schedule with drop_done keeps its newest keep_n done jobs.
    newest = func.row_number().over(partition_by=Job.schedule_id, order_by=Job.end.desc()).label("newest")
    ranked = select(Job.id, newest, Schedule.keep_n) \
        .join(Schedule, Schedule.id == Job.schedule_id) \
        .where(Job.state == "done", Schedule.schedule != "", Schedule.drop_done == True, Schedule.keep_n > 0) \
        .subquery()
    kept = select(ranked.c.id).where(ranked.c.newest <= ranked.c.keep_n)

    with Session(get_db_engine()) as session:
        logger.debug(f"cleanup_drop_done_jobs: session ID {id(session)} at {now}, delay={delay_seconds}s")
        stmt = select(Job).where(
            Job.state == "done",
            Job.drop_done == True,
            Job.end < cutoff,
            Job.id.not_in(kept)
        )

        jobs_to_remove = session.scalars(stmt).all()
        logger.debug(f"cleanup_drop_done_jobs: found {len(jobs_to_remove)} jobs to mark for removal")

        for job in jobs_to_remove:
            logger.debug(f"cleanup_drop_done_jobs: marking job {job.id} for removal")
            job.state = "remove"

        if jobs_to_remove:
            commit_with_retry(session)
            logger.debug("cleanup_drop_done_jobs: committed changes")
        else:
            logger.debug("cleanup_drop_done_jobs: no jobs to clean up")

def cleanup_failed_jobs():
    # max_fail_n: a cron schedule keeps only its newest max_fail_n failed jobs.
    newest = func.row_number().over(partition_by=Job.schedule_id, order_by=Job.end.desc()).label("newest")
    ranked = select(Job.id, newest, Schedule.max_fail_n) \
        .join(Schedule, Schedule.id == Job.schedule_id) \
        .where(Job.state == "failed", Schedule.schedule != "") \
        .subquery()
    surplus = select(ranked.c.id).where(ranked.c.newest > ranked.c.max_fail_n)

    with Session(get_db_engine()) as session:
        jobs_to_remove = session.scalars(select(Job).where(Job.id.in_(surplus))).all()

        for job in jobs_to_remove:
            logger.debug(f"cleanup_failed_jobs: marking job {job.id} for removal")
            job.state = "remove"

        if jobs_to_remove:
            commit_with_retry(session)

# States a runner transition never overwrites: an outcome already recorded,
# or a cancel/remove the operator committed.
KEPT_STATES = ("cancel", "cancelling", "cancelled", "done", "failed", "remove", "removed")


def appended_detail(message: str | None):
    """SQL value for `Job.detail` with `message` appended — used wherever a
    row keeps its state, so the message recorded with that state (e.g. the
    operator's cancel reason, the first traceback) is never overwritten.
    An empty message, or one identical to the current detail, leaves it as is."""
    if not message:
        return Job.detail
    return case(
        (or_(Job.detail.is_(None), Job.detail == ""), message),
        (Job.detail == message, Job.detail),
        else_=Job.detail + "\n" + message,
    )


def finishJob(job_id: str, state: str, detail: str | None, finished: datetime | None = None):
    """Record a job's outcome in one atomic UPDATE.

    `finished` is the job's own finish time (stamped by send_completion) and
    is given for a completion the job process reports; it is None for the
    runner's own transitions (stale pid, launch/load failure, `cancelling`).

    Which state the row ends in:
      • already done/failed/remove/removed: kept — the first outcome stands.
      • a completion while the cancel is still only requested (`cancel`, the
        runner has not started terminating, term_start NULL): the completion
        decides — the job finished before the cancel took effect.
      • a completion once the runner started terminating (term_start set):
        it decides only if the job finished before term_start; otherwise the
        row stays cancel/cancelling/cancelled.
      • a runner transition never overwrites cancel/cancelling/cancelled.
    When the state is applied, detail and end are written — end is the job's
    own finish time for a completion it reported, the runner's time for its
    own transitions. When the state is kept, end stays and the detail is
    appended (see appended_detail).
    `cancelling` (the runner starting to terminate the job) records term_start.
    """
    logger.debug(f"finishJob(job_id='{job_id}', state='{state}', detail='{detail}', finished={finished})")

    with Session(get_db_engine()) as session:
        logger.debug(f"finishJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        if job is None:
            return

        old_state = job.state
        # Compute the candidate terminal state from the snapshot. The atomic CASE
        # below decides against the row as it is at UPDATE time, so a cancel
        # committed after this read is honoured.
        if job.kind == "service" and state == "done" and job.drop_done:
            candidate = "remove"  # Immediately remove done services
        elif old_state == "running" and state == "done" and job.drop_done and not job.kind == "debug":
            candidate = state  # Keep as "done" for delayed removal
        else:
            candidate = state

        if job.kind == "service" and state == "failed":
            logger.info("finishJob: service %s failed detail='%s'", job_id, detail)

        old_pid = job.pid
        kind = job.kind
        owner = job.owner

        # Atomic UPDATE with CASE: a recorded outcome is never re-written —
        # two completions arriving for the same job (e.g. exec() returned
        # "done" + a finally-block close() raised and run_*_job's outer
        # except sent "failed") must not demote the committed state; the
        # later message is appended. A cancel is replaced only by the
        # report of a job that finished before the cancel took effect.
        now = datetime.now()
        applies = Job.state.not_in(KEPT_STATES)
        if finished is not None:
            applies = or_(
                applies,
                and_(Job.state == "cancel", Job.term_start.is_(None)),
                and_(Job.state.in_(("cancel", "cancelling", "cancelled")), Job.term_start > finished),
            )
        values = {
            Job.state: case((applies, candidate), else_=Job.state),
            Job.end: case((applies, finished if finished is not None else now), else_=Job.end),
            Job.detail: case((applies, detail), else_=appended_detail(detail)),
            Job.pid: None,
        }
        if state == "cancelling":
            values[Job.term_start] = now
        session.query(Job).filter(Job.id == job_id).update(values, synchronize_session=False)
        logger.debug(f"finishJob({job_id}): before commit, session ID {id(session)}, candidate '{candidate}' from old '{old_state}' at {datetime.now()}")
        commit_with_retry(session)
        session.expire_all()
        committed = session.get(Job, job_id)
        committed_state = committed.state if committed else "<missing>"
        logger.debug(f"finishJob({job_id}): after commit, session ID {id(session)}, committed state '{committed_state}' for {job_id} at {datetime.now()}")

        if old_pid:
            threading.Thread(target=terminate_job_processes, args=(job_id, old_pid, owner), daemon=True).start()

def disposeJobs():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(Job.state == "removed")
        if session.scalar(stmt):
            logger.debug(f"disposeJobs: found removed jobs, deleting at {datetime.now()}")
            stmt = delete(Job).where(Job.state == "removed")
            session.execute(stmt)

            commit_with_retry(session)
            logger.debug("disposeJobs: committed deletions")

def cleanProcesses(processes: dict[int, subprocess.Popen]):
    cleaned_processes: dict[int, subprocess.Popen] = {}
    for pid in processes:
        p = processes[pid]
        if p.poll() is None:
            cleaned_processes[pid] = p

    return cleaned_processes

def getNextToTerm():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            or_(Job.state == "cancel", Job.state == "remove")
        )

        job = session.scalar(stmt)
        return {"id": job.id, "pid": job.pid} if job is not None else None

def postTerm(job_id: str, killed: bool = False):
    logger.debug(f"postTerm({job_id}, killed={killed})")

    with Session(get_db_engine()) as session:
        logger.debug(f"postTerm: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)

        if job is not None:
            old_state = job.state
            if job.state == "remove":
                logger.debug(f"postTerm: before delete, session ID {id(session)}, deleting {job_id} (state '{old_state}') at {datetime.now()}")
                session.delete(job)
                lib_runner.drop(job_id, job.set)
                commit_with_retry(session)
            else:
                # A done/failed row was decided by the job's own completion
                # (it finished before the termination started, see finishJob):
                # it keeps that outcome and its finish time, only the pid is
                # cleared. Decided in the UPDATE itself, so a completion
                # recorded after the read above is honoured too.
                decided = Job.state.in_(("done", "failed"))
                session.query(Job).filter(Job.id == job_id).update({
                    Job.state: case((decided, Job.state), else_="cancelled"),
                    Job.end: case((decided, Job.end), else_=datetime.now()),
                    Job.pid: None,
                }, synchronize_session=False)
                commit_with_retry(session)
                session.expire_all()
                committed = session.get(Job, job_id)
                logger.info("postTerm called %s old_state=%s → new=%s caller=%s", job_id, old_state,
                            committed.state if committed else "<missing>", traceback.format_stack(limit=5)[-1])
            logger.debug(f"postTerm: after commit, session ID {id(session)}, committed for {job_id} at {datetime.now()}")

def schedule():
    now = datetime.now()
    logger.debug(f"schedule: starting at {now}")

    with Session(get_db_engine()) as session:
        logger.debug(f"schedule: session ID {id(session)} at {now}")
        stmt = select(Schedule.id,
                      Schedule.set,
                      Schedule.name,
                      Schedule.owner,
                      Schedule.active,
                      Schedule.drop_done,
                      Schedule.max_retries,
                      Schedule.max_fail_n,
                      Schedule.schedule,
                      Schedule.param,
                      Schedule.last)
        schedules = session.execute(stmt)

        for s in schedules:
            # A schedule whose set or task name is outside the name charset
            # cannot run; it is skipped (the Scheduler page flags it).
            if not (git.is_valid_name(s.set) and git.is_valid_name(s.name)):
                logger.debug("schedule: skipping %s, invalid name %r::%r", s.id, s.set, s.name)
                continue
            if s.active:
                if s.schedule:
                    # Check if there was a scheduled run between s.last and now
                    cron = croniter.croniter(s.schedule, s.last or datetime.min)
                    next_run = cron.get_next(datetime)
                    if next_run <= now:
                        # Check if not still running
                        running_jobs = lib_runner.find(s.owner, s.set, s.name, "task", ["todo", "dequeued", "running"])
                        if not running_jobs:
                            logger.debug(f"schedule: enqueuing task {s.set}::{s.name} (cron {s.schedule}) at {next_run}")
                            try:
                                lib_runner.enqueue(s.set, s.name, s.owner, "task", s.drop_done, s.param, s.max_retries, None, schedule_id=s.id)
                            except lib_runner.ShutdownPending:
                                logger.info("schedule: shutdown pending — skipping task enqueue for %s::%s", s.set, s.name)
                                return

                            schedule_obj = session.get(Schedule, s.id)
                            schedule_obj.last = now
                            logger.debug(f"schedule: before commit, session ID {id(session)}, last {s.last} to {next_run} for {s.id} at {datetime.now()}")
                            commit_with_retry(session)
                            logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={next_run} for {s.id} at {datetime.now()}")
                elif not lib_runner.find(s.owner, s.set, s.name, "service",
                         ["todo", "dequeued", "loading", "running",
                          "cancel", "cancelling", "cancelled", "done"]) and \
                     len(lib_runner.find(s.owner, s.set, s.name, "service", ["failed"], schedule_id=s.id)) < s.max_fail_n:
                    logger.debug(f"schedule: enqueuing service {s.set}::{s.name} at {now}")
                    try:
                        lib_runner.enqueue(s.set, s.name, s.owner, "service", s.drop_done, s.param, s.max_retries, None, schedule_id=s.id)
                    except lib_runner.ShutdownPending:
                        logger.info("schedule: shutdown pending — skipping service enqueue for %s::%s", s.set, s.name)
                        return
                    schedule_obj = session.get(Schedule, s.id)
                    old_last = schedule_obj.last
                    schedule_obj.last = now
                    logger.debug(f"schedule: before commit, session ID {id(session)}, last {old_last} to {now} for service {s.id} at {datetime.now()}")
                    commit_with_retry(session)
                    logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={now} for service {s.id} at {datetime.now()}")

    # Essential services without a Schedule row — same gating as the
    # service-with-empty-schedule branch above, with a fixed failure limit
    # of 3 in place of max_fail_n. A user-initiated cancel
    # (`cancel`, then `cancelled` once terminated) is a hard stop: the operator
    # has deliberately taken the service down and must remove the row
    # before it comes back. Three failures in a row is also a stop until
    # the operator clears them.
    for essential in PYOPS_ESSENTIAL_SERVICES:
        owner = essential["owner"]
        set_ = essential["set"]
        name_ = essential["name"]
        if lib_runner.find(owner, set_, name_, "service",
                           ["todo", "dequeued", "loading", "running",
                            "cancel", "cancelling", "cancelled", "done"]):
            continue
        if len(lib_runner.find(owner, set_, name_, "service", ["failed"])) >= 3:
            continue
        logger.debug(f"schedule: enqueuing essential service {set_}::{name_} at {now}")
        try:
            lib_runner.enqueue(set_, name_, owner, "service", True, None, 0, None)
        except lib_runner.ShutdownPending:
            # Race window: SIGTERM landed between our entry to schedule()
            # and this enqueue. Stop scheduling for this iteration; the
            # outer while-loop will exit on the next kill_now check.
            logger.info("schedule: shutdown pending — skipping essential-service enqueue for %s::%s", set_, name_)
            return

    logger.debug(f"schedule: completed at {datetime.now()}")

def main(data_base: str, web_bind: str, venv_path: str, pkg_path: str, env):
    from app.lib import files
    log_conf = files.conf_log()
    level_str = log_conf["loglevel"]
    level = logging.DEBUG if level_str.lower() == "debug" else logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    # Add stderr handler for warnings
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    handlers.append(stderr_handler)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=handlers
    )
    logger.info("Runner logging configured: level=%s", level_str)

    os.environ.clear()
    os.environ.update(env)

    # Init config early for subprocess isolation
    conf_path = os.environ.get("PYOPS_CONFIG", os.path.join(files.project_root, "etc/pyops.json"))
    files.Config.init(conf_path)

    # Stale shutdown flag from a previous unclean exit would otherwise
    # immediately refuse every enqueue. Remove on a clean startup; the
    # signal handler re-creates it the moment SIGTERM lands.
    try:
        if os.path.exists(lib_runner.SHUTDOWN_FLAG_PATH):
            os.remove(lib_runner.SHUTDOWN_FLAG_PATH)
    except Exception as e:
        logger.warning("Could not clear stale shutdown flag %s: %s", lib_runner.SHUTDOWN_FLAG_PATH, e)

    killer = GracefulKiller()

    runner_context, runner_socket, runner_poller = setup_runner_socket()

    # restore jobs which were taken out of queue but not executed due to crash
    handleOddJobs()

    # purge leftover service jobs at startup to ensure clean servicing
    purgeLeftoverServiceJobs()

    # clean up drop_done jobs that have been done for 60+ seconds
    cleanup_drop_done_jobs()
    cleanup_failed_jobs()

    processes: dict[int, subprocess.Popen] = {}

    loop_count = 0
    while not killer.kill_now:
        try:
            loop_count += 1
            logger.debug(f"main loop iteration {loop_count} at {datetime.now()}")
            current_time = time.time()

            # dispose already remove jobs
            logger.debug("main: calling disposeJobs")
            disposeJobs()
            logger.debug("main: disposeJobs completed")

            # remove cancelled processes from list
            processes = cleanProcesses(processes)
            logger.debug(f"main: cleaned processes, {len(processes)} remaining")

            # Check for stale PIDs in DB that are not running. `loading`
            # is covered too: a job's pid is set in assignJob *before* the
            # job process flips state to `running` via markRunning, so a
            # crash during the param block (or during job_setup.py's
            # install phase) would otherwise leave a `loading` row with a
            # dead pid forever.
            with Session(get_db_engine()) as session:
                stmt = select(Job.id, Job.pid, Job.set).where(
                    Job.state.in_(("loading", "running")),
                    Job.pid.isnot(None),
                )
                for job_id, pid, set_ in session.execute(stmt).all():
                    try:
                        if psutil.Process(pid).is_running():
                            continue
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.ZombieProcess:
                        logger.warning("Stale PID %s for job %s: ZombieProcess", pid, job_id)
                        pass
                    except psutil.AccessDenied:
                        logger.warning("Stale PID %s for job %s: AccessDenied", pid, job_id)
                        pass

                    # The process is gone: the job's result file holds its
                    # result if it finished but its report never arrived;
                    # without one the job died ("dead").
                    result = job_lib.read_result(os.path.join(files.conf_data()["jobs"], set_, job_id))
                    if result is not None:
                        finishJob(job_id, result["state"], result["detail"],
                                  finished=datetime.fromtimestamp(result["time"]))
                    else:
                        finishJob(job_id, "failed", f"dead (pid={pid})")
                        

            # terminate "remove" or "cancel" jobs
            try:
                logger.debug("main: calling getNextToTerm")
                job = getNextToTerm()
                logger.debug(f"main: getNextToTerm returned {job}")

                if job is not None:
                    if job["pid"] in processes:
                        logger.debug(f"Trying to term job {job}")
                        finishJob(job["id"], "cancelling", None)
                    else:
                        postTerm(job["id"])
            except Exception as e:
                logger.debug(f"main: exception in terminate block: {e}")
                logger.exception("Error terminating job: %s", e)

            # handle jobs "failed" or "done"
            events = runner_poller.poll(0)
            for sock, _ in events:
                receive_and_process(runner_socket)

            # clean up drop_done jobs that have been done for delay+ seconds
            cleanup_drop_done_jobs()
            cleanup_failed_jobs()

            # Once shutdown has been signalled, stop claiming new jobs.
            # In-flight terminations and ZMQ completions above still run so
            # already-spawned jobs drain. Any new pip install in
            # ensure_set_environment (which runs unguarded inside the spawn
            # block) would risk being killed mid-flight by the upcoming
            # process exit, corrupting the venv.
            if killer.kill_now:
                logger.debug("main: shutdown signalled — skipping getNextToDo")
                time.sleep(0.5)
                continue

            try:
                logger.debug("main: calling getNextToDo")
                job_id = getNextToDo()
                logger.debug(f"main: job_id from getNextToDo = '{job_id}'")

                if job_id is not None:
                    try:
                        logger.debug(f"main: loading job {job_id}")
                        owner, setname, name, kind, param, code, overrides, max_retries = loadJob(job_id)

                        libs = []

                        override_dict = json.loads(overrides) if overrides else None

                        if param:
                            param = job_lib.process_imports(param, setname, kind, libs, override_dict)
                        code = job_lib.process_imports(code, setname, kind, libs, override_dict)

                        logger.debug(f"Run job {job_id}")
                        # Pyops-set scriptlets are pyops-native code and
                        # run in the pyops process's own Python — pyzmq /
                        # msgpack / psutil / requests are already there
                        # (pyops itself uses them). User sets get their
                        # own venv via the standard ensure_set_environment.
                        if setname == files.BUNDLED_SET_NAME:
                            venv_python = sys.executable
                        else:
                            ensure_set_environment(setname)
                            venv_python = os.path.join(venv_path, setname, 'bin', 'python')
                        json_kwargs = json.dumps({"job_id": job_id, "owner": owner, "setname": setname, "name": name, "kind": kind, "param": param, "code": code, "libs": libs, "overrides": overrides, "runner_sock_path": "/tmp/pyops/runner.sock", "max_retries": max_retries, "conf_data": files.conf_data(), "conf_runner": files.conf_runner(), "conf_users": files.conf_users(), "conf_web": files.conf_web(), "conf_log": files.conf_log()})
                        try:
                            # Spawn job_setup.py — it runs the pip/git directive
                            # installs under the per-set bootstrap lock, then
                            # os.execv's into job_runner.py in the same PID with
                            # a fresh interpreter that picks up any new .pth
                            # files from `pip install -e` at startup.
                            # `preexec_fn=os.setsid` puts the job in its own
                            # process group (PGID == p.pid). Termination then
                            # goes through `os.killpg(p.pid, sig)` which hits
                            # the whole tree atomically — including any
                            # debugger subprocesses the user's `param`
                            # spawns (debugpy.listen() spawns an adapter
                            # process; gdbserver runs out-of-process; etc.).
                            # Without the new session, those grandchildren
                            # re-parent to PID 1 the instant the job dies
                            # and keep holding their listening ports.
                            #
                            # kwargs go in via stdin, not argv: the
                            # serialised job context can be hundreds of KB
                            # when libs + VSIX overrides are large (one
                            # i-doit::checker scriptlet was already 84 KB),
                            # and Linux execve(2) refuses combined argv+env
                            # beyond ARG_MAX (~128 KiB default) with
                            # E2BIG. job_setup.py reads sys.stdin.read()
                            # and then hands the same payload off to
                            # job_runner.py through a file in the
                            # workspace (see _exec_runner there).
                            p = subprocess.Popen(
                                [venv_python, os.path.abspath('app/job_setup.py')],
                                cwd=files.project_root,
                                stdin=subprocess.PIPE,
                                stdout=sys.stdout, stderr=sys.stderr, text=True,
                                preexec_fn=os.setsid,
                            )
                            try:
                                p.stdin.write(json_kwargs)
                                p.stdin.close()
                            except BrokenPipeError:
                                # Child died before reading stdin (rare:
                                # interpreter import error etc.). The job
                                # will be reaped via the normal stale-PID
                                # path; no extra cleanup here.
                                pass
                        except Exception as e:
                            finishJob(job_id, "failed", f"Launch failed: {e}")
                            continue

                        # State stays at `loading` until the job process
                        # signals `state=running` (after `exec(param)` returns);
                        # see markRunning + execute_job_code.
                        assignJob(job_id, p.pid)
                        processes[p.pid] = p
                        logger.debug(f"main: started process for {job_id}, pid {p.pid}")

                    except JobStateChanged as e:
                        logger.info("main: not spawning: %s", e)
                    except Exception as e:
                        logger.debug(f"main: exception loading/running {job_id}: {e}")
                        logger.exception("Error loading/running job %s: %s", job_id, e)
                        finishJob(job_id, "failed", traceback.format_exc())
                else:
                    logger.debug("main: no job, calling schedule")
                    schedule()
                    logger.debug("main: schedule completed")

                    logger.debug("main: sleeping 0.5s")
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"main: exception in main try block iteration {loop_count}: {e}")
                logger.exception("Error in main loop: %s", e)
        except Exception as e:
            logger.exception("Critical error in main loop iteration %d, continuing: %s", loop_count, e)
            time.sleep(1)  # Brief pause before retry to avoid tight loops on persistent errors

    # Synchronously SIGTERM all live job trees, wait up to
    # runner.shutdown_timeout_seconds, SIGKILL the stragglers together, then
    # sweep the rows. Old code spawned daemon threads here, which
    # got killed the moment main() returned — leaving service subprocesses
    # orphaned in the pgid until the outer test harness SIGKILL'd them after
    # its 120s graceful wait. Pass the runner socket so we drain completion
    # ACKs during the wait — without it every service ends up SIGKILL'd
    # because its send_completion REQ blocks for the full timeout.
    _shutdown_job_processes(processes, timeout=files.conf_runner()["shutdown_timeout_seconds"],
                             runner_socket=runner_socket, runner_poller=runner_poller)

    # Drain any completion ZMQ events the dying subprocesses managed to send.
    events = runner_poller.poll(0)
    while events:
        for sock, _ in events:
            receive_and_process(runner_socket)
        events = runner_poller.poll(0)

    # dispose already remove jobs
    logger.debug("main: final disposeJobs in shutdown")
    disposeJobs()
    logger.debug("main: final disposeJobs completed")

    # Remove the shutdown flag on the way out — startup also clears it, but
    # tidying here keeps /tmp/pyops clean for inspection between runs.
    try:
        if os.path.exists(lib_runner.SHUTDOWN_FLAG_PATH):
            os.remove(lib_runner.SHUTDOWN_FLAG_PATH)
    except Exception as e:
        logger.warning("Could not remove shutdown flag on exit: %s", e)

    # cleanup ZMQ
    runner_poller.unregister(runner_socket)
    runner_socket.setsockopt(zmq.LINGER, 0)
    runner_socket.close()
    runner_context.term()

    logger.debug("runner exited")
