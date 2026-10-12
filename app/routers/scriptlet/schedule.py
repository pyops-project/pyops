import croniter
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from starlette.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST
from pydantic import BaseModel, Field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.schedule import Schedule

from app.lib import runner
from app.lib import git
from app.lib.async_utils import run_in_executor
from app.lib.auth import get_current_user

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

class ScheduleArgument(BaseModel):
    id: Optional[int] = None
    set: str
    name: str
    drop_done: bool
    max_retries: int = Field(default=0, ge=0)
    # Done jobs kept despite drop_done (cron schedules only; 0 = none).
    keep_n: int = Field(default=0, ge=0, le=100)
    # Failed jobs kept (cron) / failures that stop the restarts (service).
    max_fail_n: int = Field(default=3, ge=1, le=100)
    schedule: str
    param: str
    # Whether the schedule starts active. Defaults to True for backwards
    # compatibility with the UI's "create then deactivate" pattern. Tests
    # that need to avoid the auto-start race window (between create-active
    # and an explicit deactivate) pass `active=False` to skip the window
    # entirely. Honored only on create — updates do not change `active`
    # (use the dedicated activate/deactivate endpoints).
    active: bool = True

@router.get("/scriptlet/schedule/list", tags=["schedule"])
async def list_get():
    """
    Returns a list of available Schedules
    """
    with Session(get_db_engine()) as session:
        stmt = select(Schedule.id,
                      Schedule.set,
                      Schedule.name,
                      Schedule.active,
                      Schedule.drop_done,
                      Schedule.max_retries,
                      Schedule.keep_n,
                      Schedule.max_fail_n,
                      Schedule.schedule,
                      Schedule.last,
                      Schedule.param)
        schedules = session.execute(stmt)

        return [dict(r._mapping) for r in schedules]

@router.get("/scriptlet/schedule/item", tags=["schedule"])
async def item_get(id: int):
    """
    Gets a Schedule by ID
    """
    with Session(get_db_engine()) as session:
        schedule = session.get(Schedule, id)
        if schedule == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

        return schedule.__dict__ if schedule else None

@router.get("/scriptlet/schedule/run", tags=["schedule"])
async def run_get(id: int):
    """
    Manually runs a Schedule as a job
    """
    with Session(get_db_engine()) as session:
        schedule = session.get(Schedule, id)
    if schedule == None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

    job_id = runner.enqueue(schedule.set, schedule.name, schedule.owner, "task" if schedule.schedule else "service", schedule.drop_done, schedule.param, schedule.max_retries, None, schedule_id=schedule.id)

    with Session(get_db_engine()) as session:
        updated_schedule = session.get(Schedule, id)
        updated_schedule.last = datetime.now()
        session.commit()

    return {"job": job_id}

@router.put("/scriptlet/schedule/item", tags=["schedule"])
async def item_put(cr: ScheduleArgument, user: str = Depends(get_current_user)):
    """
    Creates or updates a Schedule
    """
    repo = await run_in_executor(git.get_repo_path, cr.set)
    # Check exists (committed or unstaged via fallback).
    try:
        await run_in_executor(git.read_content, repo, cr.name, "task", committed=True)
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {cr.set}::{cr.name} not existing.")

    if cr.schedule:
        now = datetime.now()
        try:
            croniter.croniter(cr.schedule, now)
        except (croniter.CroniterBadCronError, ValueError) as ex:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {ex}",
            )

    with Session(get_db_engine()) as session:
        if cr.id is not None:
            # Update existing
            schedule = session.get(Schedule, cr.id)
            if schedule == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(cr.id))
            schedule.set = cr.set
            schedule.name = cr.name
            schedule.drop_done = cr.drop_done
            schedule.max_retries = cr.max_retries
            schedule.keep_n = cr.keep_n
            schedule.max_fail_n = cr.max_fail_n
            schedule.schedule = cr.schedule
            schedule.param = cr.param
            # Do not update owner, active, last
        else:
            # Create new
            schedule = Schedule(
                set       = cr.set,
                name        = cr.name,
                owner       = user,
                active      = cr.active,
                drop_done   = cr.drop_done,
                max_retries = cr.max_retries,
                keep_n      = cr.keep_n,
                max_fail_n  = cr.max_fail_n,
                schedule    = cr.schedule,
                param       = cr.param,
                last        = datetime.now()
            )
            session.add(schedule)

        session.commit()

        return {"schedule": schedule.id}

@router.delete("/scriptlet/schedule/item", tags=["schedule"])
async def item_del(id: int):
    """
    Deletes a Schedule
    """
    with Session(get_db_engine()) as session:
        schedule = session.get(Schedule, id)
        if schedule == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))
        
        session.delete(schedule)
        session.commit()

@router.get("/scriptlet/schedule/activate", tags=["schedule"])
async def activate_get(id: int):
    """
    Activates a Schedule
    """
    with Session(get_db_engine()) as session:
        schedule = session.get(Schedule, id)
        if schedule == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

        now = datetime.now()
        schedule.active = True
        schedule.last = now
        session.commit()

@router.get("/scriptlet/schedule/deactivate", tags=["schedule"])
async def deactivate_get(id: int):
    """
    Deactivates a Schedule
    """
    with Session(get_db_engine()) as session:
        schedule = session.get(Schedule, id)
        if schedule == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Schedule {} not existing".format(id))

        schedule.active = False
        session.commit()
