from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel
import json
import time
import re
import jedi
jedi.settings.fast_parser=False

from app.lib import sites

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

class CompletionArgument(BaseModel):
    code: str
    line: int
    column: int

@router.get("/scriptlet/editor_task")
async def get_editor_task():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_task"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_task: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_library")
async def get_editor_library():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_library"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_library: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_view")
async def get_editor_view():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_view"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_view: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_md")
async def get_editor_md():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_md"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_md: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_data")
async def get_editor_data():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_data"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_data: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_jslibrary")
async def get_editor_jslibrary():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_jslibrary"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get_editor_jslibrary: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.post("/scriptlet/completion")
async def completion(co: CompletionArgument):
    try:
        script = jedi.Script(code=co.code)

        type_scores = {
            'module': 1,
            'class': 2,
            'instance': 7,
            'function': 8,
            'param': 9,
            'path': 3,
            'keyword': 4,
            'property': 5,
            'statement': 6
        }

        completions = []
        for completion in script.complete(line=co.line, column=co.column):
            completions.append({
                "name": completion.name,
                "value": completion.name,
                "meta": completion.docstring(),
                "score": type_scores.get(completion.type, 0)
            })
        
        return completions
    except Exception as ex:
        logger.exception("Error in completion: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
