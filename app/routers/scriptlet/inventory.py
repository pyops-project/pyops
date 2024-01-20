from datetime import datetime

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel, create_model

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet, ScriptletDraft, ScriptletRevision

router = APIRouter()

class CreateArgument(BaseModel):
    group: str
    name: str
    type: str

class SaveArgument(BaseModel):
    group: str
    name: str
    code: str

@router.get("/scriptlet/inventory/list", tags=["scriptlet"])
async def list_get():
    """
    Returns a list of available Scriptlets
    """
    try:
        with Session(engine) as session:
            stmt = select(Scriptlet.group,
                          Scriptlet.name,
                          Scriptlet.description,
                          Scriptlet.type)
            scriptlets = session.execute(stmt)
            
            return [dict(r._mapping) for r in scriptlets]
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.put("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_put(cr: CreateArgument):
    """
    Creates a new Scriptlet
    """
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (cr.group, cr.name))
            
            if scriptlet != None:
                raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Scriptlet {}/{} already existing".format(cr.group, cr.name))

            scriptlet = Scriptlet(
                group       = cr.group,
                name        = cr.name,
                description = "",
                type        = cr.type
            )

            session.add(scriptlet)
            session.commit()
        
        return {"result": "OK"}
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
    
@router.post("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_post(sa: SaveArgument):
    """
    Updates a Scriptlet's code
    """
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (sa.group, sa.name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}/{} not existing".format(sa.group, sa.name))

            revision = ScriptletRevision(
                group       = sa.group,
                name        = sa.name,
                revision    = datetime.now(),
                owner       = "TODO",
                code        = sa.code
            )
            session.add(revision)
            #scriptlet.code = sa.code

            draft = session.get(ScriptletDraft, (sa.group, sa.name, "TODO"))
            session.delete(draft)

            session.commit()

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.delete("/scriptlet/inventory/item", tags=["scriptlet"])
async def item_del(group: str, name: str):
    """
    Deletes a Scriptlet
    """
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (group, name))
            draft = session.get(ScriptletDraft, (group, name, "TODO"))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}/{} not existing".format(group, name))
            
            session.delete(scriptlet)
            if draft != None:
                session.delete(draft)
            session.commit()

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))

@router.post("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_post(sa: SaveArgument):
    """
    Creates or Updates a Scriptlet's draft code
    """
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (sa.group, sa.name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}/{} not existing".format(sa.group, sa.name))
            
            draft = session.get(ScriptletDraft, (sa.group, sa.name, "TODO"))
            if draft == None:
                draft = ScriptletDraft(
                    group       = sa.group,
                    name        = sa.name,
                    owner     = "TODO"
                )
                session.add(draft)

            draft.code = sa.code
            session.commit()
        
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.delete("/scriptlet/inventory/draft", tags=["scriptlet"])
async def draft_del(group: str, name: str):
    """
    Deletes a Scriptlet's draft
    """
    try:
        with Session(engine) as session:
            draft = session.get(ScriptletDraft, (group, name, "TODO"))
            session.delete(draft)
            session.commit()
        
    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))
    
@router.get("/scriptlet/inventory/code", tags=["scriptlet"])
async def code_get(group: str, name: str):
    try:
        with Session(engine) as session:
            scriptlet = session.get(Scriptlet, (group, name))
            if scriptlet == None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Scriptlet {}/{} not existing".format(group, name))

            draft = session.get(ScriptletDraft, (group, name, "TODO"))

            if draft != None:
                return {"code": draft.code}
            else:
                stmt = select(ScriptletRevision).where(
                    and_(
                        ScriptletRevision.group == group,
                        ScriptletRevision.name == name
                    )
                ).order_by(ScriptletRevision.revision.desc())
                revision = session.scalar(stmt)
                
                if revision != None:
                    return {"code": revision.code}
                else:
                    return {"code": ""}

    except HTTPException as ex:
        raise ex
    except Exception as ex:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail= str(ex))