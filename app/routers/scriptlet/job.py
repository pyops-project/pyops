import os.path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet
from orm.job import Job

from helper import runner, files

from zipstream import ZipStream, ZIP_DEFLATED

router = APIRouter()

class RunArgument(BaseModel):
    group: str
    name: str
    drop_done: bool
    param: str
    debug: bool = False

@router.get("/scriptlet/job/list", tags=["job"])
async def list_get():
    """
    Returns a list of available Schedules
    """
    try:
        with Session(engine) as session:
            stmt = select(Job.id,
                          Job.start,
                          Job.end,
                          Job.kind,
                          Job.state,
                          Job.detail,
                          Job.param).where(Job.state != "remove").order_by(Job.id.desc())
            db_jobs = session.execute(stmt)
            
            jobs = [dict(r._mapping) for r in db_jobs]

            return jobs
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.put("/scriptlet/job/item", tags=["job"])
async def job_put(ru: RunArgument):
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (ru.group, ru.name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(ru.group, ru.name))

            return {"job": runner.enqueue(ru.group, ru.name, "TODO", "debug" if ru.debug else "task", ru.drop_done, ru.param)}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/job/item", tags=["job"])
async def job_get(id: str):
    try:
        job = runner.query(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        data_base = files.conf_data()["jobs"]
        userspace = os.path.join(data_base, job.owner)
        groupspace = os.path.join(userspace, job.group)
        workspace = os.path.join(groupspace, job.id)
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
            "group": job.group,
            "state": job.state,
            "detail": job.detail,
            "stdout": stdout,
            "stderr": stderr
        }

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/job/item", tags=["job"])
async def job_del(id: str):
    try:
        job = runner.remove(id)
        if job == None:
            if runner.drop(id, job.owner, job.group):
                return {"id": id}
            else:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
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
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.get("/scriptlet/job/download", tags=["job"])
async def download_get(id: str):
    try:
        job = runner.query(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        data_base = files.conf_data()["jobs"]
        userspace = os.path.join(data_base, f"{job.owner}")
        groupspace = os.path.join(userspace, f"{job.group}")
        workspace = os.path.join(groupspace, f"{job.id}")

        zs = ZipStream(compress_type=ZIP_DEFLATED)

        zs.add_path(workspace)

        return StreamingResponse(zs.finalize(), media_type="application/x-zip-compressed", headers={
            'Content-Disposition': f'attachment; filename="{id}.zip"'
        })

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))