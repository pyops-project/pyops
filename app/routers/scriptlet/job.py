import os.path
import subprocess
import json
import re

from fastapi import APIRouter, HTTPException, Depends, Header, Query
from fastapi.responses import StreamingResponse
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.job import Job

from app.lib import runner, files, git
from app.lib.auth import sanitize_owner

from zipstream import ZipStream, ZIP_DEFLATED

import logging
logger = logging.getLogger(__name__)

async def get_current_user(x_user: str = Header(default=None, alias="X-User")):
    return sanitize_owner(x_user)

router = APIRouter()

class RunArgument(BaseModel):
    set: str
    name: str
    drop_done: bool
    param: str
    kind: Literal["task", "draft", "debug"] = "task"
    overrides: dict[str, str] | None = None

@router.get("/scriptlet/job/list", tags=["job"])
async def list_get(user: str = Depends(get_current_user)):
    """
    Returns a list of available Schedules
    """
    try:
        with Session(get_db_engine()) as session:
            stmt = select(Job.id,
                          Job.start,
                          Job.end,
                          Job.kind,
                          Job.state,
                          Job.detail,
                          Job.param,
                          Job.owner).where(
                Job.state != "remove",
                Job.owner == user
            ).order_by(Job.id.desc())
            db_jobs = session.execute(stmt)
            
            jobs = [dict(r._mapping) for r in db_jobs]

            return jobs
    except Exception as ex:
        logger.exception("Error in list_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.put("/scriptlet/job/item", tags=["job"])
async def job_put(ru: RunArgument, user: str = Depends(get_current_user)):
    """
    Creates a new Job
    """
    try:
        repo = git.get_repo_path(ru.set)
        # Check exists (committed or unstaged via fallback).
        try:
            git.read_content(repo, ru.name, "task", committed=True)
        except FileNotFoundError:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {ru.set}::{ru.name} not existing.")

        # Validate overrides for debug kind
        overrides_json = None
        if ru.kind == "debug" and ru.overrides is not None:
            for key in ru.overrides:
                if not re.match(r"^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$", key):
                    raise HTTPException(status_code=400, detail=f"Invalid override key '{key}': must be 'set::name' format.")
            overrides_json = json.dumps(ru.overrides)

        # "debug" kind skeleton for future VSIX integration (e.g., special execution mode)
        job = runner.enqueue(ru.set, ru.name, user, ru.kind, ru.drop_done, ru.param, overrides=overrides_json)

        return {"job": job}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in job_put: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/job/item", tags=["job"])
async def job_get(id: str):
    """
    Gets a new Job
    """
    try:
        job = runner.query(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        data_base = files.conf_data()["jobs"]
        userspace = os.path.join(data_base, job.owner)
        setspace = os.path.join(userspace, job.set)
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
            "state": job.state,
            "detail": job.detail,
            "stdout": stdout,
            "stderr": stderr
        }

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in job_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/job/item", tags=["job"])
async def job_del(id: str):
    try:
        job = runner.query(id)
        if job is None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Job {id} not existing")

        allowed_states = ["todo", "cancelled", "killed", "failed", "done"]
        if job.state not in allowed_states:
            raise HTTPException(status_code=400, detail=f"Job {id} is in state '{job.state}' and cannot be deleted. Allowed states: {', '.join(allowed_states)}")

        job = runner.remove(id)
        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in job_del: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/job/cancel", tags=["job"])
async def cancel(id: str):
    try:
        job = runner.cancel(id)

        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in cancel: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.get("/scriptlet/job/download", tags=["job"])
async def download_get(id: str):
    try:
        job = runner.query(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        data_base = files.conf_data()["jobs"]
        userspace = os.path.join(data_base, f"{job.owner}")
        setspace = os.path.join(userspace, f"{job.set}")
        workspace = os.path.join(setspace, f"{job.id}")

        zs = ZipStream(compress_type=ZIP_DEFLATED)

        zs.add_path(workspace)

        return StreamingResponse(zs.finalize(), media_type="application/x-zip-compressed", headers={
            'Content-Disposition': f'attachment; filename="{id}.zip"'
        })

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in download_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
