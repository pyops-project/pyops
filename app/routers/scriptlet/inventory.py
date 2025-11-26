import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Depends, Header
from starlette.status import HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, HTTP_500_INTERNAL_SERVER_ERROR, HTTP_400_BAD_REQUEST
from pydantic import BaseModel, create_model

from app.lib import git
from app.lib.auth import sanitize_owner

import logging
logger = logging.getLogger(__name__)

async def get_current_user(x_user: str = Header(default=None, alias="X-User")):
    return sanitize_owner(x_user)

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
    try:
        return git.list_scriptlets()
    except Exception as ex:
        logger.exception("Error in list_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.put("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_put(cr: CreateArgument, user: str = Depends(get_current_user)):
    """
    Creates a new Scriptlet
    """
    try:
        git_base = git.get_git_base()
        potential_repo = os.path.join(git_base, cr.set)
        if not os.path.exists(os.path.join(potential_repo, '.git')):
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Set does not exist")

        repo = git.get_repo_path(cr.set)
        if not git.check_name_unique(repo, cr.name):
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Name {} already exists".format(cr.name))

        if cr.type == "markdown" and cr.name != "README":
            raise HTTPException(400, "Markdown only for README.")
        
        if cr.type == "view":
            initial_content = '<div class="container">\n  <!-- View content here -->\n</div>'
        else:
            initial_content = "" if cr.type == "markdown" else "pass\n"
        git.write_content(repo, cr.name, cr.type, initial_content)
        file_path = git.get_file_path(repo, cr.name, cr.type)
        git.perform_git_add_commit_push(repo, file_path, f"Create {cr.name}.{cr.type} by {user}")

        return {"set": cr.set, "name": cr.name, "type": cr.type}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_put: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
    
@router.post("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_post(sa: SaveArgument, user: str = Depends(get_current_user)):
    """
    Updates a Scriptlet's content (with commit/push for explicit save).
    """
    try:
        repo = git.get_repo_path(sa.set)
        # Infer type from existing file.
        files = [f for f in os.listdir(repo) if f.startswith(f"{sa.name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(sa.set, sa.name))
        filename = files[0]  # Assume unique.
        type_ = git.infer_type_from_filename(filename)
        
        git.write_content(repo, sa.name, type_, sa.code)
        file_path = git.get_file_path(repo, sa.name, type_)
        git.perform_git_add_commit_push(repo, file_path, f"Update {sa.name}.{type_} by {user}")
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_post: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.post("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_post(sa: SaveArgument):
    """
    Saves a Scriptlet's draft content (unstaged write-only; no commit/push).
    Use for auto-save; follow with /item POST for explicit save.
    """
    try:
        repo = git.get_repo_path(sa.set)
        # Infer type from existing file.
        files = [f for f in os.listdir(repo) if f.startswith(f"{sa.name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(sa.set, sa.name))
        filename = files[0]  # Assume unique.
        type_ = git.infer_type_from_filename(filename)
        
        git.write_content(repo, sa.name, type_, sa.code)
        # No git add/commit/push: leaves unstaged for draft workflow.
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in draft_post: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_del(set: str, name: str, user: str = Depends(get_current_user)):
    """
    Deletes a Scriptlet
    """
    try:
        repo = git.get_repo_path(set)
        files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(set, name))
        filename = files[0]
        type_ = git.infer_type_from_filename(filename)
        file_path = git.get_file_path(repo, name, type_)
        git.delete_file_and_commit(repo, file_path, f"Delete {name}.{type_} by {user}")
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in item_del: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_del(set: str = Query(..., description="Set"), name: str = Query(..., description="Name"), type_: str = Query(None, description="Type")):
    """
    Discards a Scriptlet's draft (unstaged changes): resets to committed HEAD.
    Infers type from existing file if not provided or invalid.
    Returns 204 on full success (content match); raises on fail.
    """
    try:
        repo = git.get_repo_path(set)
        files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
        filename = files[0]
        inferred_type = git.infer_type_from_filename(filename)
        if type_ is not None and type_ != inferred_type:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Provided type '{type_}' mismatches inferred '{inferred_type}'")
        type_ = inferred_type  # Use inferred if None or valid.

        file_path = git.get_file_path(repo, name, type_)

        # Perform rollback (no .bck)
        git.rollback_committed(set, name, type_)

        # Verify post-checkout
        committed_content = git.read_content(repo, name, type_, committed=True, strict=True)

        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                current_content = f.read()
            if current_content == committed_content:
                logger.info("Discarded draft for %s::%s.%s; reset to committed", set, name, type_)
                return  # 204 No Content
            else:
                logger.warning("Content mismatch post-discard for %s::%s.%s", set, name, type_)
                raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Partial discard detected")
        else:
            logger.error("File missing post-discard for %s::%s.%s", set, name, type_)
            raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="File lost post-discard")

    except FileNotFoundError:
        logger.warning("No committed version for discard of %s::%s.%s", set, name, type_)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="No committed version to revert to")
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("Error in draft_del: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))

@router.get("/scriptlet/inventory/code", tags=["scriptlet"])
async def code_get(set: str, name: str):
    try:
        repo = git.get_repo_path(set)
        files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(set, name))
        filename = files[0]
        type_ = git.infer_type_from_filename(filename)
        content = git.read_content(repo, name, type_, committed=False)
        return {"code": content}
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in code_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.get("/scriptlet/inventory/sets", tags=["scriptlet"])
async def list_sets():
    """
    Lists existing sets (git repos).
    """
    try:
        git_base = git.get_git_base()
        sets = [d for d in os.listdir(git_base)
                  if os.path.isdir(os.path.join(git_base, d))
                  and os.path.exists(os.path.join(git_base, d, ".git"))]
        return {"sets": sorted(sets)}
    except Exception as ex:
        logger.exception("Error in list_sets: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.get("/repo/status", tags=["repo"])
async def repo_status(set: str = Query(..., description="Set name")):
    """Gets repo status (branch, tag flag)."""
    try:
        return git.get_repo_status(set)
    except Exception as ex:
        logger.exception("Error in repo_status: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))

@router.get("/scriptlet/inventory/status", tags=["scriptlet"])
async def status_get(set: str, name: str):
    """
    Checks if the scriptlet has unstaged changes.
    """
    try:
        repo = git.get_repo_path(set)
        files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith(('.py', '.html', '.md'))]
        if not files:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
        filename = files[0]
        type_ = git.infer_type_from_filename(filename)
        has_unstaged = git.has_unstaged_changes(repo, name, type_)
        logger.debug(f"Status for {set}::{name}: {has_unstaged}")
        return {"has_unstaged": has_unstaged}
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in status_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
