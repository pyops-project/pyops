import os, shutil
from glob import glob
from datetime import datetime

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.job import Job

from helper import files

def enqueue(group: str, name: str, owner: str, kind: str, drop_done: bool, param: str):
    with Session(engine) as session:
        job = Job(
            id = "{}_{}_{}_{}".format(group, name, datetime.now(), owner),
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
        job = session.get(Job, id)

        if job != None:
            return {"state": job.state, "detail": job.detail}
        else:
            return None

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

def orphans():
    #TODO get orphaned data
    """
    Job.id,
    Job.kind,
    Job.state,
    Job.detail,
    Job.param
    """

    findings = []

    log_base = files.conf_log()["jobs"]

    for f in glob("{}/*.out.log".format(log_base)):
        id = os.path.basename(f)[:-8]
        j = query(id)
        if not j or j["state"] =="remove":
            findings.append({
                "id": id,
                "kind": "data",
                "state": "data",
                "detail": "orphaned data",
                "param": ""
            })

    return findings

def drop(id: str):
    log_base = files.conf_log()["jobs"]
    if os.path.exists(os.path.join(log_base, "{}.out.log".format(id))):
        os.remove(os.path.join(log_base, "{}.out.log".format(id)))
        os.remove(os.path.join(log_base, "{}.err.log".format(id)))

        data_base = files.conf_data()["jobs"]
        if os.path.exists(os.path.join(data_base, id)):
            shutil.rmtree(os.path.join(data_base, id))

        return True
    else:
        return False