from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from starlette.status import HTTP_200_OK
import logging
from app.lib import git
from concurrent.futures import ThreadPoolExecutor
from asyncio import get_event_loop

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scriptlet/jslib/{set}/{name}")
async def get_jslib(set: str, name: str, committed: bool = Query(True, description="Use committed version instead of unstaged")):
    try:
        repo = git.get_repo_path(set)
        files = await get_event_loop().run_in_executor(None, git.list_scriptlet_files, repo, name)
        if not files:
            raise HTTPException(404, f"Scriptlet '{name}' not found in set '{set}'")
        filename = files[0]
        type_ = git.infer_type_from_filename(filename)
        if type_ != "jslibrary":
            raise HTTPException(400, f"Scriptlet '{name}' is not a JS library")
        code = await get_event_loop().run_in_executor(None, git.read_content, repo, name, type_, committed)
        return Response(content=code, media_type="application/javascript")
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception(f"Error serving JS library {set}::{name}: {ex}")
        raise HTTPException(500, str(ex))