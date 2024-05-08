import signal, time, os, sys, re
import traceback
import croniter
from datetime import datetime
from multiprocessing import Process, current_process, Queue

from sqlalchemy import null, select, delete, and_, or_
from sqlalchemy.orm import Session

from orm.database import engine
from orm.scriptlet import Scriptlet, ScriptletDraft, ScriptletRevision
from orm.job import Job
from orm.schedule import Schedule

from lib import runner, infraops

class GracefulKiller:
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        self.kill_now = True

def handleOddJobs():
    with Session(engine) as session:
        stmt = select(Job.id).where(
            or_(Job.state == "dequeued", Job.state == "running")
        ).order_by(Job.id.asc())

        jobs = session.execute(stmt)

        for j in jobs:
            job = session.get(Job, j)
            if job.state == "dequeued":
                job.state = "todo"
            else:
                job.state = "failed"
                job.detail = "incomplete execution detected on startup"
        session.commit()

def getNextToDo():
    with Session(engine) as session:
        stmt = select(Job).where(
            Job.state == "todo"
        ).order_by(Job.id.asc())

        job = session.scalar(stmt)

        if job != None:
            job.state = "dequeued"
            session.commit()

            return job.id
        else:
            return None

def assignJob(id: str, pid: int):
    print(f"assignJob(id='{id}' pid={pid})")

    with Session(engine) as session:
        job = session.get(Job, id)
        job.pid = pid
        session.commit()

def loadLib(group: str, name: str, debug: bool):
    print(f"loadLib(group={group}, name={name}, debug={debug})")
    with Session(engine) as session:
        scriptlet = session.get(Scriptlet, (group, name))
        if scriptlet == None:
            raise Exception(f"Scriptlet {group}::{name} not existing")
        else:
            if debug:
                draft = session.get(ScriptletDraft, (group, name, "TODO"))
            else:
                draft = None

            if draft != None:
                code = draft.code
            else:
                stmt = select(ScriptletRevision).where(
                    and_(
                        ScriptletRevision.group == group,
                        ScriptletRevision.name == name
                    )
                ).order_by(ScriptletRevision.revision.desc())
                revision = session.scalar(stmt)

                if revision != None:
                    code = revision.code
                else:
                    code = ""

        return code

def loadJob(id: str):
    print(f"loadJob(id={id})")

    with Session(engine) as session:
        job = session.get(Job, id)
        debug = job.kind == "debug"
        scriptlet = session.get(Scriptlet, (job.group, job.name))
        if scriptlet == None:
            raise Exception(f"Scriptlet {job.group}::{job.name} not existing")
        else:
            if job.kind == "debug":
                draft = session.get(ScriptletDraft, (job.group, job.name, "TODO"))
            else:
                draft = None

            if draft != None:
                code = draft.code
            else:
                stmt = select(ScriptletRevision).where(
                    and_(
                        ScriptletRevision.group == job.group,
                        ScriptletRevision.name == job.name
                    )
                ).order_by(ScriptletRevision.revision.desc())
                revision = session.scalar(stmt)

                if revision != None:
                    code = revision.code
                else:
                    raise Exception("nothing to do")

            job.state = "running"
            job.start = datetime.now()

            session.commit()

        return job.owner, job.group, job.name, job.kind, job.param, code, debug

def finishJob(id: str, state: str, detail: str):
    print(f"finishJob(id='{id}', state='{state}', detail='{detail}')")

    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            if job.state == "running" and state == "done" and job.drop_done and not job.kind == "debug":
                job.state = "remove"
            else:
                job.state = state
            job.end = datetime.now()
            job.detail = detail
            job.pid = None
            session.commit()

def disposeJobs():
    with Session(engine) as session:
        stmt = delete(Job).where(Job.state == "removed")
        session.execute(stmt)

        session.commit()

def run(id: str, owner: str, group: str, name: str, kind: str, param: str, code: str, libs: list[str], q: Queue, data_base: str, web_bind: str):
    userspace = os.path.join(data_base, f"{owner}")
    groupspace = os.path.join(userspace, f"{group}")
    workspace = os.path.join(groupspace, f"{id}")

    if not os.path.exists(userspace):
        os.makedirs(userspace)

    if not os.path.exists(groupspace):
        os.makedirs(groupspace)

    if not os.path.exists(workspace):
        os.makedirs(workspace)

    print(f"{current_process().pid:<10}start({id})")
    with open(os.path.join(workspace, "stdout.log"), 'w', 1) as sys.stdout:
        with open(os.path.join(workspace, "stderr.log"), 'w', 1) as sys.stderr:
            try:
                if kind == "service" or kind == "debug":
                    listener = infraops.ServiceListener(os.path.join(data_base, f"{id}.sock"))
                else:
                    listener = None

                glob = {
                    "userspace": userspace,
                    "groupspace": groupspace,
                    "workspace": workspace,
                    "control": infraops.Control(web_bind),
                    "listener" : listener
                }
                    
                exec(param, glob)
                for l in libs:
                    exec(l, glob)
                exec(code, glob)

                q.put({"id": id, "state": "done", "detail": None})
            except Exception:
                q.put({"id": id, "state": "failed", "detail": traceback.format_exc()})
            
    print(f"{current_process().pid:<10}end({id})")

