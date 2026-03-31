import signal, time, os, sys, re
import traceback
import croniter
import subprocess
import json
from datetime import datetime, timedelta
from io import TextIOWrapper
import threading
from typing import Any

import zmq, msgpack
import psutil

from sqlalchemy import null, select, delete, and_, or_
from sqlalchemy.orm import Session
import sqlalchemy.exc

from app.orm.database import get_db_engine
from app.orm.job import Job
from app.orm.schedule import Schedule

from app.lib import runner as lib_runner, files
# Import Git helpers for code loading.
from app.lib import git

def commit_with_retry(session, max_retries=5, backoff=0.1):
    for attempt in range(max_retries):
        try:
            session.commit()
            return
        except sqlalchemy.exc.OperationalError as e:
            if 'database is locked' in str(e).lower():
                if attempt < max_retries - 1:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                else:
                    raise
            else:
                raise
        time.sleep(1.0)

from app.lib import pyops
from app.lib.sets import ensure_set_environment
from app.lib import job as job_lib

import logging
from contextlib import redirect_stdout, redirect_stderr
logger = logging.getLogger(__name__)

class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        self.kill_now = True

def terminate_job_processes(job_id: str, pid: int, is_debug: bool, owner: str, graceful_timeout: float = 15.0):
    logger.info("ENTER terminate_job_processes job_id=%s pid=%s is_debug=%s owner=%s", job_id, pid, is_debug, owner)
    """
    Terminate a job's process tree, handling debugpy processes if needed, and update DB.

    Args:
        job_id (str): The job ID.
        pid (int): The process ID of the job.
        is_debug (bool): Whether this is a debug job.
        owner (str): The job owner.
        graceful_timeout (float): Timeout in seconds for graceful termination.
    """
    # Guard: Skip if job already in terminal state or pid None
    with Session(get_db_engine()) as session:
        job = session.get(Job, job_id)
        if job and (job.state in ['failed', 'done', 'cancelled', 'remove'] or job.pid is None):
            logger.debug("terminate_job_processes: skip %s state=%s pid=%s", job_id, job.state, job.pid)
            return

    # Get target processes to terminate
    target_processes = job_lib.get_process_tree(pid)

    # Send SIGTERM to targets
    job_lib.kill_proc_tree(target_processes, signal.SIGTERM)

    # Wait for graceful termination
    import time
    start_time = time.time()
    while (time.time() - start_time) < graceful_timeout:
        # Recreate target processes and check if any are still running
        target_processes = job_lib.get_process_tree(pid)
        alive = False
        for proc in target_processes:
            logger.debug(f"from {job_id} polling pid={pid} alive={proc.is_running()}")
            if proc.is_running():
                alive = True
        if not alive:
            postTerm(job_id)
            return
        time.sleep(1.0)

    # Force kill if still running
    job_lib.kill_proc_tree(target_processes, signal.SIGKILL)
    postTerm(job_id, killed=True)
    
def setup_runner_socket(sock_path="/tmp/pyops/runner.sock"):
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(f"ipc://{sock_path}")
    poller = zmq.Poller()
    poller.register(socket, flags=zmq.POLLIN)
    return context, socket, poller

def receive_and_process(socket):
    identity = socket.recv()
    socket.recv()  # empty frame
    packed = socket.recv()
    msg = msgpack.unpackb(packed)
    try:
        logger.debug(f"main: processing ZMQ msg {msg}")
        finishJob(msg["id"], msg["state"], msg["detail"])
    except Exception as e:
        logger.exception("Failed to finish job %s: %s", msg["id"], e)
    # Send REP
    socket.send(identity, zmq.SNDMORE)
    socket.send(b'', zmq.SNDMORE)
    socket.send(b'ack')

def handleOddJobs():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            or_(Job.state == "dequeued", Job.state == "running")
        ).order_by(Job.id.asc())

        jobs = session.scalars(stmt).all()

        for job in jobs:
            if job.state == "dequeued":
                job.state = "todo"
            else:
                job.state = "failed"
                job.detail = "incomplete execution detected on startup"
        commit_with_retry(session)

