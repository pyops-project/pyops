from sqlalchemy import Column, Integer, String, DateTime, Boolean

from .database import Base

class Schedule(Base):
    __tablename__ = "schedule"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    schedule = Column(String, index=True, nullable=False)
    set = Column(String, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    owner = Column(String, index=True, nullable=False)
    active = Column(Boolean, index=True, nullable=False)
    drop_done = Column(Boolean, default=True, nullable=False)
    param = Column(String)
    last = Column(DateTime, index=True)
