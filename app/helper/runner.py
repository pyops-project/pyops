from datetime import datetime

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.job import Job

def enqueue(group: str, name: str, owner: str, kind: str, param: str):
    with Session(engine) as session:
        job = Job(
            id = "{}_{}_{}_{}".format(group, name, datetime.now(), owner),
            group = group,
            name = name,
            owner = owner,
            kind = kind,
            state = "todo",
            param = param
        )

        session.add(job)
        session.commit()

        return job.id

def query(id: str):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            return {"state": job.state, "detail": job.detail}
        else:
            return None

def remove(id: str):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "remove"
            session.commit()

        return job

def cancel(id: str):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "cancel"
            session.commit()

        return job