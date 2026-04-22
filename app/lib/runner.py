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

def enqueue(set: str, name: str, owner: str, kind: str, drop_done: bool, param: str, max_retries: int = 0, overrides: str | None = None):
    """Enqueues a new job into the database.

    Args:
        set (str): Scriptlet set.
        name (str): Scriptlet name.
        owner (str): Job owner (fixed to 'anonymous').
        kind (str): Job kind ('task', 'service', 'debug').
        drop_done (bool): Auto-drop on success.
        param (str): Job parameters/code snippet.
        overrides (str | None): JSON string of overrides dict for debug kind.

    Returns:
        str: Generated job ID.
    """
    with Session(get_db_engine()) as session:
        if kind == "debug":
            # Mark existing debug job for cancellation to allow new one (only one debug session at a time)
            stmt = select(Job).where(and_(Job.owner == owner, Job.kind == "debug", Job.state.in_(["dequeued", "running"]))).limit(1)
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
            overrides = overrides
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
