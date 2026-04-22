import signal, time, os, sys, re
import traceback
import subprocess
import json
from datetime import datetime, timedelta
from io import TextIOWrapper
import threading
from typing import Any

import zmq, msgpack
import psutil

# Add project root to sys.path for importing app modules in isolated venv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.lib import git
from app.lib import pyops
from app.lib.sets import ensure_set_environment
from app.lib import job as job_lib

import logging
from contextlib import redirect_stdout, redirect_stderr
logger = logging.getLogger(__name__)

_project_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

_conf_data = None
_conf_runner = None
_conf_users = None
_conf_web = None

def setup_workspace(data_base: str, set: str, job_id: str) -> tuple[str, str]:
    """Create and return workspace paths: (setspace, workspace)"""
    setspace = os.path.abspath(os.path.join(data_base, f"{set}"))
    workspace = os.path.abspath(os.path.join(setspace, f"{job_id}"))

    os.makedirs(setspace, exist_ok=True)
    os.makedirs(workspace, exist_ok=True)

    return setspace, workspace

def setup_execution_environment(venv_path: str, pkg_path: str, set: str, workspace: str, log_level: int) -> tuple[TextIOWrapper, TextIOWrapper]:
    """Change to workspace directory and return stdout/stderr files"""
    os.chdir(workspace)

    stdout_file = open("stdout.log", 'w', 1)
    stderr_file = open("stderr.log", 'w', 1)

    return stdout_file, stderr_file

def setup_job_globals(owner: str, setspace: str, workspace: str, web_bind: str, job_id: str, kind: str, set: str, name: str) -> dict:
    """Create the globals dict for job execution"""
    if kind in ("service", "debug"):
        listener = pyops.ServiceListener(owner, job_id)
    else:
        listener = None

    return {
        "user": owner,
        "set": set,
        "name": name,
        "setspace": setspace,
        "workspace": workspace,
        "gitspace": os.path.join(_project_root, _conf_data["git"]),
        "pkgspace": os.path.join(_project_root, _conf_runner["pkg"]),
        "jobsspace": os.path.join(_project_root, _conf_data["jobs"]),
        "control": pyops.Control(owner, web_bind),
        "listener": listener,
        "__name__": "__main__",
        "__builtins__": __builtins__
    }

def setup_logging(log_level: int, stdout_file, stderr_file):
    """Configure logging handlers for stdout/stderr and return them for cleanup"""
    root = logging.getLogger()
    root.setLevel(log_level)

    info_handler = logging.StreamHandler(stdout_file if stdout_file else sys.stdout)
    info_handler.setLevel(logging.DEBUG)
    info_handler.addFilter(lambda r: r.levelno < logging.WARNING)
    root.addHandler(info_handler)

    warn_handler = logging.StreamHandler(stderr_file if stderr_file else sys.stderr)
    warn_handler.setLevel(logging.WARNING)
    root.addHandler(warn_handler)

    return info_handler, warn_handler

def execute_job_code(param: str, compiled_code, compiled_libs, glob: dict, job_type: str, job_id: str, runner_sock_path: str, max_retries: int = 0):
    """Execute code with appropriate retry/error handling based on job_type"""
    try:
        if job_type == "task":
            # Task jobs have retry logic
            for attempt in range(max_retries + 1):
                try:
                    # Execute param before each attempt
                    if param:
                        exec(param, glob)
                    for cl in compiled_libs:
                        exec(cl, glob)
                    exec(compiled_code, glob)
                    job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "done", "detail": None})
                    break
                except Exception:
                    error_msg = f"Failed after {max_retries} retries\n{traceback.format_exc()}"
                    logger.error(error_msg)
                    if attempt < max_retries:
                        logger.warning(f"Task job {job_id} attempt {attempt + 1}/{max_retries + 1} failed, retrying in 60s")
                        time.sleep(60)
                    else:
                        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
                        break
        else:
            # Service and debug jobs - no retry
            # Execute param once before execution
            if param:
                exec(param, glob)
            for cl in compiled_libs:
                exec(cl, glob)
            exec(compiled_code, glob)
            job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "done", "detail": None})
    except Exception:
        error_msg = f"Error executing {job_type} job {job_id}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        if 'listener' in glob and glob['listener']:
            glob['listener'].close()

def run_debug_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str, overrides: str | None = None, port: int | None = None):
    """Executes debug job in isolated workspace with file-based compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]

    stdout_file, stderr_file = None, None
    info_handler, warn_handler = None, None

    try:
        # Step 1: Workspace setup
        setspace, workspace = setup_workspace(data_jobs, set, job_id)

        # Step 2: Execution environment setup
        stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.DEBUG)

        logger.debug(f"{os.getpid():<10}start debug job({job_id})")

        # Step 2.5: Framework setup BEFORE redirect (errors go to main stderr)
        # Terminate any existing debugpy adapter for the owner before starting new debug job
        job_lib.terminate_debugpy_adapter(owner, port)

        # Step 3: Main execution with redirected output
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                try:
                    # Ensure debugpy is installed early
                    pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                    subprocess.run([pip_path, 'install', 'debugpy'], check=True, stdout=stdout_file, stderr=stderr_file)

                    # Start debugpy adapter immediately
                    await_debug = '#>AWAIT_DEBUG' in param
                    debug_code = f"""import debugpy
