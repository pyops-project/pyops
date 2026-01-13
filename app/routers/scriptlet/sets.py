from fastapi import APIRouter, HTTPException
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
async def clone_set_endpoint(request: CloneRequest):
    try:
        git_base = files.conf_data()["git"]
        name = sets.clone_set(git_base, request.url, request.username, request.password, request.git_name, request.git_email)
        return {"status": "ok", "message": f"Cloned set '{name}'"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scriptlet/sets/create")
async def create_set_endpoint(request: CreateRequest):
    try:
        git_base = files.conf_data()["git"]
        sets.create_set(git_base, request.name)
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
