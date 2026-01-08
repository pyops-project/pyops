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
from app.lib import git

import logging
logger = logging.getLogger(__name__)



def enqueue(set: str, name: str, owner: str, kind: str, drop_done: bool, param: str, overrides: str | None = None):
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
            # Mark existing debug job for cancellation to allow new one
            stmt = select(Job).where(and_(Job.owner == owner, Job.kind == "debug", Job.state.in_(["dequeued", "running"]))).limit(1)
            existing = session.scalar(stmt)
            if existing:
                existing.state = "cancel"
                existing.detail = f"Cancelled by new debug job start for {owner}"
                existing.end = datetime.now()
                session.commit()
                logger.info(f"Marked existing debug job {existing.id} for cancellation by new debug enqueue by {owner}")

        job = Job(
            id = "{}_{}_{}_{}".format(owner, set, name, datetime.now().strftime("%Y%m%dT%H%M%S%f")),
            set = set,
            name = name,
            owner = owner,
            drop_done = drop_done,
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

def find(owner: str, set: str, name: str, kind: str, states: list[str]):
    """Finds job IDs matching criteria.
    
    Args:
        owner (str): Owner.
        set (str): Set.
        name (str): Name.
        kind (str): Kind.
        states (list[str]): Job states to filter.
    
    Returns:
        list[str]: Matching job IDs (descending order).
    """
    with Session(get_db_engine()) as session:
        stmt = select(Job.id).where(
            Job.owner == owner,
            Job.set == set,
            Job.name == name,
            Job.kind == kind,
            Job.state.in_(states)
        ).order_by(Job.id.desc())
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

def drop(id: str, owner: str, set: str):
    """Drops job workspace and socket files.
    
    Args:
        id (str): Job ID.
        owner (str): Owner.
        set (str): Set.
    
    Returns:
        bool: True if dropped successfully.
    """
    data_base = files.conf_data()["jobs"]
    userspace = os.path.join(data_base, f"{owner}")
    setspace = os.path.join(userspace, f"{set}")
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

            if len(os.listdir(userspace)) == 0:
                shutil.rmtree(userspace)

            return True
        else:
            return False
    except Exception as e:
        logger.exception("Error dropping job %s: %s", id, e)
        return False

def loadLib(set: str, name: str, kind: str):
    """Loads library code from Git (unstaged if kind=="debug", else committed).

    Args:
        set (str): Set.
        name (str): Name.
        kind (str): Job kind, determines unstaged if "debug".

    Returns:
        str: Code content.

    Raises:
        Exception: If library not found.
    """
    print(f"loadLib(set={set}, name={name}, kind={kind})")
    repo = git.get_repo_path(set)
    files_list = [f for f in os.listdir(repo) if f.startswith(name) and f.endswith('.py')]
    if not files_list:
        raise Exception(f"Library {set}::{name} not existing")
    filename = files_list[0]
    type_ = git.infer_type_from_filename(filename)
    committed = kind != "debug" # if debug job prefer unstaged changes
    return git.read_content(repo, name, type_, committed=committed)

def loadJob(id: str):
    """Loads job details and code from Git/DB (unstaged if kind=="debug", else committed).

    Args:
        id (str): Job ID.

    Returns:
        tuple: (owner, set, name, kind, param, code).

    Raises:
        Exception: If scriptlet not found or no code.
    """
    print(f"loadJob(id={id})")

    with Session(get_db_engine()) as session:
        job = session.get(Job, id)
        if job is None:
            raise Exception(f"Job {id} not existing")
        repo = git.get_repo_path(job.set)
        files_list = [f for f in os.listdir(repo) if f.startswith(job.name) and f.endswith('.py')]
        if not files_list:
            raise Exception(f"Scriptlet {job.set}::{job.name} not existing")
        filename = files_list[0]
        type_ = git.infer_type_from_filename(filename)
        committed = job.kind != "debug" # if debug job prefer unstaged changes
        code = git.read_content(repo, job.name, type_, committed=committed)

        job.state = "running"
        job.start = datetime.now()
        session.commit()

        return job.owner, job.set, job.name, job.kind, job.param, code
