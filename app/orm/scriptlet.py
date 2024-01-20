from sqlalchemy import Column, String, DateTime

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
    revision = Column(DateTime, primary_key=True)
    owner = Column(String, index=True)
    code = Column(String)

class ScriptletDraft(Base):
    __tablename__ = "scriptlet_draft"
    group = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    owner = Column(String, primary_key=True)
    code = Column(String)