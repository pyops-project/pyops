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

import logging
from contextlib import redirect_stdout, redirect_stderr
logger = logging.getLogger(__name__)

_project_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

_conf_data = None
_conf_runner = None
_conf_users = None
_conf_web = None

def send_completion(runner_sock_path: str, msg: dict):
    """Send job completion message to runner via ZMQ REQ/REP."""
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect(f"ipc://{runner_sock_path}")
    try:
        for attempt in range(3):
            try:
                packed = msgpack.packb(msg)
                socket.send(packed)
                reply = socket.recv()  # Wait for ACK
                logger.debug(f"Sent completion for job {msg['id']}")
                return
            except zmq.ZMQError as e:
                if attempt < 2:
                    logger.warning(f"Send attempt {attempt + 1} failed for job {msg['id']}: {e}")
                    time.sleep(0.1)
                else:
                    logger.error(f"Failed to send completion for job {msg['id']} after 3 attempts: {e}")
    finally:
        socket.close()
        context.term()

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
    user_conf = _conf_users.get(owner, {})
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



def get_debug_port(owner: str) -> int:
    users = _conf_users
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

def install_git_repo(pip_path: str, repo_dir: str):
    """Install git repo as editable package."""
    result = subprocess.run([pip_path, 'install', '--no-cache-dir', '--upgrade', '--upgrade-strategy', 'eager', '-e', repo_dir], capture_output=False, text=True, check=True)
    return result