def purgeLeftoverServiceJobs():
    """Purge leftover service jobs at startup to ensure clean servicing."""
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            and_(Job.kind == "service", or_(Job.state == "dequeued", Job.state == "running", Job.state == "failed", Job.state == "done"))
        ).order_by(Job.id.asc())

        jobs = session.scalars(stmt).all()

        for job in jobs:
            logger.debug(f"Purging leftover service job {job.id} in state {job.state}")
            session.delete(job)
            lib_runner.drop(job.id, job.owner, job.set)
        commit_with_retry(session)

def getNextToDo():
    with Session(get_db_engine()) as session:
        logger.debug(f"getNextToDo: session ID {id(session)} at {datetime.now()}")
        stmt = select(Job).where(
            Job.state == "todo"
        ).order_by(Job.id.asc())

        job = session.scalar(stmt)

        if job != None:
            logger.debug(f"getNextToDo: before commit, session ID {id(session)}, job.state '{job.state}' at {datetime.now()}")
            job.state = "dequeued"
            commit_with_retry(session)
            logger.debug(f"getNextToDo: after commit, session ID {id(session)}, committed 'dequeued' for {job.id} at {datetime.now()}")
            logger.debug(f"getNextToDo: about to return job.id = '{job.id}'")

            return job.id
        else:
            return None

