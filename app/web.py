import asyncio, traceback
import mimetypes
import os
import subprocess
import logging
import json

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_405_METHOD_NOT_ALLOWED, HTTP_500_INTERNAL_SERVER_ERROR

from .routers.scriptlet import job
from .routers.scriptlet import schedule
from .routers.scriptlet import inventory
from .routers.scriptlet import manager
from .routers.scriptlet import monitor
from .routers.scriptlet import scheduler
from .routers.scriptlet import editor
from .routers.scriptlet import sets
from .routers.scriptlet import jslib

from .lib import sites, files, pyops, git, runner
from .lib.async_utils import run_in_executor
from .lib.auth import sanitize_owner

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from pathlib import Path
from app.lib.files import Config

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    # Set default config path and initialize
    default_path = str(Path(__file__).parent.parent / "etc" / "pyops.json")
    Config.init(default_path)

app = FastAPI(docs_url=None)

app.include_router(inventory.router)
app.include_router(job.router)
app.include_router(scheduler.router)
app.include_router(schedule.router)
app.include_router(manager.router)
app.include_router(monitor.router)
app.include_router(editor.router)
app.include_router(sets.router)
app.include_router(jslib.router)

@app.get("/version")
def get_version():
    return {"version": files.version()}

@app.get("/debug/port")
def get_debug_port(request: Request):
    owner = sanitize_owner(request.headers.get("X-User", "anonymous"))
    users = files.conf_users()
    user_config = users.get(owner, {})
    port = user_config.get("debug_port")
    if port is None:
        raise HTTPException(status_code=400, detail=f"No debug_port configured for user '{owner}'")
    return {"port": int(port)}