def cleanProcesses(processes: dict[int, Process]):
    cleaned_processes: dict[int, Process] = {}
    for pid in processes:
        p = processes[pid]
        if p.is_alive:
            cleaned_processes[pid] = p

    return cleaned_processes

def getNextToTerm():
    with Session(engine) as session:
        stmt = select(Job).where(
            or_(Job.state == "cancel", Job.state == "remove")
        )

        job = session.scalar(stmt)
        return {"id": job.id, "pid": job.pid} if job != None else None

def term(job, processes):
    print(f"term({job})")
    processes[job["pid"]].terminate()

    postTerm(job["id"])

def postTerm(id: str):
    print(f"postTerm({id})")

    with Session(engine) as session:
        job = session.get(Job, id)
        if job != None:
            if job.state == "remove":
                session.delete(job)
                runner.drop(id, job.owner, job.group)
            else:
                job.end = datetime.now()
                job.state = "canceled"
            session.commit()

def schedule():
    now = datetime.now()

    with Session(engine) as session:
        stmt = select(Schedule.id,
                      Schedule.group,
                      Schedule.name,
                      Schedule.owner,
                      Schedule.active,
                      Schedule.drop_done,
                      Schedule.schedule,
                      Schedule.param,
                      Schedule.last)
        schedules = session.execute(stmt)

        for s in schedules:
            if s.active:
                if s.schedule:
                    cron = croniter.croniter(s.schedule, now)
                    prev = cron.get_prev(datetime)
                    if not s.last or prev > s.last:
                        runner.enqueue(s.group, s.name, s.owner, "task", s.drop_done, s.param)

                        schedule = session.get(Schedule, s.id)
                        schedule.last = prev
                        session.commit()
                elif not runner.find(s.owner, s.group, s.name, "service", ["todo", "dequeued", "running", "cancel", "canceled", "done"]) and \
                     len(runner.find(s.owner, s.group, s.name, "service", ["failed"])) < 3:
                    runner.enqueue(s.group, s.name, s.owner, "service", s.drop_done, s.param)
                    schedule = session.get(Schedule, s.id)
                    schedule.last = now
                    session.commit()

def main(data_base: str, web_bind: str):
    killer = GracefulKiller()
    
    q = Queue()

    # restore jobs which were taken out of queue but not executed due to crash
    handleOddJobs()

    processes: dict[int, Process] = {}

    while not killer.kill_now:
        # dispose already remove jobs
        disposeJobs()

        # remove canceled processes from list
        processes = cleanProcesses(processes)

        # terminate "remove" or "cancel" jobs
        try:
            job = getNextToTerm()
            
            if job != None:
                if job["pid"] in processes:
                    print(f"Trying to term job {job}")
                    term(job, processes)
                else:
                    postTerm(job["id"])
        except Exception:
            print(traceback.format_exc())

        # handle jobs "failed" or "done"
        while not q.empty():
            msg = q.get()
            finishJob(msg["id"], msg["state"], msg["detail"])

        try:
            job = getNextToDo()

            if job != None:
                try:
                    owner, group, name, kind, param, code, debug = loadJob(job)

                    libs = []

                    for lm in re.finditer(r'!\{([a-zA-Z0-9_-]+)::([a-zA-Z0-9_-]+)\}', code):
                        libs.append(loadLib(lm.group(1), lm.group(2), debug))
                    code = re.sub(r'\!{([a-zA-Z0-9_-]+)::([a-zA-Z0-9_-]+)\}', r'# importing \1::\2', code)

                    print(f"Run job {job}")
                    p = Process(target=run, kwargs={"id": job, "owner": owner, "group": group, "name": name, "kind": kind, "param": param, "code": code, "libs": libs, "q": q, "data_base": data_base, "web_bind": web_bind})
                    p.start()
                    assignJob(job, p.pid)
                    processes[p.pid] = p
                except Exception:
                    finishJob(job, "failed", traceback.format_exc())
            else:
                schedule()
                time.sleep(0.1)
        except Exception:
            print(traceback.format_exc())

    # join running processes for graceful shutdown
    for p in processes:
        print(f"Terminating {p}")
        if processes[p].is_alive:
            processes[p].terminate()

    for p in processes:
        print(f"Waiting 3 seconds for {p} to shut down")
        if processes[p].is_alive:
            processes[p].join(timeout=3)

    for p in processes:
        print(f"Killing for {p}")
        if processes[p].is_alive:
            processes[p].kill()

    # handle finished or failed jobs
    while not q.empty():
        msg = q.get()
        finishJob(msg["id"], msg["state"], msg["detail"])

    # dispose already remove jobs
    disposeJobs()

    print("runner exited")