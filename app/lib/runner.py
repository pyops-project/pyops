import os, shutil, signal
from datetime import datetime
import tempfile

import psutil
import time

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.job import Job

from app.lib import files
from app.lib.errors import JobStateConflict, ShutdownPending

import logging
logger = logging.getLogger(__name__)

# Cross-process shutdown gate. Once SIGTERM lands on the runner, its signal
# handler creates this file; every enqueue() — including the ones gunicorn
# workers and the pyops data service make over HTTP — must refuse new
# work, so already-running jobs can drain without us piling more on. The
# file lives under /tmp/pyops/ which the runner socket already provisions.
SHUTDOWN_FLAG_PATH = "/tmp/pyops/shutdown.flag"


def is_shutdown_pending() -> bool:
    return os.path.exists(SHUTDOWN_FLAG_PATH)


def enqueue(set: str, name: str, owner: str, kind: str, drop_done: bool, param: str, max_retries: int = 0, overrides: str | None = None, schedule_id: int | None = None):
    """Enqueues a new job into the database.

    Args:
        set (str): Scriptlet set.
        name (str): Scriptlet name.
        owner (str): Job owner (fixed to 'anonymous').
        kind (str): Job kind ('task', 'service', 'debug', 'draft', 'test', 'testdebug').
        drop_done (bool): Auto-drop on success.
        param (str): Job parameters/code snippet.
        overrides (str | None): JSON string of overrides dict for debug/test/testdebug kind.
        schedule_id (int | None): Id of the Schedule row the job is started from.

    Returns:
        str: Generated job ID.

    Raises:
        ShutdownPending: when the runner is mid-shutdown — no new tasks or
            services may be enqueued or re-enqueued in that window.
    """
    if is_shutdown_pending():
        raise ShutdownPending(f"pyops shutdown in progress; refusing {kind} enqueue for {set}::{name}")
    with Session(get_db_engine()) as session:
        if kind in ("debug", "testdebug"):
            # Mark any existing interactive-debug job for cancellation
            # to allow the new one. debug and testdebug share this gate:
            # both block on a debugger attach, so only one of either kind
            # can be active per owner at a time.
            stmt = select(Job).where(and_(Job.owner == owner, Job.kind.in_(["debug", "testdebug"]), Job.state.in_(["dequeued", "running"]))).limit(1)
            existing = session.scalar(stmt)
            if existing:
                existing.state = "cancel"
                existing.detail = f"Cancelled by new {kind} job start for {owner}"
                existing.end = datetime.now()
                session.commit()
                logger.info(f"Marked existing {existing.kind} job {existing.id} for cancellation by new {kind} enqueue by {owner}")

        job = Job(
            id = "{}_{}_{}_{}".format(owner, set, name, datetime.now().strftime("%Y%m%dT%H%M%S%f")),
            set = set,
            name = name,
            owner = owner,
            drop_done = drop_done,
            max_retries = max_retries,
            kind = kind,
            state = "todo",
            param = param,
            overrides = overrides,
            schedule_id = schedule_id
        )

        session.add(job)
        session.commit()

        return job.id

def query(id: str):
    """Queries a job by ID.
    
    Args:
        id (str): Job ID.
    
    Returns:
        Job: ORM Job instance or None.
    """
    with Session(get_db_engine()) as session:
        return session.get(Job, id)

def find(owner: str | None, set: str, name: str, kind: str, states: list[str], schedule_id: int | None = None):
    """Finds job IDs matching criteria.

    Args:
        owner (str | None): Owner (optional, if None, no owner filter).
        set (str): Set.
        name (str): Name.
        kind (str): Kind.
        states (list[str]): Job states to filter.
        schedule_id (int | None): Schedule id (optional, if None, no schedule filter).

    Returns:
        list[str]: Matching job IDs (descending order).
    """
    with Session(get_db_engine()) as session:
        stmt = select(Job.id).where(
            Job.set == set,
            Job.name == name,
            Job.kind == kind,
            Job.state.in_(states)
        )
        if owner is not None:
            stmt = stmt.where(Job.owner == owner)
        if schedule_id is not None:
            stmt = stmt.where(Job.schedule_id == schedule_id)
        stmt = stmt.order_by(Job.id.desc())
        return [j.id for j in session.execute(stmt)]

# States a job can be cancelled from: it has not finished yet.
CANCELLABLE_STATES = ("todo", "dequeued", "loading", "running")
# States of a cancel already requested or done: cancelling again is a no-op.
CANCEL_STATES = ("cancel", "cancelling", "cancelled")
# States a job can be removed (deleted) from: no process of it is running.
REMOVABLE_STATES = ("todo", "cancelled", "failed", "done")


def _transition(id: str, from_states: tuple, to_state: str):
    """Sets `to_state` on job `id` in one conditional UPDATE, only while the
    row is in one of `from_states`. Returns (updated, state): whether the
    row changed and its state afterwards; (False, None) for an unknown id."""
    with Session(get_db_engine()) as session:
        updated = session.query(Job).filter(
            Job.id == id, Job.state.in_(from_states),
        ).update({Job.state: to_state}, synchronize_session=False)
        session.commit()
        state = session.scalar(select(Job.state).where(Job.id == id))
        return updated > 0, state


def cancel(id: str):
    """Marks a job for cancellation — only a job that has not finished.

    Args:
        id (str): Job ID.

    Returns:
        dict | None: {"id", "state"} of the job, None for an unknown id.
            A job already cancelled (or being cancelled) is returned
            unchanged: cancelling again is a no-op.

    Raises:
        JobStateConflict: The job already finished (done, failed) or is
            being deleted (remove, removed); the row is left unchanged.
    """
    updated, state = _transition(id, CANCELLABLE_STATES, "cancel")
    if state is None:
        return None
    if not updated and state not in CANCEL_STATES:
        raise JobStateConflict(f"Job {id} is already {state}")
    return {"id": id, "state": state}

def remove(id: str):
    """Marks a job for removal — only a job with no running process.

    Args:
        id (str): Job ID.

    Returns:
        dict | None: {"id", "state"} of the job, None for an unknown id.

    Raises:
        JobStateConflict: The job is in a state it cannot be deleted from;
            the row is left unchanged.
    """
    updated, state = _transition(id, REMOVABLE_STATES, "remove")
    if state is None:
        return None
    if not updated:
        raise JobStateConflict(
            f"Job {id} is in state '{state}' and cannot be deleted. "
            f"Allowed states: {', '.join(REMOVABLE_STATES)}"
        )
    return {"id": id, "state": state}

def drop(id: str, set: str):
    """Drops job workspace and socket files.

    Args:
        id (str): Job ID.
        set (str): Set.

    Returns:
        bool: True if dropped successfully.
    """
    data_base = files.conf_data()["jobs"]
    setspace = os.path.join(data_base, f"{set}")
    workspace = os.path.join(setspace, f"{id}")

    # Updated sock path for cleanup
    temp_dir = tempfile.gettempdir()
    pyops_dir = os.path.join(temp_dir, "pyops")
    sockfile = os.path.join(pyops_dir, f"{id}.sock")

    try:
        if os.path.exists(sockfile):
            os.remove(sockfile)

        if os.path.exists(workspace):
            shutil.rmtree(workspace)

            if len(os.listdir(setspace)) == 0:
                shutil.rmtree(setspace)

            return True
        else:
            return False
    except Exception as e:
        logger.exception("Error dropping job %s: %s", id, e)
        return False