@app.get("/")
async def root():
    try:
        return HTMLResponse(
            content=sites.get("root/root"),
            status_code=HTTP_200_OK
        )
    except Exception as ex:
        logger.exception("Error in root: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@app.get("/favicon.ico")
async def favicon():
    try:
        return Response(
            content=files.get("favicon.ico"),
            media_type="image/x-icon",
            status_code=HTTP_200_OK
        )
    except Exception as ex:
        logger.exception("Error in favicon: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@app.get("/files/{path:path}")
async def file(path: str):
    try:
        media_type, _ = mimetypes.guess_type(path)
        return Response(
            content=files.get(path),
            media_type=media_type or "application/octet-stream",
            status_code=HTTP_200_OK
        )
    except Exception as ex:
        logger.exception("Error in file: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

async def get_controller_code(set: str, name: str, debug: bool = True) -> str:
    """Loads controller code from Git, preferring unstaged if debug=True (default).

    Args:
        set (str): Set.
        name (str): Controller name.
        debug (bool): If True (default), load unstaged (working tree); else committed.

    Returns:
        str: Executable Python code.

    Raises:
        HTTPException: 404 if not found, 405 if wrong type.
        FileNotFoundError: No committed version if debug=False.
    """
    repo = await run_in_executor(git.get_repo_path, set)
    # Check exists and right type.
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    inferred_type = git.infer_type_from_filename(filename)
    if inferred_type != "controller":
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} is not a controller.")
    # Load based on debug.
    if debug:
        return await run_in_executor(git.read_content, repo, name, inferred_type, committed=False)
    else:
        return await run_in_executor(git.read_content, repo, name, inferred_type, committed=True, strict=True)

async def get_view_content(set: str, name: str, debug: bool = False) -> str:  # Renamed/Updated: Raw HTML, no exec
    """Loads view content from Git (.html), preferring unstaged if debug=True.

    Args:
        set (str): Set.
        name (str): View name.
        debug (bool): If True, load unstaged (working tree); else committed (default).

    Returns:
        str: Raw HTML content.

    Raises:
        HTTPException: 404 if not found, 405 if wrong type.
        FileNotFoundError: No committed version if debug=False.
    """
    repo = await run_in_executor(git.get_repo_path, set)
    # Check exists and right type.
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.html')]
    if not files_list:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    inferred_type = git.infer_type_from_filename(filename)
    if inferred_type != "view":
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} is not a view.")
    # Load based on debug.
    if debug:
        return await run_in_executor(git.read_content, repo, name, inferred_type, committed=False)
    else:
        return await run_in_executor(git.read_content, repo, name, inferred_type, committed=True, strict=True)

@app.get("/controller/{set}/{name}")
async def controller(set: str, name: str):
    try:
        glob = {}
        code = await get_controller_code(set, name)
        exec(code, glob)
        controller_debug = glob.get("debug", False)
        head_content = await get_view_content(set, glob["head"], debug=controller_debug)
        menu_content = await get_view_content(set, glob["menu"], debug=controller_debug)
        body_content = await get_view_content(set, glob["body"], debug=controller_debug)
        display_name = glob.get("name", name)

        head_content = head_content.replace("{{SET}}", set).replace("{{NAME}}", name)
        menu_content = menu_content.replace("{{SET}}", set).replace("{{NAME}}", name)
        body_content = body_content.replace("{{SET}}", set).replace("{{NAME}}", name)

        return HTMLResponse(
            content="""
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">

        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">

        <link rel="stylesheet" href="/files/css/bootstrap/bootstrap.min.css">
        <link rel="stylesheet" href="/files/css/material/material-icons.css">

        <script src="/files/js/jquery.min.js"></script>
        <script src="/files/js/js.cookie.min.js"></script>
        <script src="/files/js/bootstrap/bootstrap.bundle.min.js"></script>

        {{HEAD}}
    </head>
    <body class="bg-light">
        <nav class="navbar navbar-expand-md navbar-dark bg-dark text-light pt-3 sticky-top">
            <div class="container-fluid">
                <span class="navbar-brand">
                    <span class="material-icons-round">description</span>
                    <span>{{SET}}::{{DISPLAY_NAME}}</span>
                </span>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#controllerNav" aria-controls="controllerNav" aria-expanded="false" aria-label="Toggle navigation">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="controllerNav">
                    <ul class="navbar-nav w-100">
                        {{MENU}}
                    </ul>
                </div>
            </div>
        </nav>
        {{BODY}}
    </body>
</html>
""".replace("{{HEAD}}", head_content)
   .replace("{{MENU}}", menu_content)
   .replace("{{BODY}}", body_content)
   .replace("{{SET}}", set)
   .replace("{{DISPLAY_NAME}}", display_name),
                status_code=HTTP_200_OK
            )
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("Controller draft execution failed: %s", ex)
        error_detail = str(ex)
        return HTMLResponse(
            content=f"""
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
        <link rel="stylesheet" href="/files/css/bootstrap/bootstrap.min.css">
        <link rel="stylesheet" href="/files/css/material/material-icons.css">
        <script src="/files/js/jquery.min.js"></script>
        <script src="/files/js/bootstrap/bootstrap.bundle.min.js"></script>
    </head>
    <body class="bg-light">
        <div class="container mt-5">
            <div class="alert alert-danger" role="alert">
                <h4 class="alert-heading">Controller Draft Error</h4>
                <p>The draft controller for {set}::{name} failed to execute.</p>
                <hr>
                <pre class="mb-0">{error_detail}</pre>
                <p class="mt-3">Please fix the syntax or logic errors in the editor and refresh.</p>
            </div>
        </div>
    </body>
</html>
            """,
            status_code=HTTP_200_OK
        )
    
@app.api_route("/controller/{set}/{name}/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def controller_proxy(set: str, name: str, rest: str, request: Request):
    try:
        glob = {}
        code = await get_controller_code(set, name)
        exec(code, glob)

        user = sanitize_owner(request.headers.get("X-User", "anonymous"))
        kind = "debug" if glob.get("debug", False) else "service"
        owner_filter = user if kind == "debug" else None
        jobs = runner.find(owner_filter, glob["set"], glob["service"], kind, ["running"])
        if not jobs:
            raise Exception(f"No running {kind}: {glob['set']}::{glob['service']}")
        job_id = jobs[0]
        s = pyops.ServiceClient(job_id)

        params = dict(request.query_params)
        params["type"] = request.method
        if "path" not in params:
            params["path"] = f"/{rest}"
        params["user"] = user

        body = await request.body()
        content_type = request.headers.get("content-type", "").lower()
        if body:
            if "application/json" in content_type:
                try:
                    parsed = json.loads(body.decode('utf-8'))
                    params["data"] = parsed
                    params["data_type"] = "json"
                except json.JSONDecodeError:
                    params["data"] = body
                    params["data_type"] = "binary"
            elif "text/" in content_type:
                try:
                    text = body.decode('utf-8')
                    params["data"] = text
                    params["data_type"] = "text"
                except UnicodeDecodeError:
                    params["data"] = body
                    params["data_type"] = "binary"
            else:
                params["data"] = body
                params["data_type"] = "binary"
        else:
            params["data"] = None
            params["data_type"] = "none"

        status, data = s.request(params)
        if status != 200:
            raise HTTPException(status_code=status, detail=data)
        return data
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("Controller proxy failed: %s", ex)
        return {"error": f"Controller execution failed: {str(ex)}"}

@app.get("/menu")
async def menu():
    """
    Generates dynamic menu from Git controllers, preferring draft, collects failures.
    """
    try:
        content = [{
            "id": "scriptlet",
            "name": "Scriptlet",
            "items": [
                {
                    "id": "scriptlet_manager",
                    "name": "Manager",
                    "path": "/scriptlet/manager",
                    "default": False
                },
                {
                    "id": "scriptlet_scheduler",
                    "name": "Scheduler",
                    "path": "/scriptlet/scheduler",
                    "default": False
                },
                {
                    "id": "scriptlet_monitor",
                    "name": "Monitor",
                    "path": "/scriptlet/monitor",
                    "default": True
                },
                {
                    "id": "scriptlet_sets",
                    "name": "Sets",
                    "path": "/scriptlet/sets",
                    "default": False
                },
            ]
        }]
        failures = []
    
        # Scan Git for controllers.
        scriptlets = await run_in_executor(git.list_scriptlets)
        sets = sorted(set(s["set"] for s in scriptlets if s["type"] == "controller"))

        for g in sets:
            m = {
                "id": g,
                "name": g,
                "items": []
            }
            ctrl_scriptlets = [s for s in scriptlets if s["set"] == g and s["type"] == "controller"]
            for s in sorted(ctrl_scriptlets, key=lambda x: x["name"]):
                name = s["name"]
                repo = await run_in_executor(git.get_repo_path, g)
                try:
                    code = await get_controller_code(g, name)
                    glob = {}
                    exec(code, glob)
                    display_name = glob.get("name", name)
                    sm = {
                        "id": f"{g}_{name}",
                        "name": display_name,
                        "path": f"/controller/{g}/{name}",
                        "default": False
                    }
                except FileNotFoundError:
                    sm = {
                        "id": f"{g}_{name}",
                        "name": f"No committed: {name}",
                        "path": f"/controller/{g}/{name}",
                        "default": False
                    }
                except Exception as e:
                    logger.warning(f"Exec failed for draft {g}::{name}: {e}")
                    failures.append({
                        "set": g,
                        "name": name,
                        "error": str(e)
                    })
                    sm = {
                        "id": f"{g}_{name}",
                        "name": f"Draft error: {name}",
                        "path": f"/controller/{g}/{name}",
                        "default": False
                    }
                m["items"].append(sm)
            
            if m["items"]:
                content.append(m)
        
        response = {"menu": content, "failures": failures}
        return response
    except Exception as ex:
        logger.exception("Error in menu: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
