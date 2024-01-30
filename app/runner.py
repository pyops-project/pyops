import signal, time, os, sys, re, json
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

from helper import runner

class GracefulKiller:
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        self.kill_now = True

def restoreJobs():
    # TODO update all where state == "dequeued" to state = "todo"
    return

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
    print("assignJob(id={})".format(id))

    with Session(engine) as session:
        job = session.get(Job, id)
        job.pid = pid
        session.commit()

def loadLib(group: str, name: str, debug: bool):
    print("loadLib(group={}, name={}, debug={})".format(group, name, debug))
    with Session(engine) as session:
        scriptlet = session.get(Scriptlet, (group, name))
        if scriptlet == None:
            raise Exception("Scriptlet {}/{} not existing".format(group, name))
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
    print("loadJob(id={})".format(id))

    with Session(engine) as session:
        job = session.get(Job, id)
        debug = job.kind == "debug"
        scriptlet = session.get(Scriptlet, (job.group, job.name))
        if scriptlet == None:
            raise Exception("Scriptlet {}/{} not existing".format(job.group, job.name))
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

        return job.param, code, debug

def finishJob(id: str, state: str, detail: str):
    print("finishJob(id={})".format(id))

    with Session(engine) as session:
        job = session.get(Job, id)

        if job != None:
            # only set new state if old is "running"
            if job.state == "running":
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

def run(id: str, param: str, code: str, libs: list[str], q: Queue, log_base: str):
    print("{:<10}start({})".format(current_process().pid, id))
    with open(os.path.join(log_base, "{}.out.log".format(id)), 'w', 1) as sys.stdout:
        with open(os.path.join(log_base, "{}.err.log".format(id)), 'w', 1) as sys.stderr:
            try:
                # TODO !!! python virtual environment

                glob = {}
                exec(param, glob)
                for l in libs:
                    exec(l, glob)
                exec(code, glob)

                q.put({"id": id, "state": "done", "detail": None})
            except Exception:
                q.put({"id": id, "state": "failed", "detail": traceback.format_exc()})
            
    print("{:<10}end({})".format(current_process().pid, id))

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
    print("term({})".format(job))
    processes[job["pid"]].terminate()

    postTerm(job["id"])

def postTerm(id: str):
    print("postTerm({})".format(id))

    with Session(engine) as session:
        job = session.get(Job, id)
        if job != None:
            if job.state == "remove":
                session.delete(job)
            else:
                job.state = "canceled"
            session.commit()

def schedule():
    now = datetime.now()

    with Session(engine) as session:
        stmt = select(Schedule.id,
                        Schedule.group,
                        Schedule.name,
                        Schedule.schedule,
                        Schedule.param,
                        Schedule.last)
        schedules = session.execute(stmt)

        for s in schedules:
            cron = croniter.croniter(s.schedule, now)
            prev = cron.get_prev(datetime)
            if not s.last or prev > s.last:
                runner.enqueue(s.group, s.name, "TODO", "task", s.param)

                schedule = session.get(Schedule, s.id)
                schedule.last = prev
                session.commit()

def main(log_base):
    killer = GracefulKiller()
    
    q = Queue()

    # restore jobs which were taken out of queue but not executed due to crash
    restoreJobs()

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
                    print("Trying to term job {}".format(job))
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
                    param, code, debug = loadJob(job)

                    libs = []

                    for lm in re.finditer(r'!\{([a-zA-Z0-9_]+)::([a-zA-Z0-9_]+)\}', code):
                        libs.append(loadLib(lm.group(1), lm.group(2), debug))
                    code = re.sub(r'\!{([a-zA-Z0-9_]+)::([a-zA-Z0-9_]+)\}', r'# importing \1::\2', code)

                    print("Run job {}".format(job))
                    p = Process(target=run, kwargs={"id": job, "param": param, "code": code, "libs": libs, "q": q, "log_base": log_base})
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
        print("Waiting for {}".format(p))
        if processes[p].is_alive:
            processes[p].join()

    # handle finished or failed jobs
    while not q.empty():
        msg = q.get()
        finishJob(msg["id"], msg["state"], msg["detail"])

    # dispose already remove jobs
    disposeJobs()

    print("runner exited")