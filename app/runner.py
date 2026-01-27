import signal, time, os, sys, re
import traceback
import croniter
import subprocess
import json
from datetime import datetime, timedelta
from io import TextIOWrapper
from multiprocessing import Process, current_process, Queue
import threading
from typing import Any

import psutil

from sqlalchemy import null, select, delete, and_, or_
from sqlalchemy.orm import Session

from app.orm.database import get_db_engine
from app.orm.job import Job
from app.orm.schedule import Schedule

from app.lib import runner as lib_runner, files
# Import Git helpers for code loading.
from app.lib import git
from app.lib import pyops
from app.lib.sets import ensure_set_environment

import logging
from contextlib import redirect_stdout, redirect_stderr
logger = logging.getLogger(__name__)

def kill_proc_tree(processes: list, sig=signal.SIGTERM):
    """Kill all processes in the list using specified signal."""
    for proc in processes:
        try:
            os.kill(proc.pid, sig)
        except OSError:
            pass  # Process already dead

def terminate_debugpy_adapter(owner: str):
    """
    Terminate the debugpy adapter process for the given owner.

    Args:
        owner (str): The job owner for debugpy port lookup.
    """
    logger.info("ENTER terminate_debugpy_adapter owner=%s", owner)
    user_conf = files.conf_users().get(owner, {})
    port = user_conf.get("debug_port")
    if port:
        pid = get_debugpy_adapter_pid(port)
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                logger.info("killed debugpy adapter PID %s", pid)
            except OSError as e:
                logger.warning("failed to kill debugpy adapter PID %s: %s", pid, e)
        else:
            logger.warning("no debugpy adapter process found for port %s", port)
    else:
        logger.warning("no debug_port configured for owner %s", owner)

def get_process_tree(pid: int) -> list[psutil.Process]:
    try:
        parent = psutil.Process(pid)
        tree = [parent]
        tree.extend(parent.children(recursive=True))
        return tree
    except psutil.NoSuchProcess:
        return []

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
    # Get target processes to terminate
    target_processes = get_process_tree(pid)

    # Send SIGTERM to targets
    kill_proc_tree(target_processes, signal.SIGTERM)

    # Wait for graceful termination
    import time
    start_time = time.time()
    while (time.time() - start_time) < graceful_timeout:
        # Recreate target processes and check if any are still running
        target_processes = get_process_tree(pid)
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
    kill_proc_tree(target_processes, signal.SIGKILL)
    postTerm(job_id, killed=True)

def get_debug_port(owner: str) -> int:
    from app.lib import files
    users = files.conf_users()
    user_conf = users.get(owner, {})
    port = user_conf.get("debug_port")
    if not port or not isinstance(port, int) or port < 1025 or port > 65535:
        raise Exception(f"Invalid or missing debug_port for user {owner}")
    return port

def has_debugpy_adapter(port: int) -> bool:
    return get_debugpy_adapter_pid(port) is not None

def get_debugpy_adapter_pid(port: int) -> int | None:
    logger.debug(f"Searching for debugpy adapter on port {port}")
    port_str = str(port)
    for pinfo in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = pinfo.info['cmdline'] or []
            # Precise match: --port XXX
            port_match = False
            i = 0
            while i < len(cmdline) - 1:
                if cmdline[i] == '--port' and cmdline[i + 1] == port_str:
                    port_match = True
                    break
                i += 1
            adapter_match = any('adapter' in arg.lower() for arg in cmdline)
            if port_match and adapter_match:
                logger.debug(f"Found debugpy adapter PID {pinfo.info['pid']}: {cmdline}")
                return pinfo.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    logger.debug("No debugpy adapter found")
    return None

def validate_debug_prerequisites(job_id: str, owner: str) -> int:
    return get_debug_port(owner)

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
        if job.kind == "debug":
            port = get_debug_port(job.owner)
            debugpy_pid = get_debugpy_adapter_pid(port)
            job.debugpy_pid = debugpy_pid
            logger.debug(f"assignJob: debugpy_pid {debugpy_pid} for {job_id}")
        job.state = "running"
        job.start = datetime.now()
        logger.debug(f"assignJob: before commit, session ID {id(session)}, job.pid {old_pid} to {pid} for {job_id} at {datetime.now()}")
        session.commit()
        logger.debug(f"assignJob: after commit, session ID {id(session)}, committed pid for {job_id} at {datetime.now()}")

