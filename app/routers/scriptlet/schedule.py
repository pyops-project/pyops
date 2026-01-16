import croniter
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Header
from starlette.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.schedule import Schedule

from app.lib import runner
from app.lib import git
from app.lib.auth import sanitize_owner

import logging
logger = logging.getLogger(__name__)

async def get_current_user(x_user: str = Header(default=None, alias="X-User")):
    return sanitize_owner(x_user)

router = APIRouter()

class CreateArgument(BaseModel):
    set: str
    name: str
    drop_done: bool
    max_retries: int = 0
    schedule: str
    param: str

@router.get("/scriptlet/schedule/list", tags=["schedule"])
async def list_get():
    """
    Returns a list of available Schedules
    """
    try:
        with Session(get_db_engine()) as session:
            stmt = select(Schedule.id,
                          Schedule.set,
                          Schedule.name,
                          Schedule.active,
                          Schedule.drop_done,
                          Schedule.schedule,
                          Schedule.last,
                          Schedule.param)
            schedules = session.execute(stmt)
            
            return [dict(r._mapping) for r in schedules]
    except Exception as ex:
        logger.exception("Error in list_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/item", tags=["schedule"])
async def item_get(id: int):
    """
    Gets a Schedule by ID
    """
    try:
        with Session(get_db_engine()) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

            return schedule.__dict__ if schedule else None

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/run", tags=["schedule"])
async def run_get(id: int):
    """
    Manually runs a Schedule as a job
    """
    try:
        with Session(get_db_engine()) as session:
            schedule = session.get(Schedule, id)
        if schedule == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

        job_id = runner.enqueue(schedule.set, schedule.name, schedule.owner, "task" if schedule.schedule else "service", schedule.drop_done, schedule.param, schedule.max_retries, None)

        with Session(get_db_engine()) as session:
            updated_schedule = session.get(Schedule, id)
            updated_schedule.last = datetime.now()
            session.commit()

        return {"job": job_id}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in run_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.put("/scriptlet/schedule/item", tags=["schedule"])
async def item_put(cr: CreateArgument, user: str = Depends(get_current_user)):
    """
    Creates a new Schedule
    """
    try:
        repo = git.get_repo_path(cr.set)
        # Check exists (committed or unstaged via fallback).
        try:
            git.read_content(repo, cr.name, "task", committed=True)
        except FileNotFoundError:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {cr.set}::{cr.name} not existing.")

        if cr.schedule:
            now = datetime.now()
            croniter.croniter(cr.schedule, now)

        with Session(get_db_engine()) as session:
            schedule = Schedule(
                set       = cr.set,
                name        = cr.name,
                owner       = user,
                active      = True,
                drop_done   = cr.drop_done,
                max_retries = cr.max_retries,
                schedule    = cr.schedule,
                param       = cr.param
            )

            session.add(schedule)
            session.commit()

            return {"schedule": schedule.id}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_put: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))

@router.delete("/scriptlet/schedule/item", tags=["schedule"])
async def item_del(id: int):
    """
    Deletes a Schedule
    """
    try:
        with Session(get_db_engine()) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))
            
            session.delete(schedule)
            session.commit()

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_del: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/activate", tags=["schedule"])
async def activate_get(id: int):
    """
    Activates a Schedule
    """
    try:
        with Session(get_db_engine()) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

            now = datetime.now()
            schedule.active = True
            schedule.last = now
            session.commit()

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in activate_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/schedule/deactivate", tags=["schedule"])
async def deactivate_get(id: int):
    """
    Deactivates a Schedule
    """
    try:
        with Session(get_db_engine()) as session:
            schedule = session.get(Schedule, id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

            schedule.active = False
            session.commit()
            
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in deactivate_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
