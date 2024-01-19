from sqlalchemy import Boolean, Column, ForeignKey, Float, String
from sqlalchemy.orm import relationship

from orm.database import Base

class Scriptlet(Base):
    __tablename__ = "scriptlet"
    group = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    description = Column(String)
    type = Column(String, index=True)

class ScriptletRevision(Base):
    __tablename__ = "scriptlet_revision"
    group = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    revision = Column(Float, primary_key=True)
    owner = Column(String, index=True)
    code = Column(String)

class ScriptletDraft(Base):
    __tablename__ = "scriptlet_draft"
    group = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    owner = Column(String, primary_key=True)
    code = Column(String)