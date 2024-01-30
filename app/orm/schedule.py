from sqlalchemy import Column, Integer, String, DateTime

from orm.database import Base

class Schedule(Base):
    __tablename__ = "schedule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule = Column(String, index=True)
    group = Column(String, index=True)
    name = Column(String, index=True)
    owner = Column(String, index=True)
    param = Column(String)
    last = Column(DateTime, index=True)