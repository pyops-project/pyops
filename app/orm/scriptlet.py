from sqlalchemy import Column, String, DateTime

from orm.database import Base

class Scriptlet(Base):
    __tablename__ = "scriptlet"
    group = Column(String, primary_key=True, nullable=False)
    name = Column(String, primary_key=True, nullable=False)
    description = Column(String)
    type = Column(String, index=True, nullable=False)

class ScriptletRevision(Base):
    __tablename__ = "scriptlet_revision"
    group = Column(String, primary_key=True, nullable=False)
    name = Column(String, primary_key=True, nullable=False)
    revision = Column(DateTime, primary_key=True, nullable=False)
    owner = Column(String, index=True, nullable=False)
    code = Column(String)

class ScriptletDraft(Base):
    __tablename__ = "scriptlet_draft"
    group = Column(String, primary_key=True, nullable=False)
    name = Column(String, primary_key=True, nullable=False)
    owner = Column(String, primary_key=True, nullable=False)
    code = Column(String)