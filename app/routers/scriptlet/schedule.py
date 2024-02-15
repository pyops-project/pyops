import croniter
from datetime import datetime

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet
from orm.schedule import Schedule

from helper import runner

router = APIRouter()

class CreateArgument(BaseModel):
    group: str
    name: str
    drop_done: bool
    schedule: str
    param: str

@router.get("/scriptlet/schedule/list", tags=["schedule"])
async def list_get():
    """
    Returns a list of available Schedules
    """
    try:
        with Session(engine) as session:
            stmt = select(Schedule.id,
                          Schedule.group,
                          Schedule.name,
                          Schedule.active,
                          Schedule.drop_done,
                          Schedule.schedule,
                          Schedule.param)
            schedules = session.execute(stmt)
            
            return [dict(r._mapping) for r in schedules]
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/item", tags=["schedule"])
async def item_get(id: int):
    try:
        with Session(engine) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {}::{} not existing".format(group, name))

            return schedule

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/run", tags=["schedule"])
async def run_get(id: int):
    try:
        with Session(engine) as session:
            schedule = session.get(Schedule, id)
            scriptlet = session.get(Scriptlet, (schedule.group, schedule.name))
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {}::{} not existing".format(group, name))

            return {"job": runner.enqueue(schedule.group, schedule.name, "TODO", "debug", False, schedule.param)}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.put("/scriptlet/schedule/item", tags=["schedule"])
async def item_put(cr: CreateArgument):
    """
    Creates a new Schedule
    """
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (cr.group, cr.name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(cr.group, cr.name))
            elif scriptlet.type != "task":
                raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Scriptlet {}::{} not of type task".format(cr.group, cr.name))

            now = datetime.now()
            croniter.croniter(cr.schedule, now)

            schedule = Schedule(
                group       = cr.group,
                name        = cr.name,
                owner       = "TODO",
                active      = True,
                drop_done   = cr.drop_done,
                schedule    = cr.schedule,
                param      = cr.param
            )

            session.add(schedule)
            session.commit()
        
        return {"result": "OK"}
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))

@router.delete("/scriptlet/schedule/item", tags=["schedule"])
async def item_del(id: int):
    """
    Deletes a Schedule
    """
    try:
        with Session(engine) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {}::{} not existing".format(group, name))
            
            session.delete(schedule)
            session.commit()

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/activate", tags=["schedule"])
async def activate_get(id: int):
    try:
        with Session(engine) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {}::{} not existing".format(group, name))

            now = datetime.now()
            schedule.active = True
            schedule.last = now
            session.commit()

            return {"result": "OK"}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/deactivate", tags=["schedule"])
async def activate_get(id: int):
    try:
        with Session(engine) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {}::{} not existing".format(group, name))

            schedule.active = False
            session.commit()

            return {"result": "OK"}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))