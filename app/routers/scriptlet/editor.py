from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session

from helper import sites, files

router = APIRouter()

@router.get("/scriptlet/editor")
async def get():
    try:
        return HTMLResponse(
            content=sites.get("scriptlet/editor"),
            status_code=HTTP_200_OK
        )
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))