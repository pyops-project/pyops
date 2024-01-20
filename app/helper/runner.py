from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.job import Job

def enqueue(group: str, name: str, owner: str, kind: str, param: str):
    with Session(engine) as session:
        job = Job(
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

def query(id: int):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            return {"state": job.state, "result": job.result}
        else:
            return None

def remove(id: int):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "remove"
            session.commit()

        return job

def cancel(id: int):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "cancel"
            session.commit()

        return job