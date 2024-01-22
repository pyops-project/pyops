from sqlalchemy import Column, Integer, String, DateTime

from orm.database import Base

class Job(Base):
    __tablename__ = "job"
    id = Column(String, primary_key=True)
    group = Column(String, index=True)
    name = Column(String, index=True)
    owner = Column(String, index=True)
    kind = Column(String, index=True)
    state = Column(String, index=True)
    param = Column(String)
    pid = Column(Integer, index=True)
    detail = Column(String)
    start = Column(DateTime, index=True)
    end = Column(DateTime, index=True)

class Schedule(Base):
    __tablename__ = "schedule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule = Column(String, index=True)
    group = Column(String, index=True)
    name = Column(String, index=True)
    owner = Column(String, index=True)
    param = Column(String)