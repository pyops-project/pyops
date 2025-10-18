from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR

from app.lib import sites

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scriptlet/manager")
async def get():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/manager"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        logger.exception("Error in get manager: %s", ex)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
