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
    max_retries = Column(Integer, default=0, nullable=False)
    param = Column(String)
    pid = Column(Integer, index=True)
    detail = Column(String)
    overrides = Column(String, default=None)
    start = Column(DateTime, index=True)
    end = Column(DateTime, index=True)
    schedule_id = Column(Integer, index=True)
    # When the runner started terminating the job (cancel or shutdown);
    # NULL while it has not. A completion the job reports decides the state
    # only if the job finished before this time (see runner.finishJob).
    term_start = Column(DateTime)
