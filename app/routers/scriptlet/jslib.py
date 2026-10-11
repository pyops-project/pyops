from fastapi import APIRouter, Query
from fastapi.responses import Response
from starlette.status import HTTP_200_OK
import logging
from app.lib import git
from app.lib.async_utils import run_in_executor

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scriptlet/jslib/{set}/{name}")
async def get_jslib(set: str, name: str, committed: bool = Query(True, description="Use committed version instead of unstaged")):
    """Serves a JS library. Invalid set or name → 400; unknown set, no JS
    library of that name (also a name of another type) → 404 — raised
    typed by app.lib.git and mapped by the handler in app/web.py."""
    repo = await run_in_executor(git.get_repo_path, set)
    await run_in_executor(git.resolve_scriptlet, repo, name, "jslibrary")
    code = await run_in_executor(git.read_content, repo, name, "jslibrary", committed)
    return Response(content=code, media_type="application/javascript")
