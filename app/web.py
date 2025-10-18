import asyncio, traceback
import os
import subprocess
import logging
import json

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_405_METHOD_NOT_ALLOWED, HTTP_500_INTERNAL_SERVER_ERROR

from .routers.scriptlet import job
from .routers.scriptlet import schedule
from .routers.scriptlet import inventory
from .routers.scriptlet import manager
from .routers.scriptlet import monitor
from .routers.scriptlet import scheduler
from .routers.scriptlet import editor

from .lib import sites, files, pyops, git

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from .orm.database import init_db
from .orm.schedule import Schedule
from .orm.job import Job

from pathlib import Path
from app.lib.files import Config

if __name__ == "__main__":
    # Set default config path and initialize
    default_path = str(Path(__file__).parent.parent / "etc" / "pyops.json")
    Config.init(default_path)

    # Initialize tables on module import
    init_db()

    logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None)

app.include_router(inventory.router)
app.include_router(job.router)
app.include_router(scheduler.router)
app.include_router(schedule.router)
app.include_router(manager.router)
app.include_router(monitor.router)
app.include_router(editor.router)

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
        return HTMLResponse(
            content=files.get("favicon.ico"),
            status_code=HTTP_200_OK
        )
    except Exception as ex:
        logger.exception("Error in favicon: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@app.get("/files/{path:path}")
async def file(path: str):
    try:
        return HTMLResponse(
            content=files.get(path),
            status_code=HTTP_200_OK
        )
    except Exception as ex:
        logger.exception("Error in file: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

def get_controller_code(set: str, name: str) -> str:
    """Loads committed controller code from Git.
    
    Args:
        set (str): Set.
        name (str): Controller name.
    
    Returns:
        str: Executable Python code.
    
    Raises:
        HTTPException: 404 if not found, 405 if wrong type or no committed code.
    """
    repo = git.get_repo_path(set)
    # Check exists and right type.
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    inferred_type = git.infer_type_from_filename(filename)
    if inferred_type != "controller":
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} is not a controller.")
    # Strict committed check.
    try:
        return git.get_committed_content(repo, name, inferred_type)
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} has no committed controller code.")

def get_view_content(set: str, name: str) -> str:  # Renamed/Updated: Raw HTML, no exec
    """Loads committed view content from Git (.html).
    
    Args:
        set (str): Set.
        name (str): View name.
    
    Returns:
        str: Raw HTML content.
    
    Raises:
        HTTPException: 404 if not found, 405 if wrong type or no committed content.
    """
    repo = git.get_repo_path(set)
    # Check exists and right type.
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.html')]
    if not files_list:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    inferred_type = git.infer_type_from_filename(filename)
    if inferred_type != "view":
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} is not a view.")
    # Strict committed check.
    try:
        return git.get_committed_content(repo, name, inferred_type)
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail=f"Scriptlet {set}::{name} has no committed view content.")

@app.get("/controller/{set}/{name}")
def controller(set: str, name: str):
    try:
        glob = {}
        exec(get_controller_code(set, name), glob)

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
        <nav class="navbar navbar-expand navbar-dark bg-dark text-light pt-3">
            <ul class="navbar-nav w-100">
                <li class="navbar-brand">
                    <span class="material-icons-round">description</span>
                    <span>Controller {{SET}}::{{NAME}}</span>
                </li>
                
                {{MENU}}
            </ul>
        </nav>
        {{BODY}}
    </body>
</html>
""".replace(r"{{HEAD}}", get_view_content(set, glob["head"]))
   .replace(r"{{MENU}}", get_view_content(set, glob["menu"]))
   .replace(r"{{BODY}}", get_view_content(set, glob["body"]))
   .replace(r"{{SET}}", set)
   .replace(r"{{NAME}}", name),
                status_code=HTTP_200_OK
            )

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in controller: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.get("/controller/{set}/{name}/q")
def controller_get(set: str, name: str, request: Request):
    try:
        params = dict(request.query_params)
        params["type"] = "GET"
        
        glob = {}
        exec(get_controller_code(set, name), glob)

        user = request.headers.get("X-User", "anonymous")
        s = pyops.ServiceClient(glob["set"], glob["service"], debug=glob["debug"], owner=user)
        return s.request(params)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in controller_get: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(traceback.format_exc()))
    
@app.delete("/controller/{set}/{name}/q")
async def controller_delete(set: str, name: str, request: Request):
    try:
        body = await request.body()
        content_type = request.headers.get("content-type", "").lower()
        params = dict(request.query_params)
        params["type"] = "DELETE"
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
        
        glob = {}
        exec(get_controller_code(set, name), glob)

        user = request.headers.get("X-User", "anonymous")
        s = pyops.ServiceClient(glob["set"], glob["service"], debug=glob["debug"], owner=user)
        return s.request(params)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in controller_delete: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(traceback.format_exc()))
    
@app.put("/controller/{set}/{name}/q")
async def controller_put(set: str, name: str, request: Request):
    try:
        body = await request.body()
        content_type = request.headers.get("content-type", "").lower()
        params = dict(request.query_params)
        params["type"] = "PUT"
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
        
        glob = {}
        exec(get_controller_code(set, name), glob)

        user = request.headers.get("X-User", "anonymous")
        s = pyops.ServiceClient(glob["set"], glob["service"], debug=glob["debug"], owner=user)
        return s.request(params)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in controller_put: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(traceback.format_exc()))
    
@app.post("/controller/{set}/{name}/q")
async def controller_post(set: str, name: str, request: Request):
    try:
        body = await request.body()
        content_type = request.headers.get("content-type", "").lower()
        params = dict(request.query_params)
        params["type"] = "POST"
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
        
        glob = {}
        exec(get_controller_code(set, name), glob)

        user = request.headers.get("X-User", "anonymous")
        s = pyops.ServiceClient(glob["set"], glob["service"], debug=glob["debug"], owner=user)
        return s.request(params)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in controller_post: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(traceback.format_exc()))
    
@app.get("/menu")
async def menu():
    """
    Generates dynamic menu from Git controllers.
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
            ]
        }]
    
        # Scan Git for controllers.
        scriptlets = git.list_scriptlets()
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
                repo = git.get_repo_path(g)
                try:
                    code = git.get_committed_content(repo, name, "controller")
                    glob = {}
                    exec(code, glob)
                    sm = {
                        "id": f"{g}_{name}",
                        "name": glob["name"] if "name" in glob and glob["name"] else name,
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
                except Exception:
                    sm = {
                        "id": f"{g}_{name}",
                        "name": f"Exec failed: {name}",
                        "path": f"/controller/{g}/{name}",
                        "default": False
                    }
                m["items"].append(sm)
            
            if m["items"]:
                content.append(m)
        
        return content
    except Exception as ex:
        logger.exception("Error in menu: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