def loadLib(set: str, name: str, kind: str):
    """Loads library code from Git (unstaged if kind=="debug", else committed)."""
    logger.debug(f"loadLib(set={set}, name={name}, kind={kind})")
    repo = git.get_repo_path(set)
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise Exception(f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    type_ = git.infer_type_from_filename(filename)
    committed = kind != "debug"
    code = git.read_content(repo, name, type_, committed=committed)
    return code

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
        committed = job.kind != "debug"
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

        session.commit()
        logger.debug(f"loadJob: after commit, session ID {id(session)}, committed 'running' for {job_id} at {datetime.now()}")

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
            session.commit()
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

            logger.debug(f"finishJob({job_id}): pid={job.pid} kind={job.kind}, calling terminate")

            if job.pid:
                threading.Thread(target=terminate_job_processes, args=(job.id, job.pid, job.kind == "debug", job.owner), daemon=True).start()

            job.pid = None
            logger.debug(f"finishJob({job_id}): before commit, session ID {id(session)}, changing {job_id} from '{old_state}' to '{new_state}' at {datetime.now()}")
            session.commit()
            logger.debug(f"finishJob({job_id}): after commit, session ID {id(session)}, committed state '{job.state}' for {job_id} at {datetime.now()}")

def disposeJobs():
    with Session(get_db_engine()) as session:
        stmt = select(Job).where(Job.state == "removed")
        if session.scalar(stmt):
            logger.debug(f"disposeJobs: found removed jobs, deleting at {datetime.now()}")
            stmt = delete(Job).where(Job.state == "removed")
            session.execute(stmt)

            session.commit()
            logger.debug("disposeJobs: committed deletions")

def process_imports(text: str, current_set: str, kind: str, libs: list[dict[str, str]], overrides_dict: dict[str, str] = None, seen_libs: set[str] = None, depth: int = 0) -> str:
    r"""
    Recursively processes directives ^#!\{[set::]name\} in text/libs.
    Loads/overrides/appends to libs before exec (topo order).
    Depth/cycle safe.
    """
    if depth > 10:
        logger.warning("Directive depth limit exceeded")
        return text
    if seen_libs is None:
        seen_libs = set()
    for line in text.splitlines():
        match = re.match(r'^#!\{([a-zA-Z0-9_-]*?)(::([a-zA-Z0-9_-]+))?\}', line)
        if match and match.group(2):
            g = match.group(1) or current_set
            n = match.group(3)
            if not n: continue
            lib_key = f"{g}::{n}"
            if lib_key in seen_libs: continue
            seen_libs.add(lib_key)
            try:
                if overrides_dict and lib_key in overrides_dict:
                    lib_code = overrides_dict[lib_key]
                else:
                    lib_code = loadLib(g, n, kind)
                # Recurse sub-deps
                lib_code = process_imports(lib_code, g, kind, libs, overrides_dict, seen_libs, depth + 1)
                libs.append({"code": lib_code, "filename": lib_key})
            except Exception as e:
                logger.error(f"Failed to load library {lib_key}: {e}")
                raise
    return text

def process_install_lines(lines, venv_path, pkg_path, set, id):
    """Process #! lines for installs; uses env prop + CWD for shebang/path resolution."""
    # Ensure per-set directories and venv exist for migration scenarios
    ensure_set_environment(set)
    pip_path = os.path.join(venv_path, set, 'bin', 'pip')
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('#!'):
            rest = stripped[2:].lstrip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 1:
                continue
            arg1 = parts[0].strip()
            arg2 = parts[1].strip() if len(parts) > 1 else None
            if '://' in arg1 or arg1.startswith('git@'):
                repo_name = os.path.splitext(os.path.basename(arg1))[0]
                # Sanitize for Python import (e.g., 'my-lib-1.0' -> 'my_lib_1_0')
                repo_name = re.sub(r'[^\w]', '_', repo_name)
                repo_dir = os.path.join(pkg_path, set, repo_name)
                if not os.path.exists(repo_dir):
                    clone_args = ['clone', arg1, repo_dir]
                    if arg2:
                        clone_args.insert(1, '-b')
                        clone_args.insert(2, arg2)
                    result = subprocess.run(['git'] + clone_args, capture_output=True, text=True, cwd=os.path.join(pkg_path, set))
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
            else:
                # Skip if it looks like a library directive (starts with { or contains ::)
                if rest.startswith('{') or '::' in rest:
                    continue
                # Use entire rest as pip install arguments
                package_spec = rest
                install_result = subprocess.run([pip_path, 'install', '-q', package_spec], capture_output=True, text=True)
                if install_result.returncode != 0:
                    raise Exception(f"pip install {package_spec} failed: {install_result.stderr}")
    return lines

def process_code_installs(param: str, code: str, libs: list[dict], venv_path: str, pkg_path: str, set: str, job_id: str) -> tuple[str, str, list[dict]]:
    """Process #!pip/#!git installs in param, code, and libs"""
    # Process param
    if param:
        param_lines = param.splitlines(keepends=True)
        new_param_lines = process_install_lines(param_lines, venv_path, pkg_path, set, job_id)
        param = ''.join(new_param_lines)

    # Process libs
    new_libs = []
    for lib in libs:
        l_code = lib["code"]
        l_lines = l_code.splitlines(keepends=True)
        new_l_lines = process_install_lines(l_lines, venv_path, pkg_path, set, job_id)
        new_lib = {"code": ''.join(new_l_lines), "filename": lib["filename"]}
        new_libs.append(new_lib)
    libs = new_libs

    # Process code
    code_lines = code.splitlines(keepends=True)
    new_code_lines = process_install_lines(code_lines, venv_path, pkg_path, set, job_id)
    code = ''.join(new_code_lines)

    return param, code, libs

def setup_workspace(data_base: str, owner: str, set: str, job_id: str) -> tuple[str, str, str]:
    """Create and return workspace paths: (userspace, setspace, workspace)"""
    userspace = os.path.join(data_base, f"{owner}")
    setspace = os.path.join(userspace, f"{set}")
    workspace = os.path.join(setspace, f"{job_id}")

    if not os.path.exists(userspace):
        os.makedirs(userspace)

    if not os.path.exists(setspace):
        os.makedirs(setspace)

    if not os.path.exists(workspace):
        os.makedirs(workspace)

    return userspace, setspace, workspace

def setup_execution_environment(venv_path: str, pkg_path: str, set: str, workspace: str, log_level: int) -> tuple[TextIOWrapper, TextIOWrapper]:
    """Setup sys.path, change to workspace directory, and return stdout/stderr files"""
    import sys
    site_packages = os.path.join(venv_path, set, 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages')

    sys.path.insert(0, os.path.join(pkg_path, set))
    sys.path.insert(1, site_packages)

    os.chdir(workspace)

    stdout_file = open("stdout.log", 'w', 1)
    stderr_file = open("stderr.log", 'w', 1)

    return stdout_file, stderr_file

def setup_job_globals(owner: str, userspace: str, setspace: str, workspace: str, web_bind: str, job_id: str, kind: str, set: str) -> dict:
    """Create the globals dict for job execution"""
    if kind in ("service", "debug"):
        listener = pyops.ServiceListener(owner, job_id)
    else:
        listener = None

    return {
        "owner": owner,
        "userspace": userspace,
        "setspace": setspace,
        "workspace": workspace,
        "gitspace": os.path.join(files.project_root, files.conf_data()["git"]),
        "pkgspace": os.path.join(files.project_root, files.conf_runner()["pkg"], set),
        "jobsspace": os.path.join(files.project_root, files.conf_data()["jobs"]),
        "control": pyops.Control(owner, web_bind),
        "listener": listener,
        "__name__": "__main__"
    }

def setup_logging(log_level: int):
    """Configure logging handlers for stdout/stderr and return them for cleanup"""
    root = logging.getLogger()
    root.setLevel(log_level)

    info_handler = logging.StreamHandler(sys.stdout)
    info_handler.setLevel(logging.DEBUG)
    info_handler.addFilter(lambda r: r.levelno < logging.WARNING)
    root.addHandler(info_handler)

    warn_handler = logging.StreamHandler(sys.stderr)
    warn_handler.setLevel(logging.WARNING)
    root.addHandler(warn_handler)

    return info_handler, warn_handler

def execute_job_code(compiled_param, compiled_code, compiled_libs, glob: dict, job_type: str, job_id: str, q: Queue, max_retries: int = 0):
    """Execute code with appropriate retry/error handling based on job_type"""
    try:
        if job_type == "task":
            # Task jobs have retry logic
            for attempt in range(max_retries + 1):
                try:
                    if compiled_param is not None:
                        exec(compiled_param, glob)
                    for cl in compiled_libs:
                        exec(cl, glob)
                    exec(compiled_code, glob)
                    q.put({"id": job_id, "state": "done", "detail": None})
                    break
                except Exception:
                    error_msg = f"Failed after {max_retries} retries\n{traceback.format_exc()}"
                    print(error_msg, file=sys.stderr)  # Write to stderr for logging
                    sys.stdout.flush()
                    sys.stderr.flush()
                    if attempt < max_retries:
                        logger.warning(f"Task job {job_id} attempt {attempt + 1}/{max_retries + 1} failed, retrying in 60s")
                        time.sleep(60)
                    else:
                        q.put({"id": job_id, "state": "failed", "detail": error_msg})
                        break
        else:
            # Service and debug jobs - no retry
            if compiled_param is not None:
                exec(compiled_param, glob)
            for cl in compiled_libs:
                exec(cl, glob)
            exec(compiled_code, glob)
            q.put({"id": job_id, "state": "done", "detail": None})
    except Exception:
        error_msg = f"Error executing {job_type} job {job_id}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)  # Write to stderr for logging
        sys.stdout.flush()
        sys.stderr.flush()
        q.put({"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        if 'listener' in glob and glob['listener']:
            glob['listener'].close()

def run_debug_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], q: Queue, data_base: str, web_bind: str, venv_path: str, pkg_path: str, overrides: str | None = None, port: int | None = None):
    """Executes debug job in isolated workspace with file-based compilation."""
    userspace, setspace, workspace = setup_workspace(data_base, owner, set, job_id)

    logger.debug(f"{current_process().pid:<10}start debug job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.DEBUG)
    info_handler, warn_handler = None, None
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Terminate any existing debugpy adapter for the owner before starting new debug job
                terminate_debugpy_adapter(owner)

                # Ensure debugpy is installed early
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                subprocess.run([pip_path, 'install', '-q', 'debugpy'], check=True, capture_output=True)

                # Start debugpy adapter immediately
                await_debug = '#>AWAIT_DEBUG' in param
                debug_code = f"""
import debugpy
debugpy.log_to('.debug/adapter')
debugpy.listen(('0.0.0.0', {port}))"""
                if await_debug:
                    debug_code += """
debugpy.wait_for_client()"""
                exec(debug_code)

                # Apply overrides for debug kind
                if overrides:
                    override_dict = json.loads(overrides)
                    if f"{set}::{name}" in override_dict:
                        code = override_dict[f"{set}::{name}"]
                    for lib in libs:
                        if lib["filename"] in override_dict:
                            lib["code"] = override_dict[lib["filename"]]

                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Create scriptlet files for debug jobs to enable breakpoint verification in .debug/src/ subdirectories
                debug_src_dir = os.path.join(workspace, ".debug", "src")
                os.makedirs(debug_src_dir, exist_ok=True)

                # Create task directory for main set
                main_task_dir = os.path.join(debug_src_dir, set, "task")
                os.makedirs(main_task_dir, exist_ok=True)

                # Write param if present
                if param:
                    param_file = os.path.join(main_task_dir, "param.py")
                    with open(param_file, 'w') as f:
                        f.write(param)

                # Write main code
                code_file = os.path.join(main_task_dir, f"{name}.py")
                with open(code_file, 'w') as f:
                    f.write(code)

                # Write libs
                lib_files = []
                for lib in libs:
                    lib_set, lib_name = lib["filename"].split("::")
                    lib_dir = os.path.join(debug_src_dir, lib_set, "library")
                    os.makedirs(lib_dir, exist_ok=True)
                    lib_file = os.path.join(lib_dir, f"{lib_name}.py")
                    with open(lib_file, 'w') as f:
                        f.write(lib["code"])
                    lib_files.append(lib_file)

                # Compile code objects from files for accurate breakpoint mapping
                if param:
                    with open(param_file, 'r') as f:
                        compiled_param = compile(f.read(), param_file, 'exec')
                else:
                    compiled_param = None

                with open(code_file, 'r') as f:
                    compiled_code = compile(f.read(), code_file, 'exec')

                # Compile libs from files for debugger mapping
                compiled_libs = []
                for lib_file in lib_files:
                    with open(lib_file, 'r') as f:
                        compiled_libs.append(compile(f.read(), lib_file, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "debug", set)

                info_handler, warn_handler = setup_logging(logging.DEBUG)

                execute_job_code(compiled_param, compiled_code, compiled_libs, glob, "debug", job_id, q)
    except Exception:
        error_msg = f"Error executing debug job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        q.put({"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.close()
        stderr_file.close()
        root = logging.getLogger()
        if info_handler:
            root.removeHandler(info_handler)
        if warn_handler:
            root.removeHandler(warn_handler)
        logger.debug(f"{current_process().pid:<10}end debug job({job_id})")


def run_task_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], q: Queue, data_base: str, web_bind: str, venv_path: str, pkg_path: str, max_retries: int = 0):
    """Executes task job in isolated workspace with in-memory compilation and retry logic."""
    userspace, setspace, workspace = setup_workspace(data_base, owner, set, job_id)

    logger.debug(f"{current_process().pid:<10}start task job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)
    info_handler, warn_handler = None, None
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Compile code objects with custom filenames for accurate tracebacks
                if param:
                    param_filename = f"{set}/task/param.py"
                    compiled_param = compile(param, param_filename, 'exec')
                else:
                    compiled_param = None

                code_filename = f"{set}/task/{name}.py"
                compiled_code = compile(code, code_filename, 'exec')

                # Compile libs with generated subdirectory filenames for debugger mapping
                compiled_libs = []
                for lib in libs:
                    lib_name = lib["filename"].split('::')[1]
                    lib_filename = f"{set}/library/{lib_name}.py"
                    compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "task", set)

                info_handler, warn_handler = setup_logging(logging.INFO)

                execute_job_code(compiled_param, compiled_code, compiled_libs, glob, "task", job_id, q, max_retries)
    except Exception:
        error_msg = f"Error executing task job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        q.put({"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.close()
        stderr_file.close()
        root = logging.getLogger()
        if info_handler:
            root.removeHandler(info_handler)
        if warn_handler:
            root.removeHandler(warn_handler)
        logger.debug(f"{current_process().pid:<10}end task job({job_id})")


def run_service_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], q: Queue, data_base: str, web_bind: str, venv_path: str, pkg_path: str):
    """Executes service job in isolated workspace with in-memory compilation."""
    userspace, setspace, workspace = setup_workspace(data_base, owner, set, job_id)

    logger.debug(f"{current_process().pid:<10}start service job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)
    info_handler, warn_handler = None, None
    glob = {}
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Compile code objects with custom filenames for accurate tracebacks
                if param:
                    param_filename = f"{set}/task/param.py"
                    compiled_param = compile(param, param_filename, 'exec')
                else:
                    compiled_param = None

                code_filename = f"{set}/task/{name}.py"
                compiled_code = compile(code, code_filename, 'exec')

                # Compile libs with generated subdirectory filenames for debugger mapping
                compiled_libs = []
                for lib in libs:
                    lib_name = lib["filename"].split('::')[1]
                    lib_filename = f"{set}/library/{lib_name}.py"
                    compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "service", set)

                info_handler, warn_handler = setup_logging(logging.INFO)

                execute_job_code(compiled_param, compiled_code, compiled_libs, glob, "service", job_id, q)
    except Exception:
        error_msg = f"Error executing service job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        q.put({"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.close()
        stderr_file.close()
        root = logging.getLogger()
        if info_handler:
            root.removeHandler(info_handler)
        if warn_handler:
            root.removeHandler(warn_handler)
        logger.debug(f"{current_process().pid:<10}end service job({job_id})")