debugpy.log_to('.debug/adapter')
debugpy.listen(('0.0.0.0', {port}))
"""
                    if await_debug:
                        debug_code += """
debugpy.wait_for_client()
debugpy.breakpoint()
"""
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
                    param, code, libs = job_lib.process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)
                    # Update libs with git module for process_imports
                    libs = libs

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
                    with open(code_file, 'r') as f:
                        compiled_code = compile(f.read(), code_file, 'exec')

                    # Compile libs from files for debugger mapping
                    compiled_libs = []
                    for lib_file in lib_files:
                        with open(lib_file, 'r') as f:
                            compiled_libs.append(compile(f.read(), lib_file, 'exec'))

                    glob = setup_job_globals(owner, setspace, workspace, web_bind, job_id, "debug", set, name)

                    info_handler, warn_handler = setup_logging(logging.DEBUG, stdout_file, stderr_file)

                    execute_job_code(param, compiled_code, compiled_libs, glob, "debug", job_id, runner_sock_path)

                finally:
                    # Remove logging handlers before exiting redirect scope
                    root = logging.getLogger()
                    if info_handler:
                        root.removeHandler(info_handler)
                    if warn_handler:
                        root.removeHandler(warn_handler)

    except subprocess.CalledProcessError as e:
        # Handle pip install failures (debugpy installation)
        error_msg = f"Install processing failed for debug job {job_id}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    except json.JSONDecodeError as e:
        # Handle JSON parsing errors (overrides parsing)
        error_msg = f"Invalid overrides for debug job {job_id}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    except Exception as e:
        # Catch-all for workspace/environment setup errors and any other unexpected errors
        error_msg = f"Error executing debug job {job_id}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    finally:
        # Cleanup: flush/close files
        if stdout_file:
            stdout_file.flush()
            stdout_file.close()
        if stderr_file:
            stderr_file.flush()
            stderr_file.close()
        logger.debug(f"{os.getpid():<10}end debug job({job_id})")

def run_draft_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str):
    """Executes draft job in isolated workspace with in-memory compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]

    stdout_file, stderr_file = None, None
    info_handler, warn_handler = None, None

    try:
        # Step 1: Workspace setup
        setspace, workspace = setup_workspace(data_jobs, set, job_id)

        # Step 2: Execution environment setup
        stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)

        logger.debug(f"{os.getpid():<10}start draft job({job_id})")

        # Step 3: Main execution with redirected output
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                try:
                    # Process installs w/ env prop.
                    param, code, libs = job_lib.process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)

                    # Compile code objects with custom filenames for accurate tracebacks
                    code_filename = f"{set}/task/{name}.py"
                    compiled_code = compile(code, code_filename, 'exec')

                    # Compile libs with generated subdirectory filenames for debugger mapping
                    compiled_libs = []
                    for lib in libs:
                        lib_name = lib["filename"].split('::')[1]
                        lib_filename = f"{set}/library/{lib_name}.py"
                        compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                    glob = setup_job_globals(owner, setspace, workspace, web_bind, job_id, "draft", set, name)

                    info_handler, warn_handler = setup_logging(logging.INFO, stdout_file, stderr_file)

                    execute_job_code(param, compiled_code, compiled_libs, glob, "draft", job_id, runner_sock_path)

                finally:
                    # Remove logging handlers before exiting redirect scope
                    root = logging.getLogger()
                    if info_handler:
                        root.removeHandler(info_handler)
                    if warn_handler:
                        root.removeHandler(warn_handler)

    except subprocess.CalledProcessError as e:
        # Handle pip/git install failures during process_code_installs
        error_msg = f"Install processing failed for draft job {job_id}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    except Exception as e:
        # Catch-all for workspace/environment setup errors and any other unexpected errors
        error_msg = f"Error executing draft job {job_id}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    finally:
        # Cleanup: flush/close files
        if stdout_file:
            stdout_file.flush()
            stdout_file.close()
        if stderr_file:
            stderr_file.flush()
            stderr_file.close()
        logger.debug(f"{os.getpid():<10}end draft job({job_id})")

