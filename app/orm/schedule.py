from sqlalchemy import Column, Integer, String, DateTime, Boolean, text

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
    max_retries = Column(Integer, default=0, nullable=False)
    keep_n = Column(Integer, nullable=False, default=0, server_default=text("0"))
    max_fail_n = Column(Integer, nullable=False, default=3, server_default=text("3"))
    param = Column(String)
    last = Column(DateTime, index=True)
