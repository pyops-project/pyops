from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK

from app.lib import sites

router = APIRouter()

@router.get("/scriptlet/monitor")
async def get():
    return HTMLResponse(
        content=sites.get("scriptlet/monitor"),
        status_code=HTTP_200_OK
    )
