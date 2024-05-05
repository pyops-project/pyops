from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_405_METHOD_NOT_ALLOWED, HTTP_500_INTERNAL_SERVER_ERROR

import routers.scriptlet.job
import routers.scriptlet.schedule
import routers.scriptlet.inventory
import routers.scriptlet.manager
import routers.scriptlet.monitor
import routers.scriptlet.scheduler
import routers.scriptlet.editor

from lib import sites, files

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet, ScriptletDraft, ScriptletRevision

app = FastAPI(docs_url=None)

app.include_router(routers.scriptlet.inventory.router)
app.include_router(routers.scriptlet.job.router)
app.include_router(routers.scriptlet.scheduler.router)
app.include_router(routers.scriptlet.schedule.router)
app.include_router(routers.scriptlet.manager.router)
app.include_router(routers.scriptlet.monitor.router)
app.include_router(routers.scriptlet.editor.router)

@app.get("/")
async def root():
    return HTMLResponse(
        content=sites.get("root/root"),
        status_code=HTTP_200_OK
    )

@app.get("/favicon.ico")
async def favicon():
    return HTMLResponse(
        content=files.get("favicon.ico"),
        status_code=HTTP_200_OK
    )

@app.get("/files/{path:path}")
async def file(path: str):
    return HTMLResponse(
        content=files.get(path),
        status_code=HTTP_200_OK
    )

def get_controller(group: str, name: str):
    with Session(engine) as session:
        scriptlet = session.get(Scriptlet, (group, name))
        if scriptlet == None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}::{} not existing".format(group, name))
        elif scriptlet.type != "controller":
            raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail="Scriptlet {}::{} is no controller".format(group, name))

        draft = session.get(ScriptletDraft, (group, name, "TODO"))

        if draft != None:
            # TODO use draft code only when in debug mode
            code = draft.code
        else:
            stmt = select(ScriptletRevision).where(
                and_(
                    ScriptletRevision.group == group,
                    ScriptletRevision.name == name
                )
            ).order_by(ScriptletRevision.revision.desc())
            revision = session.scalar(stmt)
            
            if revision != None:
                code = revision.code
            else:
                raise HTTPException(status_code=HTTP_405_METHOD_NOT_ALLOWED, detail="Scriptlet {}::{} has no valid controller code".format(group, name))
            
        return code

@app.get("/controller/{group}/{name}")
async def controller(group: str, name: str):
    try:
            glob = {}
            exec(get_controller(group, name), glob)

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

        {HEAD}
    </head>
    <body class="bg-light">
        <nav class="navbar navbar-expand navbar-dark bg-dark pt-3">
            <ul class="navbar-nav w-100">
                <li class="navbar-brand">
                    <span class="material-icons-round">description</span>
                    <span>Controller {GROUP}::{NAME}</span>
                </li>
                
                {MENU}
            </ul>
        </nav>
        {BODY}
    </body>
</html>
""".replace(r"{GROUP}", group)
   .replace(r"{NAME}", name)
   .replace(r"{HEAD}", glob["head"])
   .replace(r"{MENU}", glob["menu"])
   .replace(r"{BODY}", glob["body"]),
                status_code=HTTP_200_OK
            )

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.get("/controller/{group}/{name}/q")
async def controller_get(group: str, name: str, request: Request):
    try:
        params = dict(request.query_params)
        
        glob = {}
        exec(get_controller(group, name), glob)

        return glob["get"](params)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.delete("/controller/{group}/{name}/q")
async def controller_delete(group: str, name: str, request: Request):
    try:
        json = await request.json() if await request.body() else None
        params = dict(request.query_params)
        
        glob = {}
        exec(get_controller(group, name), glob)

        return glob["delete"](params, json)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.put("/controller/{group}/{name}/q")
async def controller_put(group: str, name: str, request: Request):
    try:
        json = await request.json() if await request.body() else None
        params = dict(request.query_params)
        
        glob = {}
        exec(get_controller(group, name), glob)

        return glob["put"](params, json)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.post("/controller/{group}/{name}/q")
async def controller_post(group: str, name: str, request: Request):
    try:
        json = await request.json() if await request.body() else None
        params = dict(request.query_params)
        
        glob = {}
        exec(get_controller(group, name), glob)

        return glob["post"](params, json)

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@app.get("/menu")
async def menu():
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
    
        with Session(engine) as session:
            stmt = select(Scriptlet.group).where(Scriptlet.type == "controller").distinct().order_by(Scriptlet.group.asc())
            groups = [r._mapping.group for r in session.execute(stmt)]

            for g in groups:
                m = {
                    "id": g,
                    "name": g,
                    "items": []
                }

                stmt = select(Scriptlet.name).where(Scriptlet.group == g, Scriptlet.type == "controller").distinct().order_by(Scriptlet.group.asc())
                ctrls = [r._mapping.name for r in session.execute(stmt)]

                for c in ctrls:
                    sm = {
                        "id": f"{g}_{c}",
                        "name": c,
                        "path": f"/controller/{g}/{c}",
                        "default": False
                    }
                    m["items"].append(sm)

                content.append(m)
        
        return content
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))