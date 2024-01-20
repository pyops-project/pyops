import time

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet

from helper import runner

router = APIRouter()

class RunArgument(BaseModel):
    group: str
    name: str
    param: str
    debug: bool = False

@router.put("/scriptlet/job/item", tags=["scriptlet"])
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

@router.get("/scriptlet/job/item", tags=["scriptlet"])
async def job_get(id: int):
    try:
        job = runner.query(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/job/item", tags=["scriptlet"])
async def job_del(id: int):
    try:
        job = runner.remove(id)
        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/job/cancel", tags=["scriptlet"])
async def cancel(id: int):
    try:
        job = runner.cancel(id)

        if job == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job {} not existing".format(id))

        return job

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))