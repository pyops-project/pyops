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

import logging
logger = logging.getLogger(__name__)

# Cross-process shutdown gate. Once SIGTERM lands on the runner, its signal
# handler creates this file; every enqueue() — including the ones gunicorn
# workers and the pyops data service make over HTTP — must refuse new
# work, so already-running jobs can drain without us piling more on. The
# file lives under /tmp/pyops/ which the runner socket already provisions.
SHUTDOWN_FLAG_PATH = "/tmp/pyops/shutdown.flag"


class ShutdownPending(RuntimeError):
    """enqueue() refuses while the runner has been signalled to shut down."""


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

def find(owner: str | None, set: str, name: str, kind: str, states: list[str]):
    """Finds job IDs matching criteria.

    Args:
        owner (str | None): Owner (optional, if None, no owner filter).
        set (str): Set.
        name (str): Name.
        kind (str): Kind.
        states (list[str]): Job states to filter.

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
        stmt = stmt.order_by(Job.id.desc())
        return [j.id for j in session.execute(stmt)]

def cancel(id: str):
    """Marks job for cancellation.
    
    Args:
        id (str): Job ID.
    
    Returns:
        Job: Updated job or None.
    """
    with Session(get_db_engine()) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "cancel"
            session.commit()

        return job.__dict__ if job else None

def remove(id: str):
    """Marks job for removal.
    
    Args:
        id (str): Job ID.
    
    Returns:
        Job: Updated job or None.
    """
    with Session(get_db_engine()) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "remove"
            session.commit()

        return job.__dict__ if job else None

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