def loadLib(set: str, name: str, kind: str):
    """Loads library code from Git (unstaged if kind=="debug" or "draft", else committed)."""
    logger.debug(f"loadLib(set={set}, name={name}, kind={kind})")
    repo = git.get_repo_path(set)
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise Exception(f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    type_ = git.infer_type_from_filename(filename)
    committed = kind not in ("debug", "draft")
    code = git.read_content(repo, name, type_, committed=committed)
    return code

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
    venv_activate = os.path.join(venv_path, set, 'bin', 'activate')
    # Bootstrap: Upgrade core packaging tools to ensure proper dependency resolution
    # and help newer venv packages override any older system-level versions
    subprocess.run(f". {venv_activate} && pip install --upgrade pip setuptools wheel", shell=True, check=True)
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
                    sys.stderr.write(result.stderr)
                    sys.stderr.flush()
                    if result.returncode != 0:
                        raise Exception(f"git clone {arg1} failed: {result.stderr}")
                else:
                    if arg2:
                        ck_result = subprocess.run(['git', '-C', repo_dir, 'checkout', arg2], capture_output=True, text=True, cwd=repo_dir)
                        sys.stderr.write(ck_result.stderr)
                        sys.stderr.flush()
                        if ck_result.returncode != 0:
                            raise Exception(f"git checkout {arg2} in {repo_dir} failed: {ck_result.stderr}")
                    pull_result = subprocess.run(['git', '-C', repo_dir, 'pull'], capture_output=True, text=True, cwd=repo_dir)
                    sys.stderr.write(pull_result.stderr)
                    sys.stderr.flush()
                    if pull_result.returncode != 0:
                        raise Exception(f"git pull in {repo_dir} failed: {pull_result.stderr}")
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                install_git_repo(pip_path, repo_dir)
            else:
                # Skip if it looks like a library directive (starts with { or contains ::)
                if rest.startswith('{') or '::' in rest:
                    continue
                # Split rest into pip install arguments
                args = rest.split()
                # Use safe flags to avoid conflicts with system site-packages
                cmd = f". {venv_activate} && pip install --no-cache-dir --upgrade --upgrade-strategy eager {' '.join(args)}"
                result = subprocess.run(cmd, shell=True, stdout=sys.stdout, stderr=sys.stderr, text=True, check=True)
                sys.stdout.flush()
                sys.stderr.flush()
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
    userspace = os.path.abspath(os.path.join(data_base, f"{owner}"))
    setspace = os.path.abspath(os.path.join(userspace, f"{set}"))
    workspace = os.path.abspath(os.path.join(setspace, f"{job_id}"))

    if not os.path.exists(userspace):
        os.makedirs(userspace)

    if not os.path.exists(setspace):
        os.makedirs(setspace)

    if not os.path.exists(workspace):
        os.makedirs(workspace)

    return userspace, setspace, workspace

def setup_execution_environment(venv_path: str, pkg_path: str, set: str, workspace: str, log_level: int) -> tuple[TextIOWrapper, TextIOWrapper]:
    """Setup sys.path, change to workspace directory, and return stdout/stderr files"""
    site_packages = os.path.join(venv_path, set, 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages')
    sys.path.insert(0, os.path.join(pkg_path, set))
    sys.path.insert(0, site_packages)

    os.chdir(workspace)

    stdout_file = open("stdout.log", 'w', 1)
    stderr_file = open("stderr.log", 'w', 1)

    return stdout_file, stderr_file

def setup_job_globals(owner: str, userspace: str, setspace: str, workspace: str, web_bind: str, job_id: str, kind: str, set: str, name: str) -> dict:
    """Create the globals dict for job execution"""
    if kind in ("service", "debug"):
        listener = pyops.ServiceListener(owner, job_id)
    else:
        listener = None

    return {
        "owner": owner,
        "set": set,
        "name": name,
        "userspace": userspace,
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
                    send_completion(runner_sock_path, {"id": job_id, "state": "done", "detail": None})
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
                        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
                        break
        else:
            # Service and debug jobs - no retry
            # Execute param once before execution
            if param:
                exec(param, glob)
            for cl in compiled_libs:
                exec(cl, glob)
            exec(compiled_code, glob)
            send_completion(runner_sock_path, {"id": job_id, "state": "done", "detail": None})
    except Exception:
        error_msg = f"Error executing {job_type} job {job_id}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)  # Write to stderr for logging
        sys.stdout.flush()
        sys.stderr.flush()
        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
    finally:
        if 'listener' in glob and glob['listener']:
            glob['listener'].close()

def run_debug_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str, overrides: str | None = None, port: int | None = None):
    """Executes debug job in isolated workspace with file-based compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]
    userspace, setspace, workspace = setup_workspace(data_jobs, owner, set, job_id)

    logger.debug(f"{os.getpid():<10}start debug job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.DEBUG)
    info_handler, warn_handler = None, None
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Terminate any existing debugpy adapter for the owner before starting new debug job
                terminate_debugpy_adapter(owner)

                # Ensure debugpy is installed early
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                subprocess.run([pip_path, 'install', 'debugpy'], check=True, capture_output=False)

                # Start debugpy adapter immediately
                await_debug = '#>AWAIT_DEBUG' in param
                debug_code = f"""
import debugpy
debugpy.log_to('.debug/adapter')
debugpy.listen(('0.0.0.0', {port}))"""
                if await_debug:
                    debug_code += """
debugpy.wait_for_client()
debugpy.breakpoint()"""
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
                with open(code_file, 'r') as f:
                    compiled_code = compile(f.read(), code_file, 'exec')

                # Compile libs from files for debugger mapping
                compiled_libs = []
                for lib_file in lib_files:
                    with open(lib_file, 'r') as f:
                        compiled_libs.append(compile(f.read(), lib_file, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "debug", set, name)

                info_handler, warn_handler = setup_logging(logging.DEBUG)

                execute_job_code(param, compiled_code, compiled_libs, glob, "debug", job_id, runner_sock_path)
    except Exception:
        error_msg = f"Error executing debug job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
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
        logger.debug(f"{os.getpid():<10}end debug job({job_id})")


def run_draft_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str):
    """Executes draft job in isolated workspace with in-memory compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]
    userspace, setspace, workspace = setup_workspace(data_jobs, owner, set, job_id)

    logger.debug(f"{os.getpid():<10}start draft job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)
    info_handler, warn_handler = None, None
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Compile code objects with custom filenames for accurate tracebacks
                code_filename = f"{set}/task/{name}.py"
                compiled_code = compile(code, code_filename, 'exec')

                # Compile libs with generated subdirectory filenames for debugger mapping
                compiled_libs = []
                for lib in libs:
                    lib_name = lib["filename"].split('::')[1]
                    lib_filename = f"{set}/library/{lib_name}.py"
                    compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "draft", set, name)

                info_handler, warn_handler = setup_logging(logging.INFO)

                execute_job_code(param, compiled_code, compiled_libs, glob, "draft", job_id, runner_sock_path)
    except Exception:
        error_msg = f"Error executing draft job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
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
        logger.debug(f"{os.getpid():<10}end draft job({job_id})")


def run_task_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str, max_retries: int = 0):
    """Executes task job in isolated workspace with in-memory compilation and retry logic."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]
    userspace, setspace, workspace = setup_workspace(data_jobs, owner, set, job_id)

    logger.debug(f"{os.getpid():<10}start task job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)
    info_handler, warn_handler = None, None
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Compile code objects with custom filenames for accurate tracebacks
                code_filename = f"{set}/task/{name}.py"
                compiled_code = compile(code, code_filename, 'exec')

                # Compile libs with generated subdirectory filenames for debugger mapping
                compiled_libs = []
                for lib in libs:
                    lib_name = lib["filename"].split('::')[1]
                    lib_filename = f"{set}/library/{lib_name}.py"
                    compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "task", set, name)

                info_handler, warn_handler = setup_logging(logging.INFO)

                execute_job_code(param, compiled_code, compiled_libs, glob, "task", job_id, runner_sock_path, max_retries)
    except Exception:
        error_msg = f"Error executing task job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
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
        logger.debug(f"{os.getpid():<10}end task job({job_id})")


def run_service_job(job_id: str, owner: str, set: str, name: str, param: str, code: str, libs: list[dict[str, str]], runner_sock_path: str):
    """Executes service job in isolated workspace with in-memory compilation."""
    data_jobs = os.path.join(_project_root, _conf_data["jobs"])
    venv_path = os.path.join(_project_root, _conf_runner["venv"])
    pkg_path = os.path.join(_project_root, _conf_runner["pkg"])
    web_bind = _conf_web["bind"]
    userspace, setspace, workspace = setup_workspace(data_jobs, owner, set, job_id)

    logger.debug(f"{os.getpid():<10}start service job({job_id})")

    stdout_file, stderr_file = setup_execution_environment(venv_path, pkg_path, set, workspace, logging.INFO)
    info_handler, warn_handler = None, None
    glob = {}
    try:
        with redirect_stdout(stdout_file):
            with redirect_stderr(stderr_file):
                # Process installs w/ env prop.
                param, code, libs = process_code_installs(param, code, libs, venv_path, pkg_path, set, job_id)

                # Compile code objects with custom filenames for accurate tracebacks
                code_filename = f"{set}/task/{name}.py"
                compiled_code = compile(code, code_filename, 'exec')

                # Compile libs with generated subdirectory filenames for debugger mapping
                compiled_libs = []
                for lib in libs:
                    lib_name = lib["filename"].split('::')[1]
                    lib_filename = f"{set}/library/{lib_name}.py"
                    compiled_libs.append(compile(lib["code"], lib_filename, 'exec'))

                glob = setup_job_globals(owner, userspace, setspace, workspace, web_bind, job_id, "service", set, name)

                info_handler, warn_handler = setup_logging(logging.INFO)

                execute_job_code(param, compiled_code, compiled_libs, glob, "service", job_id, runner_sock_path)
    except Exception:
        error_msg = f"Error executing service job {job_id}\n{traceback.format_exc()}"
        stderr_file.write(error_msg)
        stderr_file.flush()
        send_completion(runner_sock_path, {"id": job_id, "state": "failed", "detail": error_msg})
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
    if len(sys.argv) != 2:
        sys.exit(1)
    kwargs = json.loads(sys.argv[1])
    main(**kwargs)