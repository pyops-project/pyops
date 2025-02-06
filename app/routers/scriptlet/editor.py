from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel
import json
import time
import jedi
import re

from lib import sites

router = APIRouter()

class CompletionArgument(BaseModel):
    code: str
    line: int
    column: int

@router.get("/scriptlet/editor_task")
async def get():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_task"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/editor_library")
async def get():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor_library"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.post("/scriptlet/completion")
async def pylint(co: CompletionArgument):
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
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))



#{
#        name: ea.word,
#        value: ea.word,
#        score: ea.score,
#        meta: "rhyme"
#      }