from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_400_BAD_REQUEST
from pydantic import BaseModel
import jedi
import json
import time
import re
import parso
jedi.settings.fast_parser=False

from app.lib import sites

router = APIRouter()

class CompletionArgument(BaseModel):
    code: str
    line: int
    column: int

@router.get("/scriptlet/editor_task")
async def get_editor_task():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_task"),
        status_code=HTTP_200_OK
    )

@router.get("/scriptlet/editor_library")
async def get_editor_library():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_library"),
        status_code=HTTP_200_OK
    )

@router.get("/scriptlet/editor_view")
async def get_editor_view():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_view"),
        status_code=HTTP_200_OK
    )

@router.get("/scriptlet/editor_md")
async def get_editor_md():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_md"),
        status_code=HTTP_200_OK
    )

@router.get("/scriptlet/editor_data")
async def get_editor_data():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_data"),
        status_code=HTTP_200_OK
    )

@router.get("/scriptlet/editor_jslibrary")
async def get_editor_jslibrary():
    return HTMLResponse(
        content=sites.get("scriptlet/editor_jslibrary"),
        status_code=HTTP_200_OK
    )

def _validate_position(code: str, line: int, column: int) -> None:
    """Rejects a cursor position outside `code` (e.g. a stale position after
    an edit) with 400 — the range jedi accepts: line 1..number of lines,
    column 0..length of that line without its line break."""
    lines = parso.split_lines(code, keepends=True)
    if not 0 < line <= len(lines):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST,
                            detail=f"line {line} is not in the code (1-{len(lines)})")
    line_len = len(lines[line - 1].rstrip("\r\n"))
    if not 0 <= column <= line_len:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST,
                            detail=f"column {column} is not in line {line} (0-{line_len})")

@router.post("/scriptlet/completion")
async def completion(co: CompletionArgument):
    _validate_position(co.code, co.line, co.column)
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
