from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK

from orm.database import Base, engine

import routers.scriptlet.inventory
import routers.scriptlet.manager
import routers.scriptlet.editor

from helper import sites, files

# https://fastapi.tiangolo.com/tutorial/bigger-applications/
# https://fastapi.tiangolo.com/tutorial/security/first-steps/

app = FastAPI(docs_url=None)

app.include_router(routers.scriptlet.inventory.router)
app.include_router(routers.scriptlet.manager.router)
app.include_router(routers.scriptlet.editor.router)

Base.metadata.create_all(engine)

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

@app.get("/menu")
async def menu():
    return [{
                "id": "scriptlet",
                "name": "Scriptlet",
                "items": [
                    {
                        "id": "scriptlet_manager",
                        "name": "Manager",
                        "path": "/scriptlet/manager"
                    },
                ]
            }]