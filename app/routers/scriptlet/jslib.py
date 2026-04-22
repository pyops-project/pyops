from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from starlette.status import HTTP_200_OK
import logging
from app.lib import git
from app.lib.async_utils import run_in_executor

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scriptlet/jslib/{set}/{name}")
async def get_jslib(set: str, name: str, committed: bool = Query(True, description="Use committed version instead of unstaged")):
    try:
        repo = git.get_repo_path(set)
        files = await run_in_executor(git.list_scriptlet_files, repo, name)
        if not files:
            raise HTTPException(404, f"Scriptlet '{name}' not found in set '{set}'")
        filename = files[0]
        type_ = git.infer_type_from_filename(filename)
        if type_ != "jslibrary":
            raise HTTPException(400, f"Scriptlet '{name}' is not a JS library")
        code = await run_in_executor(git.read_content, repo, name, type_, committed)
        return Response(content=code, media_type="application/javascript")
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception(f"Error serving JS library {set}::{name}: {ex}")
        raise HTTPException(500, str(ex))