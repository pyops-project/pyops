import os.path

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet
from orm.job import Job

from helper import runner, files

router = APIRouter()

class RunArgument(BaseModel):
    group: str
    name: str
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
                          Job.kind,
                          Job.state,
                          Job.detail,
                          Job.param).where(Job.state != "remove").order_by(Job.id)
            db_jobs = session.execute(stmt)
            
            jobs = [dict(r._mapping) for r in db_jobs] + runner.orphans()

            return jobs
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.put("/scriptlet/job/item", tags=["job"])
async def job_put(ru: RunArgument):
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (ru.group, ru.name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}/{} not existing".format(ru.group, ru.name))

            return {"job": runner.enqueue(ru.group, ru.name, "TODO", "debug" if ru.debug else "task", ru.param)}

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

        log_base = files.conf_log()["jobs"]
        stdout_log_path = os.path.join(log_base, "{}.out.log".format(id))
        stderr_log_path = os.path.join(log_base, "{}.err.log".format(id))

        if os.path.exists(stdout_log_path):
            with open(stdout_log_path, 'r') as fout:
                job["stdout"] = fout.read()
        
        if os.path.exists(stderr_log_path):
            with open(stderr_log_path, 'r') as fout:
                job["stderr"] = fout.read()

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/job/item", tags=["job"])
async def job_del(id: str):
    try:
        job = runner.remove(id)
        if job == None:
            if runner.drop(id):
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