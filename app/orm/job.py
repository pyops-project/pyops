from sqlalchemy import Column, Integer, String, DateTime, Boolean

from .database import Base

class Job(Base):
    __tablename__ = "job"
    id = Column(String, primary_key=True, nullable=False)
    set = Column(String, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    owner = Column(String, index=True, nullable=False)
    kind = Column(String, index=True, nullable=False)
    state = Column(String, index=True, nullable=False)
    drop_done = Column(Boolean, default=False, nullable=False)
    param = Column(String)
    pid = Column(Integer, index=True)
    detail = Column(String)
    start = Column(DateTime, index=True)
    end = Column(DateTime, index=True)