def assignJob(job_id: str, pid: int):
    logger.debug(f"assignJob(job_id='{job_id}' pid={pid})")

    with Session(get_db_engine()) as session:
        logger.debug(f"assignJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        old_pid = job.pid
        job.pid = pid
        if job.kind == "debug":
            port = job_lib.get_debug_port(job.owner)
            debugpy_pid = job_lib.get_debugpy_adapter_pid(port)
            job.debugpy_pid = debugpy_pid
            logger.debug(f"assignJob: debugpy_pid {debugpy_pid} for {job_id}")
        job.state = "running"
        job.start = datetime.now()
        logger.debug(f"assignJob: before commit, session ID {id(session)}, job.pid {old_pid} to {pid} for {job_id} at {datetime.now()}")
        commit_with_retry(session)
        logger.debug(f"assignJob: after commit, session ID {id(session)}, committed pid for {job_id} at {datetime.now()}")

def loadJob(job_id: str):
    """Loads job code from Git (unstaged if kind=="draft", else committed)."""
    logger.debug(f"loadJob(job_id={job_id})")

    with Session(get_db_engine()) as session:
        logger.debug(f"loadJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        if job is None:
            raise Exception(f"Job {job_id} not existing")
        if job.state != "dequeued":
            logger.info(f"Skipping loadJob {job_id}: state={job.state}")
            finishJob(job_id, job.state, "state no longer dequeued")
            raise Exception(f"Job {job_id} state changed to {job.state}")
        old_state = job.state
        repo = git.get_repo_path(job.set)
        main_key = f"{job.set}::{job.name}"
        override_code = None
        override_dict = None
        if job.overrides:
            try:
                override_dict = json.loads(job.overrides)
                if not isinstance(override_dict, dict):
                    raise Exception("Overrides must be a valid JSON dict")
            except json.JSONDecodeError:
                raise Exception("Invalid overrides JSON")
            if main_key in override_dict:
                override_code = override_dict[main_key]
        committed = job.kind not in ("debug", "draft")
        if override_code is not None:
            code = override_code
        else:
            logger.debug(f"loadJob: checking repo '{repo}' for files starting with '{job.name}.' ending '.py'")
            files_list = [f for f in os.listdir(repo) if f.startswith(f"{job.name}.") and f.endswith('.py')]
            logger.debug(f"loadJob: files_list in '{repo}': {files_list}")
            if not files_list:
                raise Exception(f"Scriptlet {job.set}::{job.name} not existing")
            filename = files_list[0]
            type_ = git.infer_type_from_filename(filename)
            code = git.read_content(repo, job.name, type_, committed=committed)
        logger.debug(f"loadJob: before commit, session ID {id(session)}, job.state '{old_state}' to 'loading' for {job_id} at {datetime.now()}")
        job.state = "loading"
        job.start = datetime.now()

        commit_with_retry(session)
        logger.debug(f"loadJob: after commit, session ID {id(session)}, committed 'loading' for {job_id} at {datetime.now()}")

        return job.owner, job.set, job.name, job.kind, job.param, code, job.overrides, job.max_retries

def cleanup_drop_done_jobs():
    logger.debug("cleanup_drop_done_jobs: starting")
    now = datetime.now()
    delay_seconds = files.conf_runner()["drop_done_delay_seconds"]
    cutoff = now - timedelta(seconds=delay_seconds)

    with Session(get_db_engine()) as session:
        logger.debug(f"cleanup_drop_done_jobs: session ID {id(session)} at {now}, delay={delay_seconds}s")
        stmt = select(Job).where(
            Job.state == "done",
            Job.drop_done == True,
            Job.end < cutoff
        )

        jobs_to_remove = session.scalars(stmt).all()
        logger.debug(f"cleanup_drop_done_jobs: found {len(jobs_to_remove)} jobs to mark for removal")

        for job in jobs_to_remove:
            logger.debug(f"cleanup_drop_done_jobs: marking job {job.id} for removal")
            job.state = "remove"

        if jobs_to_remove:
            commit_with_retry(session)
            logger.debug("cleanup_drop_done_jobs: committed changes")
        else:
            logger.debug("cleanup_drop_done_jobs: no jobs to clean up")

def finishJob(job_id: str, state: str, detail: str):
    logger.debug(f"finishJob(job_id='{job_id}', state='{state}', detail='{detail}')")

    with Session(get_db_engine()) as session:
        logger.debug(f"finishJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)

        if job != None:
            old_state = job.state
            if job.kind == "service" and state == "done" and job.drop_done:
                new_state = "remove"  # Immediately remove done services
            elif job.state == "running" and state == "done" and job.drop_done and not job.kind == "debug":
                new_state = state  # Keep as "done" for delayed removal
            elif job.state != "cancel" and job.state != "cancelling":
                new_state = state
            else:
                new_state = job.state

            job.state = new_state
            job.end = datetime.now()
            job.detail = detail

            if job.kind == "service" and state == "failed":
                logger.info("finishJob: service %s failed detail='%s'", job_id, detail)

            old_pid = job.pid
            job.pid = None
            logger.debug(f"finishJob({job_id}): before commit, session ID {id(session)}, changing {job_id} from '{old_state}' to '{new_state}' at {datetime.now()}")
            commit_with_retry(session)
            logger.debug(f"finishJob({job_id}): after commit, session ID {id(session)}, committed state '{job.state}' for {job_id} at {datetime.now()}")

            if old_pid:
                threading.Thread(target=terminate_job_processes, args=(job.id, old_pid, job.kind == "debug", job.owner), daemon=True).start()

def disposeJobs():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(Job.state == "removed")
        if session.scalar(stmt):
            logger.debug(f"disposeJobs: found removed jobs, deleting at {datetime.now()}")
            stmt = delete(Job).where(Job.state == "removed")
            session.execute(stmt)

            commit_with_retry(session)
            logger.debug("disposeJobs: committed deletions")

def cleanProcesses(processes: dict[int, subprocess.Popen]):
    cleaned_processes: dict[int, subprocess.Popen] = {}
    for pid in processes:
        p = processes[pid]
        if p.poll() is None:
            cleaned_processes[pid] = p

    return cleaned_processes

def getNextToTerm():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            or_(Job.state == "cancel", Job.state == "remove")
        )

        job = session.scalar(stmt)
        return {"id": job.id, "pid": job.pid} if job != None else None

def postTerm(job_id: str, killed: bool = False):
    logger.debug(f"postTerm({job_id}, killed={killed})")

    with Session(get_db_engine()) as session:
        logger.debug(f"postTerm: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)

        if job != None:
            old_state = job.state
            if job.state == "remove":
                logger.debug(f"postTerm: before delete, session ID {id(session)}, deleting {job_id} (state '{old_state}') at {datetime.now()}")
                session.delete(job)
                lib_runner.drop(job_id, job.owner, job.set)
            else:
                job.end = datetime.now()

                job.pid = None
                if job.state != "done" and job.state != "failed":
                    job.state = "cancelled"
                logger.info("postTerm called %s old_state=%s → new=%s caller=%s", job_id, old_state, job.state, traceback.format_stack(limit=5)[-1])
                logger.debug(f"postTerm: before commit, session ID {id(session)}, setting {job_id} to '{job.state}' from '{old_state}' at {datetime.now()}")
            commit_with_retry(session)
            logger.debug(f"postTerm: after commit, session ID {id(session)}, committed for {job_id} at {datetime.now()}")

def schedule():
    now = datetime.now()
    logger.debug(f"schedule: starting at {now}")

    with Session(get_db_engine()) as session:
        logger.debug(f"schedule: session ID {id(session)} at {now}")
        stmt = select(Schedule.id,
                      Schedule.set,
                      Schedule.name,
                      Schedule.owner,
                      Schedule.active,
                      Schedule.drop_done,
                      Schedule.max_retries,
                      Schedule.schedule,
                      Schedule.param,
                      Schedule.last)
        schedules = session.execute(stmt)

        for s in schedules:
            if s.active:
                if s.schedule:
                    # Check if there was a scheduled run between s.last and now
                    cron = croniter.croniter(s.schedule, s.last or datetime.min)
                    next_run = cron.get_next(datetime)
                    if next_run <= now:
                        # Check if not still running
                        running_jobs = lib_runner.find(s.owner, s.set, s.name, "task", ["todo", "dequeued", "running"])
                        if not running_jobs:
                            logger.debug(f"schedule: enqueuing task {s.set}::{s.name} (cron {s.schedule}) at {next_run}")
                            lib_runner.enqueue(s.set, s.name, s.owner, "task", s.drop_done, s.param, s.max_retries, None)

                            schedule_obj = session.get(Schedule, s.id)
                            schedule_obj.last = now
                            logger.debug(f"schedule: before commit, session ID {id(session)}, last {s.last} to {next_run} for {s.id} at {datetime.now()}")
                            commit_with_retry(session)
                            logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={next_run} for {s.id} at {datetime.now()}")
                elif not lib_runner.find(s.owner, s.set, s.name, "service", ["todo", "dequeued", "running", "cancel", "done"]) and \
                     len(lib_runner.find(s.owner, s.set, s.name, "service", ["failed"])) < 3:
                    logger.debug(f"schedule: enqueuing service {s.set}::{s.name} at {now}")
                    lib_runner.enqueue(s.set, s.name, s.owner, "service", s.drop_done, s.param, s.max_retries, None)
                    schedule_obj = session.get(Schedule, s.id)
                    old_last = schedule_obj.last
                    schedule_obj.last = now
                    logger.debug(f"schedule: before commit, session ID {id(session)}, last {old_last} to {now} for service {s.id} at {datetime.now()}")
                    commit_with_retry(session)
                    logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={now} for service {s.id} at {datetime.now()}")
        logger.debug(f"schedule: completed at {datetime.now()}")

def main(data_base: str, web_bind: str, venv_path: str, pkg_path: str, env):
    import time

    from app.lib import files
    log_conf = files.conf_log()
    level_str = log_conf["loglevel"]
    level = logging.DEBUG if level_str.lower() == "debug" else logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    # Add stderr handler for warnings
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    handlers.append(stderr_handler)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=handlers
    )
    logger.info("Runner logging configured: level=%s", level_str)

    os.environ = env

    # Init config early for subprocess isolation
    conf_path = os.path.join(files.project_root, "etc/pyops.json")
    files.Config.init(conf_path)

    killer = GracefulKiller()

    runner_context, runner_socket, runner_poller = setup_runner_socket()

    # restore jobs which were taken out of queue but not executed due to crash
    handleOddJobs()

    # purge leftover service jobs at startup to ensure clean servicing
    purgeLeftoverServiceJobs()

    # clean up drop_done jobs that have been done for 60+ seconds
    cleanup_drop_done_jobs()

    processes: dict[int, subprocess.Popen] = {}

    loop_count = 0
    while not killer.kill_now:
        try:
            loop_count += 1
            logger.debug(f"main loop iteration {loop_count} at {datetime.now()}")
            current_time = time.time()

            # dispose already remove jobs
            logger.debug("main: calling disposeJobs")
            disposeJobs()
            logger.debug("main: disposeJobs completed")

            # remove cancelled processes from list
            processes = cleanProcesses(processes)
            logger.debug(f"main: cleaned processes, {len(processes)} remaining")

            # Check for stale PIDs in DB that are not running
            with Session(get_db_engine()) as session:
                stmt = select(Job.id, Job.pid).where(Job.state == "running", Job.pid.isnot(None))
                for job_id, pid in session.execute(stmt).all():
                    try:
                        if psutil.Process(pid).is_running():
                            continue
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.ZombieProcess:
                        logger.warning("Stale PID %s for job %s: ZombieProcess", pid, job_id)
                        pass
                    except psutil.AccessDenied:
                        logger.warning("Stale PID %s for job %s: AccessDenied", pid, job_id)
                        pass

                    finishJob(job_id, "failed", f"stale process pid={pid}")
                        

            # terminate "remove" or "cancel" jobs
            try:
                logger.debug("main: calling getNextToTerm")
                job = getNextToTerm()
                logger.debug(f"main: getNextToTerm returned {job}")

                if job != None:
                    if job["pid"] in processes:
                        logger.debug(f"Trying to term job {job}")
                        # Get job kind for termination
                        with Session(get_db_engine()) as session:
                            job_obj = session.get(Job, job["id"])
                            is_debug = job_obj.kind == "debug" if job_obj else False
                            owner = job_obj.owner if job_obj else None
                            detail = job_obj.detail
                        finishJob(job["id"], "cancelling", detail)
                    else:
                        postTerm(job["id"])
            except Exception as e:
                logger.debug(f"main: exception in terminate block: {e}")
                logger.exception("Error terminating job: %s", e)

            # handle jobs "failed" or "done"
            events = runner_poller.poll(0)
            for sock, _ in events:
                receive_and_process(runner_socket)

            # clean up drop_done jobs that have been done for delay+ seconds
            cleanup_drop_done_jobs()

            try:
                logger.debug("main: calling getNextToDo")
                job_id = getNextToDo()
                logger.debug(f"main: job_id from getNextToDo = '{job_id}'")

                if job_id != None:
                    try:
                        logger.debug(f"main: loading job {job_id}")
                        owner, set, name, kind, param, code, overrides, max_retries = loadJob(job_id)

                        port = None
                        if kind == "debug":
                            try:
                                port = job_lib.validate_debug_prerequisites(owner)
                            except Exception as e:
                                finishJob(job_id, "failed", str(e))
                                continue

                        libs = []

                        override_dict = json.loads(overrides) if overrides else None

                        if param:
                            param = job_lib.process_imports(param, set, kind, libs, override_dict)
                        code = job_lib.process_imports(code, set, kind, libs, override_dict)

                        logger.debug(f"Run job {job_id}")
                        ensure_set_environment(set)
                        venv_python = os.path.join(venv_path, set, 'bin', 'python')
                        json_kwargs = json.dumps({"job_id": job_id, "owner": owner, "set": set, "name": name, "kind": kind, "param": param, "code": code, "libs": libs, "overrides": overrides, "runner_sock_path": "/tmp/pyops/runner.sock", "max_retries": max_retries, "port": port, "conf_data": files.conf_data(), "conf_runner": files.conf_runner(), "conf_users": files.conf_users(), "conf_web": files.conf_web()})
                        try:
                            p = subprocess.Popen([venv_python, os.path.abspath('app/job_runner.py'), json_kwargs], cwd=files.project_root, stdout=sys.stdout, stderr=sys.stderr, text=True)
                        except Exception as e:
                            finishJob(job_id, "failed", f"Launch failed: {e}")
                            continue

                        if kind == "debug":
                            adapter_up = False
                            for attempt in range(50):  # 5 seconds max
                                if p.poll() is not None:
                                    logger.warning("Job process died before debugpy adapter appeared for job %s", job_id)
                                    break
                                if job_lib.has_debugpy_adapter(port):
                                    adapter_up = True
                                    logger.debug("Debugpy adapter is up for job %s", job_id)
                                    break
                                time.sleep(0.1)

                            if not adapter_up:
                                if p.poll() is None:
                                    logger.warning("Debugpy adapter did not appear within timeout for job %s - terminating broken debug job", job_id)
                                    # Mark job as failed
                                    finishJob(job_id, "failed", "Debug adapter failed to start within timeout")
                                    continue
                                else:
                                    logger.warning("Job process died before debugpy adapter appeared for job %s", job_id)
                                    # Let normal cleanup handle dead process
                                    continue

                        # Adapter is up or not a debug job - proceed normally
                        assignJob(job_id, p.pid)
                        processes[p.pid] = p
                        logger.debug(f"main: started process for {job_id}, pid {p.pid}")

                    except Exception as e:
                        logger.debug(f"main: exception loading/running {job_id}: {e}")
                        logger.exception("Error loading/running job %s: %s", job_id, e)
                        finishJob(job_id, "failed", traceback.format_exc())
                else:
                    logger.debug("main: no job, calling schedule")
                    schedule()
                    logger.debug("main: schedule completed")

                    logger.debug("main: sleeping 0.5s")
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"main: exception in main try block iteration {loop_count}: {e}")
                logger.exception("Error in main loop: %s", e)
        except Exception as e:
            logger.exception("Critical error in main loop iteration %d, continuing: %s", loop_count, e)
            time.sleep(1)  # Brief pause before retry to avoid tight loops on persistent errors

    # join running processes for graceful shutdown with timeout/kill logic
    for pid in list(processes.keys()):
        p = processes[pid]
        if p.poll() is None:
            # Get job info for termination
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter_by(pid=pid).first()
                is_debug = job.kind == "debug" if job else False
                owner = job.owner if job else None

            logger.debug(f"Terminating process tree {pid} on shutdown")
            finishJob(job_id, "cancelling", "shutdown")
            try:
                p.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                p.terminate()

    # handle finished or failed jobs
    events = runner_poller.poll(0)
    while events:
        for sock, _ in events:
            receive_and_process(runner_socket)
        events = runner_poller.poll(0)

    # dispose already remove jobs
    logger.debug("main: final disposeJobs in shutdown")
    disposeJobs()
    logger.debug("main: final disposeJobs completed")

    # handle finished or failed jobs
    events = runner_poller.poll(0)
    while events:
        for sock, _ in events:
            receive_and_process(runner_socket)
        events = runner_poller.poll(0)

    # dispose already remove jobs
    logger.debug("main: final disposeJobs in shutdown")
    disposeJobs()
    logger.debug("main: final disposeJobs completed")

    # cleanup ZMQ
    runner_poller.unregister(runner_socket)
    runner_socket.setsockopt(zmq.LINGER, 0)
    runner_socket.close()
    runner_context.term()

    logger.debug("runner exited")
