from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel
from typing import Optional
import os

from app.lib import files, sets, sites

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

class CloneRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    git_name: Optional[str] = None
    git_email: Optional[str] = None

class CreateRequest(BaseModel):
    name: str

class SwitchRequest(BaseModel):
    name: str
    branch: str

@router.get("/scriptlet/sets")
async def get_sets():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/sets"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get sets: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/sets/list")
async def list_sets_endpoint():
    try:
        git_base = files.conf_data()["git"]
        return {"status": "ok", "sets": sets.list_sets(git_base)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/clone")
async def clone_set_endpoint(request: CloneRequest, background_tasks: BackgroundTasks):
    try:
        git_base = files.conf_data()["git"]
        name = sets.clone_set(git_base, request.url, request.username, request.password, request.git_name, request.git_email)
        background_tasks.add_task(sets.ensure_set_environment, name)
        return {"status": "ok", "message": f"Cloned set '{name}'"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/create")
async def create_set_endpoint(request: CreateRequest, background_tasks: BackgroundTasks):
    try:
        git_base = files.conf_data()["git"]
        sets.create_set(git_base, request.name)
        background_tasks.add_task(sets.ensure_set_environment, request.name)
        return {"status": "ok", "message": f"Created set '{request.name}'"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/scriptlet/sets/remove/{name}")
async def remove_set_endpoint(name: str):
    try:
        git_base = files.conf_data()["git"]
        sets.remove_set(git_base, name)
        return {"status": "ok", "message": f"Removed set '{name}'"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/switch")
async def switch_branch_endpoint(request: SwitchRequest):
    try:
        git_base = files.conf_data()["git"]
        sets.switch_branch(git_base, request.name, request.branch)
        return {"status": "ok", "message": f"Switched '{request.name}' to '{request.branch}'"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/pull/{name}")
async def pull_set_endpoint(name: str):
    try:
        git_base = files.conf_data()["git"]
        sets.pull_set(git_base, name)
        return {"status": "ok", "message": f"Pulled set '{name}'"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/update_deps/{name}")
async def update_deps_endpoint(name: str):
    """Run `update_set_environment` synchronously and return the summary.

    Synchronous because the operator clicked the button and wants to see
    the result — including any conflict warnings — in the same UI round-
    trip. The flow holds the per-set install lock for its duration; running
    jobs in the same set will queue behind the upgrade, jobs in other sets
    are unaffected (different lock file).
    """
    try:
        git_base = files.conf_data()["git"]
        if not sets.list_sets(git_base) or not any(s["name"] == name for s in sets.list_sets(git_base)):
            raise HTTPException(status_code=404, detail=f"Set '{name}' not found.")
        summary = sets.update_set_environment(name)
        return {"status": "ok", "summary": summary}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("update_deps failed for %s: %s", name, e)
        raise HTTPException(status_code=500, detail=str(e))
