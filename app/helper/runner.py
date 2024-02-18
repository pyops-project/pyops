import os, shutil
from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.job import Job

from helper import files

def enqueue(group: str, name: str, owner: str, kind: str, drop_done: bool, param: str):
    with Session(engine) as session:
        job = Job(
            id = "{}_{}_{}_{}".format(owner, group, name, datetime.now().strftime("%Y%m%dT%H%M%S%f")),
            group = group,
            name = name,
            owner = owner,
            drop_done = drop_done,
            kind = kind,
            state = "todo",
            param = param
        )

        session.add(job)
        session.commit()

        return job.id

def query(id: str):
    with Session(engine) as session:
        return session.get(Job, id)

def find(owner: str, group: str, name: str, kind: str, states: list[str]):
    with Session(engine) as session:
        stmt = select(Job.id).where(
            Job.owner == owner,
            Job.group == group,
            Job.name == name,
            Job.kind == kind,
            Job.state.in_(states)
        ).order_by(Job.id.desc())
        return [j for j in session.execute(stmt)]

def cancel(id: str):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "cancel"
            session.commit()

        return job

def remove(id: str):
    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            job.state = "remove"
            session.commit()

        return job

def drop(id: str, owner: str, group: str):
    data_base = files.conf_data()["jobs"]
    userspace = os.path.join(data_base, f"{owner}")
    groupspace = os.path.join(userspace, f"{group}")
    workspace = os.path.join(groupspace, f"{id}")

    if os.path.exists(workspace):
        shutil.rmtree(workspace)

        if len(os.listdir(groupspace)) == 0:
            shutil.rmtree(groupspace)

        if len(os.listdir(userspace)) == 0:
            shutil.rmtree(userspace)

        return True
    else:
        return False