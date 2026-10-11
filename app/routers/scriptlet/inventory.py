import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from starlette.status import HTTP_204_NO_CONTENT, HTTP_409_CONFLICT, HTTP_500_INTERNAL_SERVER_ERROR, HTTP_400_BAD_REQUEST
from pydantic import BaseModel, create_model

from app.lib import git, files
from app.lib.async_utils import run_in_executor
from app.lib.auth import get_current_user

import logging
logger = logging.getLogger(__name__)

# Errors are raised typed by app.lib.git (invalid name 400, bundled set 400,
# unknown set or scriptlet 404, name taken / no committed version 409) and
# mapped to HTTP by the one handler in app/web.py — no route catches them.

router = APIRouter()


class CreateArgument(BaseModel):
    set: str
    name: str
    type: str

class SaveArgument(BaseModel):
    set: str
    name: str
    code: str  # Now 'content' for views, but kept for compat

@router.get("/scriptlet/inventory/list", tags=["scriptlet"])
async def list_get():
    """
    Returns a list of available Scriptlets
    """
    return await run_in_executor(git.list_scriptlets)

@router.put("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_put(cr: CreateArgument, user: str = Depends(get_current_user)):
    """
    Creates a new Scriptlet
    """
    git.validate_set_name(cr.set)
    git_base = git.get_git_base()
    potential_repo = os.path.join(git_base, cr.set)
    # The bundled set is refused by create_scriptlet.
    if not files.is_bundled_set(cr.set) and not os.path.exists(os.path.join(potential_repo, '.git')):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Set does not exist")

    if cr.type == "view":
        initial_content = '<div class="container">\n  <!-- View content here -->\n</div>'
    elif cr.type == "jslibrary":
        initial_content = "// JS Library\n"
    elif cr.type == "markdown":
        # README (top-level) gets a heading; docs/<name>.md starts blank
        initial_content = f"# {cr.set}\n" if cr.name == "README" else ""
    elif cr.type == "data":
        # New YAML files start empty rather than `{}`: empty parses
        # to None / {} via yaml.safe_load (see app/lib/data.py
        # `_deserialise`), and unlike `{}` it lets the author start
        # typing block-style entries without removing a flow-mapping
        # placeholder first.
        initial_content = ""
    else:
        initial_content = "pass\n"

    await run_in_executor(
        git.create_scriptlet,
        cr.set, cr.name, cr.type, initial_content,
    )

    return {"set": cr.set, "name": cr.name, "type": cr.type}

@router.post("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_post(sa: SaveArgument):
    """
    Saves a Scriptlet's draft content (unstaged write-only; no commit/push).
    Use for auto-save; follow with /item POST for explicit save.
    """
    repo = await run_in_executor(git.get_editable_repo_path, sa.set)
    # The type is the existing file's; markdown and data files round-trip
    # through the same route.
    scriptlet = await run_in_executor(git.resolve_scriptlet, repo, sa.name)
    await run_in_executor(git.write_content, repo, sa.name, scriptlet.type, sa.code)
    # No git add/commit/push: leaves unstaged for draft workflow.

@router.delete("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_del(set: str, name: str, user: str = Depends(get_current_user)):
    """
    Drafts a Scriptlet deletion (working-tree only; no commit, no push).
    The deletion is folded into the next set-wide `commit_drafts` batch.
    """
    repo = await run_in_executor(git.get_editable_repo_path, set)
    scriptlet = await run_in_executor(git.resolve_scriptlet, repo, name)
    await run_in_executor(git.delete_file_unstaged, repo, scriptlet.path)

@router.delete("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_del(set: str = Query(..., description="Set"), name: str = Query(..., description="Name")):
    """
    Discards a Scriptlet's draft (unstaged changes): resets to committed HEAD.
    The type is inferred from the existing file.
    Returns 200 once the working tree matches the committed content; 404 for
    an unknown set or scriptlet; 409 when the scriptlet was never committed
    (no committed version to revert to; the draft is kept).
    """
    repo = await run_in_executor(git.get_editable_repo_path, set)
    scriptlet = await run_in_executor(git.resolve_scriptlet, repo, name)
    type_ = scriptlet.type

    # Perform rollback (no .bck); a scriptlet that was never committed has
    # nothing to revert to — the draft is left untouched.
    try:
        await run_in_executor(git.rollback_committed, set, name, type_)
    except git.NoCommittedVersionError:
        logger.warning("No committed version for discard of %s::%s.%s", set, name, type_)
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="No committed version to revert to")

    # Verify post-checkout
    committed_content = await run_in_executor(git.read_content, repo, name, type_, committed=True, strict=True)

    if os.path.exists(scriptlet.path):
        with open(scriptlet.path, "r", encoding="utf-8") as f:
            current_content = f.read()
        # Strip both sides: read_content(committed=True) goes through
        # _check() which strips whitespace from git show output.
        if current_content.strip() == committed_content.strip():
            logger.info("Discarded draft for %s::%s.%s; reset to committed", set, name, type_)
            return
        logger.warning("Content mismatch post-discard for %s::%s.%s", set, name, type_)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Partial discard detected")
    logger.error("File missing post-discard for %s::%s.%s", set, name, type_)
    raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="File lost post-discard")

@router.get("/scriptlet/inventory/code", tags=["scriptlet"])
async def code_get(set: str, name: str):
    repo = await run_in_executor(git.get_repo_path, set)
    # Any type: markdown pages under docs/ and yaml/json data files
    # round-trip through the same endpoint as Python scriptlets.
    scriptlet = await run_in_executor(git.resolve_scriptlet, repo, name)
    content = await run_in_executor(git.read_content, repo, name, scriptlet.type, committed=False)
    # For data files, expose the actual on-disk extension so the
    # editor (and other clients) can pick the right syntax mode.
    response: dict = {"code": content, "type": scriptlet.type}
    if scriptlet.type == "data":
        response["ext"] = scriptlet.ext
    return response

@router.get("/scriptlet/inventory/sets", tags=["scriptlet"])
async def list_sets():
    """
    Lists existing sets (git repos).
    """
    return {"sets": await run_in_executor(git.list_sets)}


@router.get("/repo/status", tags=["repo"])
async def repo_status(set: str = Query(..., description="Set name")):
    """Gets repo status (branch, tag flag)."""
    return await run_in_executor(git.get_repo_status, set)

@router.get("/scriptlet/inventory/status", tags=["scriptlet"])
async def status_get(set: str, name: str):
    """
    Checks if the scriptlet has unstaged changes.
    """
    repo = await run_in_executor(git.get_repo_path, set)
    scriptlet = await run_in_executor(git.resolve_scriptlet, repo, name)
    has_unstaged = await run_in_executor(git.has_unstaged_changes, repo, name, scriptlet.type)
    logger.debug(f"Status for {set}::{name}: {has_unstaged}")
    return {"has_unstaged": has_unstaged}