def run(job_id: str, owner: str, set: str, name: str, kind: str, param: str, code: str, libs: list[dict[str, str]], q: Queue, data_base: str, web_bind: str, venv_path: str, pkg_path: str, max_retries: int = 0, overrides: str | None = None, port: int | None = None):
    """Executes job in isolated workspace by dispatching to appropriate handler based on kind."""
    if kind == "debug":
        if port is None:
            raise ValueError("Port must be provided for debug jobs")
        run_debug_job(job_id, owner, set, name, param, code, libs, q, data_base, web_bind, venv_path, pkg_path, overrides, port)
    elif kind == "task":
        run_task_job(job_id, owner, set, name, param, code, libs, q, data_base, web_bind, venv_path, pkg_path, max_retries)
    elif kind == "service":
        run_service_job(job_id, owner, set, name, param, code, libs, q, data_base, web_bind, venv_path, pkg_path)
    else:
        raise ValueError(f"Unknown job kind: {kind}")

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
                            session.commit()
                            logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={next_run} for {s.id} at {datetime.now()}")
                elif not lib_runner.find(s.owner, s.set, s.name, "service", ["todo", "dequeued", "running", "cancel", "done"]) and \
                     len(lib_runner.find(s.owner, s.set, s.name, "service", ["failed"])) < 3:
                    logger.debug(f"schedule: enqueuing service {s.set}::{s.name} at {now}")
                    lib_runner.enqueue(s.set, s.name, s.owner, "service", s.drop_done, s.param, s.max_retries, None)
                    schedule_obj = session.get(Schedule, s.id)
                    old_last = schedule_obj.last
                    schedule_obj.last = now
                    logger.debug(f"schedule: before commit, session ID {id(session)}, last {old_last} to {now} for service {s.id} at {datetime.now()}")
                    session.commit()
                    logger.debug(f"schedule: after commit, session ID {id(session)}, committed last={now} for service {s.id} at {datetime.now()}")
        logger.debug(f"schedule: completed at {datetime.now()}")