def run_task_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str, max_retries: int = 0):
    """Executes task job in isolated workspace with in-memory compilation and retry logic."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]

    stdout_file, stderr_file = None, None
    info_handler, warn_handler = None, None

    try:
        # Step 1: Workspace setup
        setspace, workspace = setup_workspace(data_jobs, set, job_id)

        # Step 2: Execution environment setup
        stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)

        logger.debug(f"{os.getpid():<10}start task job({job_id})")

        # Step 3: Main execution with redirected output
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                try:
                    # Process installs w/ env prop.
                    param, code, libs = job_lib.process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)

                    # Compile code objects with custom filenames for accurate tracebacks
                    code_filename = f"{set}/task/{name}.py"
                    compiled_code = compile(code, code_filename, 'exec')

                    # Compile libs with generated subdirectory filenames for debugger mapping
                    compiled_libs = []
                    for lib in libs:
                        lib_name = lib["filename"].split('::')[1]
                        lib_filename = f"{set}/library/{lib_name}.py"
                        compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                    glob = setup_job_globals(owner, setspace, workspace, web_bind, job_id, "task", set, name)

                    info_handler, warn_handler = setup_logging(logging.INFO, stdout_file, stderr_file)

                    execute_job_code(param, compiled_code, compiled_libs, glob, "task", job_id, runner_sock_path, max_retries)

                finally:
                    # Remove logging handlers before exiting redirect scope
                    root = logging.getLogger()
                    if info_handler:
                        root.removeHandler(info_handler)
                    if warn_handler:
                        root.removeHandler(warn_handler)

    except subprocess.CalledProcessError as e:
        # Handle pip/git install failures during process_code_installs
        error_msg = f"Install processing failed for task job {job_id}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    except Exception as e:
        # Catch-all for workspace/environment setup errors and any other unexpected errors
        error_msg = f"Error executing task job {job_id}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    finally:
        # Cleanup: flush/close files
        if stdout_file:
            stdout_file.flush()
            stdout_file.close()
        if stderr_file:
            stderr_file.flush()
            stderr_file.close()
        logger.debug(f"{os.getpid():<10}end task job({job_id})")

def run_service_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str):
    """Executes service job in isolated workspace with in-memory compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]

    stdout_file, stderr_file = None, None
    info_handler, warn_handler = None, None

    try:
        # Step 1: Workspace setup
        setspace, workspace = setup_workspace(data_jobs, set, job_id)

        # Step 2: Execution environment setup
        stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)

        logger.debug(f"{os.getpid():<10}start service job({job_id})")

        # Step 3: Main execution with redirected output
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                try:
                    # Process installs w/ env prop.
                    param, code, libs = job_lib.process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)

                    # Compile code objects with custom filenames for accurate tracebacks
                    code_filename = f"{set}/task/{name}.py"
                    compiled_code = compile(code, code_filename, 'exec')

                    # Compile libs with generated subdirectory filenames for debugger mapping
                    compiled_libs = []
                    for lib in libs:
                        lib_name = lib["filename"].split('::')[1]
                        lib_filename = f"{set}/library/{lib_name}.py"
                        compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                    glob = setup_job_globals(owner, setspace, workspace, web_bind, job_id, "service", set, name)

                    info_handler, warn_handler = setup_logging(logging.INFO, stdout_file, stderr_file)

                    execute_job_code(param, compiled_code, compiled_libs, glob, "service", job_id, runner_sock_path)

                finally:
                    # Remove logging handlers before exiting redirect scope
                    root = logging.getLogger()
                    if info_handler:
                        root.removeHandler(info_handler)
                    if warn_handler:
                        root.removeHandler(warn_handler)

    except subprocess.CalledProcessError as e:
        # Handle pip/git install failures during process_code_installs
        error_msg = f"Install processing failed for service job {job_id}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    except Exception as e:
        # Catch-all for workspace/environment setup errors and any other unexpected errors
        error_msg = f"Error executing service job {job_id}\n{traceback.format_exc()}"
        logger.error(error_msg)
        job_lib.send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})

    finally:
        # Cleanup: flush/close files
        if stdout_file:
            stdout_file.flush()
            stdout_file.close()
        if stderr_file:
            stderr_file.flush()
            stderr_file.close()
        logger.debug(f"{os.getpid():<10}end service job({job_id})")

def main(job_id: str, owner: str, set: str, name: str, kind: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str, max_retries: int, overrides: str | None, port: int | None, conf_data: dict, conf_runner: dict, conf_users: dict, conf_web: dict):
    """Executes job in isolated workspace by dispatching to appropriate handler based on kind."""

    global _conf_data, _conf_runner, _conf_users, _conf_web
    _conf_data = conf_data
    _conf_runner = conf_runner
    _conf_users = conf_users
    _conf_web = conf_web

    if kind == "debug":
        if port is None:
            raise ValueError("Port must be provided for debug jobs")
        run_debug_job(job_id, owner, set, name, param, code, libs, runner_sock_path, overrides, port)
    elif kind == "draft":
        run_draft_job(job_id, owner, set, name, param, code, libs, runner_sock_path)
    elif kind == "task":
        run_task_job(job_id, owner, set, name, param, code, libs, runner_sock_path, max_retries)
    elif kind == "service":
        run_service_job(job_id, owner, set, name, param, code, libs, runner_sock_path)
    else:
        raise ValueError(f"Unknown job kind: {kind}")

if __name__ == "__main__":
    import json
    import sys
    
    # Top-level error handling for any uncaught exceptions
    try:
        if len(sys.argv) != 2:
            print("Usage: job_runner.py <json_kwargs>", file=sys.stderr)
            sys.exit(1)
        
        kwargs = json.loads(sys.argv[1])
        main(**kwargs)
    except ValueError as e:
        # Handle JSON parsing errors or invalid port values
        error_msg = f"Invalid arguments: {e}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Catch any other unhandled exceptions
        error_msg = f"Unhandled error in job_runner.py: {e}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        sys.exit(1)
