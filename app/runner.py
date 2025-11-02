import signal, time, os, sys, re
import traceback
import croniter
import subprocess
from datetime import datetime
from multiprocessing import Process, current_process, Queue

from sqlalchemy import null, select, delete, and_, or_
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.job import Job
from app.orm.schedule import Schedule

from app.lib import runner as lib_runner, files
# Import Git helpers for code loading.
from app.lib import git
from app.lib import pyops

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
        session.commit()

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
            session.commit()
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
        logger.debug(f"assignJob: before commit, session ID {id(session)}, job.pid {old_pid} to {pid} for {job_id} at {datetime.now()}")
        session.commit()
        logger.debug(f"assignJob: after commit, session ID {id(session)}, committed pid for {job_id} at {datetime.now()}")

def loadLib(set: str, name: str, debug: bool):
    """Loads library code from Git (unstaged if debug, else committed)."""
    logger.debug(f"loadLib(set={set}, name={name}, debug={debug})")
    repo = git.get_repo_path(set)
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise Exception(f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    type_ = git.infer_type_from_filename(filename)
    committed = not debug
    return git.read_content(repo, name, type_, committed=committed)

def loadJob(job_id: str):
    """Loads job code from Git (unstaged if debug, else committed)."""
    logger.debug(f"loadJob(job_id={job_id})")

    with Session(get_db_engine()) as session:
        logger.debug(f"loadJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)
        old_state = job.state
        debug = job.kind == "debug"
        repo = git.get_repo_path(job.set)
        logger.debug(f"loadJob: checking repo '{repo}' for files starting with '{job.name}.' ending '.py'")
        files_list = [f for f in os.listdir(repo) if f.startswith(f"{job.name}.") and f.endswith('.py')]
        logger.debug(f"loadJob: files_list in '{repo}': {files_list}")
        if not files_list:
            raise Exception(f"Scriptlet {job.set}::{job.name} not existing")
        filename = files_list[0]
        type_ = git.infer_type_from_filename(filename)
        committed = not debug
        code = git.read_content(repo, job.name, type_, committed=committed)

        logger.debug(f"loadJob: before commit, session ID {id(session)}, job.state '{old_state}' to 'running' for {job_id} at {datetime.now()}")
        job.state = "running"
        job.start = datetime.now()

        session.commit()
        logger.debug(f"loadJob: after commit, session ID {id(session)}, committed 'running' for {job_id} at {datetime.now()}")

        return job.owner, job.set, job.name, job.kind, job.param, code, debug

def finishJob(job_id: str, state: str, detail: str):
    logger.debug(f"finishJob(job_id='{job_id}', state='{state}', detail='{detail}')")

    with Session(get_db_engine()) as session:
        logger.debug(f"finishJob: session ID {id(session)} at {datetime.now()}")
        job = session.get(Job, job_id)

        if job != None:
            old_state = job.state
            if job.state == "running" and state == "done" and job.drop_done and not job.kind == "debug":
                new_state = "remove"
            else:
                new_state = state
            job.state = new_state
            job.end = datetime.now()
            job.detail = detail
            job.pid = None
            logger.debug(f"finishJob: before commit, session ID {id(session)}, changing {job_id} from '{old_state}' to '{new_state}' at {datetime.now()}")
            session.commit()
            logger.debug(f"finishJob: after commit, session ID {id(session)}, committed state '{job.state}' for {job_id} at {datetime.now()}")

def disposeJobs():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(Job.state == "removed")
        if session.scalar(stmt):
            logger.debug(f"disposeJobs: found removed jobs, deleting at {datetime.now()}")
            stmt = delete(Job).where(Job.state == "removed")
            session.execute(stmt)

            session.commit()
            logger.debug("disposeJobs: committed deletions")

def process_imports(text: str, current_set: str, debug: bool, libs: list[str]) -> str:
    r"""
    Processes lines starting with !{set::name} or !{::name} (empty set uses current_set).
    - Strictly at line start (^!\{...}), no indentation allowed.
    - Loads library code, appends to libs if successful.
    - Replaces with '# importing set::name' + rest of line.
    - On failure (load error, invalid format), keeps original line and logs warning.
    - No recursion: assumes libs are pure (no further !{} directives).
    - Preserves line endings and rest of line content.
    
    Args:
        text (str): Input text (param or code) to process.
        current_set (str): Job set for empty ::name resolution.
        debug (bool): Use unstaged code for libs.
        libs (list[str]): Mutable list to append loaded lib codes.
    
    Returns:
        str: Updated text with replacements.
    """
    lines = text.splitlines(keepends=True)
    new_lines = []
    for line in lines:
        match = re.match(r'^!\{([a-zA-Z0-9_-]*?)(::([a-zA-Z0-9_-]+))?\}', line)
        if match and match.group(2):  # Has :: separator
            g = match.group(1)
            n = match.group(3)
            if g or n:  # Valid non-empty
                effective_g = current_set if not g else g
                try:
                    lib_code = loadLib(effective_g, n, debug)
                    libs.append(lib_code)
                    replacement = f'# importing {effective_g}::{n}'
                    new_line = replacement + line[match.end():]
                except Exception as e:
                    logger.warning(f"Failed to load lib {effective_g}::{n} in job processing: {e}")
                    new_line = line  # Keep original
            else:
                new_line = line  # Invalid (empty)
        else:
            new_line = line  # No match or invalid format
        new_lines.append(new_line)
    return ''.join(new_lines)

def process_install_lines(lines, venv_path, pkg_path, id):
    """Process ! lines for installs; uses env prop + CWD for shebang/path resolution."""
    new_lines = []
    pip_path = os.path.join(venv_path, 'bin', 'pip')
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('!'):
            rest = stripped[1:].lstrip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 1:
                new_lines.append(line)
                continue
            arg1 = parts[0].strip()
            arg2 = parts[1].strip() if len(parts) > 1 else None
            if '://' in arg1 or arg1.startswith('git@'):
                repo_name = os.path.splitext(os.path.basename(arg1))[0]
                # Sanitize for Python import (e.g., 'my-lib-1.0' -> 'my_lib_1_0')
                repo_name = re.sub(r'[^\w]', '_', repo_name)
                repo_dir = os.path.join(pkg_path, repo_name)
                if not os.path.exists(repo_dir):
                    clone_args = ['clone', arg1, repo_dir]
                    if arg2:
                        clone_args.insert(1, '-b')
                        clone_args.insert(2, arg2)
                    result = subprocess.run(['git'] + clone_args, capture_output=True, text=True, cwd=pkg_path)
                    if result.returncode != 0:
                        raise Exception(f"git clone {arg1} failed: {result.stderr}")
                else:
                    if arg2:
                        ck_result = subprocess.run(['git', '-C', repo_dir, 'checkout', arg2], capture_output=True, text=True, cwd=repo_dir)
                        if ck_result.returncode != 0:
                            raise Exception(f"git checkout {arg2} in {repo_dir} failed: {ck_result.stderr}")
                    pull_result = subprocess.run(['git', '-C', repo_dir, 'pull'], capture_output=True, text=True, cwd=repo_dir)
                    if pull_result.returncode != 0:
                        raise Exception(f"git pull in {repo_dir} failed: {pull_result.stderr}")
                new_lines.append('# ' + line)
            else:
                module = arg1
                result = subprocess.run([pip_path, 'list', '--format=freeze'], capture_output=True, text=True, cwd=venv_path)
                if result.returncode != 0:
                    raise Exception(f"pip list failed: {result.stderr}")
                installed = any(pkg.split('==')[0] == module for pkg in result.stdout.splitlines() if '==' in pkg)
                if not installed:
                    install_result = subprocess.run([pip_path, 'install', '-q', module], capture_output=True, text=True)
                    if install_result.returncode != 0:
                        raise Exception(f"pip install {module} failed: {install_result.stderr}")
                new_lines.append('# ' + line)
        else:
            new_lines.append(line)
    return new_lines

def run(job_id: str, owner: str, set: str, name: str, kind: str, param: str, code: str, libs: list[str], q: Queue, data_base: str, web_bind: str, venv_path: str, pkg_path: str):
    """Executes job in isolated workspace; activates venv, processes installs w/ env prop."""
    userspace = os.path.join(data_base, f"{owner}")
    setspace = os.path.join(userspace, f"{set}")
    workspace = os.path.join(setspace, f"{job_id}")

    if not os.path.exists(userspace):
        os.makedirs(userspace)

    if not os.path.exists(setspace):
        os.makedirs(setspace)

    if not os.path.exists(workspace):
        os.makedirs(workspace)

    logger.debug(f"{current_process().pid:<10}start({job_id})")

    import sys
    site_packages = os.path.join(venv_path, 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages')

    sys.path.insert(0, pkg_path)
    sys.path.insert(1, site_packages)

    stdout_file = open(os.path.join(workspace, "stdout.log"), 'w', 1)
    stderr_file = open(os.path.join(workspace, "stderr.log"), 'w', 1)
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param_lines = param.splitlines(keepends=True) if param else []
                new_param_lines = process_install_lines(param_lines, venv_path, pkg_path, job_id)
                param = ''.join(new_param_lines)

                new_libs = []
                for l in libs:
                    l_lines = l.splitlines(keepends=True)
                    new_l_lines = process_install_lines(l_lines, venv_path, pkg_path, job_id)
                    new_libs.append(''.join(new_l_lines))
                libs = new_libs

                code_lines = code.splitlines(keepends=True)
                new_code_lines = process_install_lines(code_lines, venv_path, pkg_path, job_id)
                code = ''.join(new_code_lines)

                if kind == "service" or kind == "debug":
                    listener = pyops.ServiceListener(job_id, current_user=owner)
                else:
                    listener = None

                glob = {
                    "userspace": userspace,
                    "setspace": setspace,
                    "workspace": workspace,
                    "gitspace": os.path.join(files.project_root, files.conf_data()["git"]),
                    "pkgspace": os.path.join(files.project_root, files.conf_runner()["pkg"]),
                    "jobsspace": os.path.join(files.project_root, files.conf_data()["jobs"]),
                    "control": pyops.Control(web_bind),
                    "listener" : listener
                }

                os.chdir(workspace)

                root = logging.getLogger()
                root.setLevel(logging.DEBUG if kind == "debug" else logging.INFO)

                info_handler = logging.StreamHandler(sys.stdout)
                info_handler.setLevel(logging.DEBUG)
                info_handler.addFilter(lambda r: r.levelno < logging.WARNING)
                root.addHandler(info_handler)

                warn_handler = logging.StreamHandler(sys.stderr)
                warn_handler.setLevel(logging.WARNING)
                root.addHandler(warn_handler)
                    
                if param:
                    exec(param, glob)
                for l in libs:
                    exec(l, glob)
                exec(code, glob)

                q.put({"id": job_id, "state": "done", "detail": None})
    except Exception:
        error_msg = f"Error executing job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        q.put({"id": job_id, "state": "failed", "detail": traceback.format_exc()})
    finally:
        stdout_file.close()
        stderr_file.close()
        logger.debug(f"{current_process().pid:<10}end({job_id})")

def cleanProcesses(processes: dict[int, Process]):
    cleaned_processes: dict[int, Process] = {}
    for pid in processes:
        p = processes[pid]
        if p.is_alive:
            cleaned_processes[pid] = p

    return cleaned_processes

def getNextToTerm():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(
            or_(Job.state == "cancel", Job.state == "remove")
        )

        job = session.scalar(stmt)
        return {"id": job.id, "pid": job.pid} if job != None else None

def term(job, processes):
    logger.debug(f"term({job})")
    p = processes[job["pid"]]
    p.terminate()

    # Wait up to 5 seconds for graceful termination
    timeout = 5.0
    start_time = time.time()
    while p.is_alive() and (time.time() - start_time) < timeout:
        time.sleep(0.1)
    
    killed = p.is_alive()
    if killed:
        logger.debug(f"Force killing process {job['pid']} after timeout")
        p.kill()
        # Wait a bit more for kill to take effect
        p.join(timeout=1.0)
        killed = True  # Assume killed after force kill
    
    postTerm(job["id"], killed=killed)

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
                if killed:
                    job.state = "killed"
                    job.detail = "force killed after 5s timeout"
                else:
                    job.state = "cancelled"
                    job.detail = "terminated gracefully"
                logger.debug(f"postTerm: before commit, session ID {id(session)}, setting {job_id} to '{job.state}' from '{old_state}' at {datetime.now()}")
            session.commit()
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
                      Schedule.schedule,
                      Schedule.param,
                      Schedule.last)
        schedules = session.execute(stmt)

        for s in schedules:
            if s.active:
                if s.schedule:
                    cron = croniter.croniter(s.schedule, now)
                    prev = cron.get_prev(datetime)
                    logger.debug(f"schedule: enqueuing task {s.set}::{s.name} (cron {s.schedule}) at {prev}")
                    lib_runner.enqueue(s.set, s.name, s.owner, "task", s.drop_done, s.param)

                    schedule_obj = session.get(Schedule, s.id)
                    old_last = schedule_obj.last
                    schedule_obj.last = prev
                    logger.debug(f"schedule: before commit, session ID {id(session)}, last {old_last} to {prev} for {s.id} at {datetime.now()}")
                    session.commit()
                    logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={prev} for {s.id} at {datetime.now()}")
                elif not lib_runner.find(s.owner, s.set, s.name, "service", ["todo", "dequeued", "running", "cancel", "done"]) and \
                     len(lib_runner.find(s.owner, s.set, s.name, "service", ["failed"])) < 3:
                    logger.debug(f"schedule: enqueuing service {s.set}::{s.name} at {now}")
                    lib_runner.enqueue(s.set, s.name, s.owner, "service", s.drop_done, s.param)
                    schedule_obj = session.get(Schedule, s.id)
                    old_last = schedule_obj.last
                    schedule_obj.last = now
                    logger.debug(f"schedule: before commit, session ID {id(session)}, last {old_last} to {now} for service {s.id} at {datetime.now()}")
                    session.commit()
                    logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={now} for service {s.id} at {datetime.now()}")
        logger.debug(f"schedule: completed at {datetime.now()}")

def main(data_base: str, web_bind: str, venv_path: str, pkg_path: str, env):
    import time
    os.environ = env
    killer = GracefulKiller()
    
    q = Queue()

    # restore jobs which were taken out of queue but not executed due to crash
    handleOddJobs()

    processes: dict[int, Process] = {}

    last_pull_time = 0
    pull_interval = git.get_git_pull_interval()

    loop_count = 0
    while not killer.kill_now:
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

        # terminate "remove" or "cancel" jobs
        try:
            logger.debug("main: calling getNextToTerm")
            job = getNextToTerm()
            logger.debug(f"main: getNextToTerm returned {job}")
            
            if job != None:
                if job["pid"] in processes:
                    logger.debug(f"Trying to term job {job}")
                    term(job, processes)
                else:
                    postTerm(job["id"])
        except Exception as e:
            logger.debug(f"main: exception in terminate block: {e}")
            logger.exception("Error terminating job: %s", e)

        # handle jobs "failed" or "done"
        while not q.empty():
            msg = q.get()
            logger.debug(f"main: processing queue msg {msg}")
            finishJob(msg["id"], msg["state"], msg["detail"])

        try:
            logger.debug("main: calling getNextToDo")
            job_id = getNextToDo()
            logger.debug(f"main: job_id from getNextToDo = '{job_id}'")

            if job_id != None:
                try:
                    logger.debug(f"main: loading job {job_id}")
                    owner, set, name, kind, param, code, debug = loadJob(job_id)

                    libs = []

                    if param:
                        param = process_imports(param, set, debug, libs)
                    code = process_imports(code, set, debug, libs)

                    logger.debug(f"Run job {job_id}")
                    p = Process(target=run, kwargs={"job_id": job_id, "owner": owner, "set": set, "name": name, "kind": kind, "param": param, "code": code, "libs": libs, "q": q, "data_base": data_base, "web_bind": web_bind, "venv_path": venv_path, "pkg_path": pkg_path})
                    p.start()
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

                # Periodic Git pull
                if current_time - last_pull_time > pull_interval:
                    logger.debug("main: performing git pulls")
                    git_base = git.get_git_base()
                    sets = [d for d in os.listdir(git_base) if os.path.isdir(os.path.join(git_base, d)) and os.path.exists(os.path.join(git_base, d, ".git"))]
                    for set in sets:
                        try:
                            repo = git.get_repo_path(set)
                            git.pull_if_needed(repo)
                            logger.debug(f"main: pulled {set}")
                        except Exception as e:
                            logger.debug(f"main: pull error for {set}: {e}")
                            logger.exception("Error pulling repo %s: %s", set, e)
                    last_pull_time = current_time
                    logger.debug("main: git pulls completed")

                logger.debug("main: sleeping 0.5s")
                time.sleep(0.5)
        except Exception as e:
            logger.debug(f"main: exception in main try block iteration {loop_count}: {e}")
            logger.exception("Error in main loop: %s", e)

    # join running processes for graceful shutdown with timeout/kill logic
    for pid in list(processes.keys()):
        p = processes[pid]
        if p.is_alive:
            logger.debug(f"Terminating {pid} on shutdown")
            p.terminate()

            # Wait up to 5 seconds for graceful termination
            timeout = 5.0
            start_time = time.time()
            while p.is_alive() and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            killed = p.is_alive()
            if killed:
                logger.debug(f"Force killing process {pid} after timeout on shutdown")
                p.kill()
                p.join(timeout=1.0)

            # Update job state
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter_by(pid=pid).first()
                if job:
                    job.end = datetime.now()
                    job.pid = None
                    if killed:
                        job.state = "killed"
                        job.detail = "force killed after 5s timeout on shutdown"
                    else:
                        job.state = "cancelled"
                        job.detail = "terminated gracefully on shutdown"
                    session.commit()

    # handle finished or failed jobs
    while not q.empty():
        msg = q.get()
        finishJob(msg["id"], msg["state"], msg["detail"])

    # dispose already remove jobs
    logger.debug("main: final disposeJobs in shutdown")
    disposeJobs()
    logger.debug("main: final disposeJobs completed")

    # handle finished or failed jobs
    while not q.empty():
        msg = q.get()
        finishJob(msg["id"], msg["state"], msg["detail"])

    # dispose already remove jobs
    logger.debug("main: final disposeJobs in shutdown")
    disposeJobs()
    logger.debug("main: final disposeJobs completed")

    logger.debug("runner exited")