def main(data_base: str, web_bind: str, venv_path: str, pkg_path: str, env):
    import time

    from app.lib import files
    log_conf = files.conf_log()
    level_str = log_conf["loglevel"]
    level = logging.DEBUG if level_str.lower() == "debug" else logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    if "info" in log_conf:
        info_path = log_conf["info"]
        os.makedirs(os.path.dirname(info_path), exist_ok=True)
        handlers.append(logging.FileHandler(info_path))
    # Add stderr handler for warnings
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    handlers.append(stderr_handler)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=handlers
    )
    if "info" in log_conf:
        logger.info("Runner logging configured: level=%s info=%s", level_str, info_path)
    else:
        logger.info("Runner logging configured: level=%s (no file logging)", level_str)

    os.environ = env
    killer = GracefulKiller()

    q = Queue()

    # restore jobs which were taken out of queue but not executed due to crash
    handleOddJobs()

    # purge leftover service jobs at startup to ensure clean servicing
    purgeLeftoverServiceJobs()

    # clean up drop_done jobs that have been done for 60+ seconds
    cleanup_drop_done_jobs()

    processes: dict[int, Process] = {}

    last_pull_time = 0
    pull_interval = git.get_git_pull_interval()

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
            while not q.empty():
                msg = q.get()
                logger.debug(f"main: processing queue msg {msg}")
                finishJob(msg["id"], msg["state"], msg["detail"])

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
                                port = validate_debug_prerequisites(job_id, owner)
                            except Exception as e:
                                finishJob(job_id, "failed", str(e))
                                continue

                        libs = []

                        override_dict = json.loads(overrides) if overrides else None

                        if param:
                            param = process_imports(param, set, kind, libs, override_dict)
                        code = process_imports(code, set, kind, libs, override_dict)

                        logger.debug(f"Run job {job_id}")
                        p = Process(target=run, name=f"{set}::{name}", kwargs={"job_id": job_id, "owner": owner, "set": set, "name": name, "kind": kind, "param": param, "code": code, "libs": libs, "overrides": overrides, "q": q, "data_base": data_base, "web_bind": web_bind, "venv_path": venv_path, "pkg_path": pkg_path, "max_retries": max_retries, "port": port})
                        p.start()

                        if kind == "debug":
                            adapter_up = False
                            for attempt in range(50):  # 5 seconds max
                                if not p.is_alive():
                                    logger.warning("Job process died before debugpy adapter appeared for job %s", job_id)
                                    break
                                if has_debugpy_adapter(port):
                                    adapter_up = True
                                    logger.debug("Debugpy adapter is up for job %s", job_id)
                                    break
                                time.sleep(0.1)

                            if not adapter_up:
                                if p.is_alive():
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
        except Exception as e:
            logger.exception("Critical error in main loop iteration %d, continuing: %s", loop_count, e)
            time.sleep(1)  # Brief pause before retry to avoid tight loops on persistent errors

    # join running processes for graceful shutdown with timeout/kill logic
    for pid in list(processes.keys()):
        p = processes[pid]
        if p.is_alive:
            # Get job info for termination
            with Session(get_db_engine()) as session:
                job = session.query(Job).filter_by(pid=pid).first()
                is_debug = job.kind == "debug" if job else False
                owner = job.owner if job else None

            logger.debug(f"Terminating process tree {pid} on shutdown")
            finishJob(job_id, "cancelling", "shutdown")
            p.join(timeout=1.0)

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
