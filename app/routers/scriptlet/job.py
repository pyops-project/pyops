import os.path
import subprocess
import json
import re

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND
from pydantic import BaseModel, Field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.job import Job

from app.lib import runner, files, git
from app.lib.async_utils import run_in_executor
from app.lib.auth import get_current_user

from zipstream import ZipStream, ZIP_DEFLATED

import logging
logger = logging.getLogger(__name__)

# Errors are raised typed by the lib layer (invalid name 400, unknown set or
# scriptlet 404, job state conflict 409, shutdown pending 503) and mapped to
# HTTP by the one handler in app/web.py — no route catches them.

router = APIRouter()

class RunArgument(BaseModel):
    set: str
    name: str
    drop_done: bool
    max_retries: int = Field(default=0, ge=0)
    param: str
    kind: Literal["task", "debug", "draft", "test", "testdebug"] = "task"
    overrides: dict[str, str] | None = None

@router.get("/scriptlet/job/list", tags=["job"])
async def list_get():
    """
    Returns a list of available Schedules
    """
    with Session(get_db_engine()) as session:
        stmt = select(Job.id,
                      Job.set,
                      Job.name,
                      Job.start,
                      Job.end,
                      Job.kind,
                      Job.state,
                      Job.detail,
                      Job.param,
                      Job.owner).where(
            Job.state != "remove"
        ).order_by(Job.id.desc())
        db_jobs = session.execute(stmt)

        return [dict(r._mapping) for r in db_jobs]

@router.put("/scriptlet/job/item", tags=["job"])
async def job_put(ru: RunArgument, user: str = Depends(get_current_user)):
    """
    Creates a new Job
    """
    repo = await run_in_executor(git.get_repo_path, ru.set)
    # Check exists (committed), skip for debug/draft/test/testdebug (use unstaged or overrides).
    scriptlet_key = f"{ru.set}::{ru.name}"
    overrides_present = ru.overrides is not None and scriptlet_key in ru.overrides
    if ru.kind not in ("debug", "draft", "test", "testdebug") or (ru.kind in ("debug", "test", "testdebug") and not overrides_present):
        try:
            await run_in_executor(git.read_content, repo, ru.name, "task", committed=True)
        except FileNotFoundError:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {scriptlet_key} not existing.")

    # Validate overrides for debug/test/testdebug kind
    overrides_json = None
    if ru.kind in ("debug", "test", "testdebug") and ru.overrides is not None:
        for key in ru.overrides:
            if not re.match(r"^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$", key):
                raise HTTPException(status_code=400, detail=f"Invalid override key '{key}': must be 'set::name' format.")
        overrides_json = json.dumps(ru.overrides)

    job = runner.enqueue(ru.set, ru.name, user, ru.kind, ru.drop_done, ru.param, ru.max_retries, overrides_json)

    return {"job": job}

@router.get("/scriptlet/job/item", tags=["job"])
async def job_get(id: str):
    """
    Gets a new Job
    """
    job = runner.query(id)
    if job == None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

    data_base = files.conf_data()["jobs"]
    setspace = os.path.join(data_base, job.set)
    workspace = os.path.join(setspace, job.id)
    stdout_log_path = os.path.join(workspace, "stdout.log")
    stderr_log_path = os.path.join(workspace, "stderr.log")

    stdout = None
    stderr = None

    if os.path.exists(stdout_log_path):
        with open(stdout_log_path, 'r') as fout:
            stdout = fout.read()

    if os.path.exists(stderr_log_path):
        with open(stderr_log_path, 'r') as fout:
            stderr = fout.read()

    return {
        "id": job.id,
        "owner": job.owner,
        "set": job.set,
        "name": job.name,
        "kind": job.kind,
        "param": job.param,
        "state": job.state,
        "detail": job.detail,
        "start": job.start,
        "end": job.end,
        "stdout": stdout,
        "stderr": stderr
    }

@router.delete("/scriptlet/job/item", tags=["job"])
async def job_del(id: str):
    """Marks a job for deletion (409 unless it is todo/cancelled/failed/done)."""
    if runner.remove(id) is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Job {id} not existing")
    return {}

@router.get("/scriptlet/job/cancel", tags=["job"])
async def cancel(id: str):
    """Cancels a job that has not finished; a job already cancelled is
    answered as it is, a finished or deleted one with 409."""
    if runner.cancel(id) is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))
    return {}

@router.get("/scriptlet/job/download", tags=["job"])
async def download_get(id: str):
    job = runner.query(id)
    if job == None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

    data_base = files.conf_data()["jobs"]
    setspace = os.path.join(data_base, f"{job.set}")
    workspace = os.path.join(setspace, f"{job.id}")
    # A job that has not started yet, or whose workspace was dropped, has none.
    if not os.path.isdir(workspace):
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No workspace for job {id}")

    zs = ZipStream(compress_type=ZIP_DEFLATED)

    zs.add_path(workspace)

    return StreamingResponse(zs.finalize(), media_type="application/x-zip-compressed", headers={
        'Content-Disposition': f'attachment; filename="{id}.zip"'
    })
