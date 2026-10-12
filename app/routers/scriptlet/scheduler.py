from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK

from app.lib import sites

router = APIRouter()

@router.get("/scriptlet/scheduler")
async def get():
    return HTMLResponse(
        content=sites.get("scriptlet/scheduler"),
        status_code=HTTP_200_OK
    )
