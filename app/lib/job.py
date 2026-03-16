"""Job execution utilities shared between runner.py and job_runner.py."""
import signal
import os
import re
import subprocess
import time
import json
import logging
from typing import Optional

import psutil
import zmq
import msgpack

from app.lib import files

logger = logging.getLogger(__name__)

def kill_proc_tree(processes: list, sig=signal.SIGTERM):
    """Kill all processes in the list using specified signal."""
    for proc in processes:
        try:
            os.kill(proc.pid, sig)
        except OSError:
            pass  # Process already dead

def get_process_tree(pid: int) -> list[psutil.Process]:
    """Get the process tree for a given PID."""
    try:
        parent = psutil.Process(pid)
        tree = [parent]
        tree.extend(parent.children(recursive=True))
        return tree
    except psutil.NoSuchProcess:
        return []

def get_debug_port(owner: str) -> int:
    """Get the debug port for an owner from config."""
    users = files.conf_users()
    user_conf = users.get(owner, {})
    port = user_conf.get("debug_port")
    if not port or not isinstance(port, int) or port < 1025 or port > 65535:
        raise Exception(f"Invalid or missing debug_port for user {owner}")
    return port

def has_debugpy_adapter(port: int) -> bool:
    """Check if a debugpy adapter is running on the given port."""
    return get_debugpy_adapter_pid(port) is not None

def get_debugpy_adapter_pid(port: int) -> Optional[int]:
    """Get the PID of the debugpy adapter process for a given port."""
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

def validate_debug_prerequisites(owner: str) -> int:
    """Validate debug prerequisites and return the debug port."""
    return get_debug_port(owner)

def terminate_debugpy_adapter(owner: str, port: int):
    """Terminate the debugpy adapter process for the given owner.
    
    Args:
        owner: The job owner (for logging only)
        port: The debug port (required in subprocess contexts)
    """
    logger.info("ENTER terminate_debugpy_adapter owner=%s port=%s", owner, port)
    if not port:
        logger.error("No port provided, cannot terminate debugpy adapter for owner %s", owner)
        return
    pid = get_debugpy_adapter_pid(port)
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("killed debugpy adapter PID %s", pid)
        except OSError as e:
            logger.warning("failed to kill debugpy adapter PID %s: %s", pid, e)
    else:
        logger.warning("no debugpy adapter process found for port %s", port)

def install_git_repo(pip_path: str, repo_dir: str, stdout_file, stderr_file):
    """Install git repo as editable package."""
    result = subprocess.run(
        [pip_path, 'install', '--no-cache-dir', '--upgrade', '--upgrade-strategy', 'eager', '-e', repo_dir],
        stdout=stdout_file, stderr=stderr_file, text=True, check=True
    )
    return result

def loadLib(set: str, name: str, kind: str, git_module):
    """Loads library code from Git (unstaged if kind=="debug" or "draft", else committed)."""
    logger.debug(f"loadLib(set={set}, name={name}, kind={kind})")
    repo = git_module.get_repo_path(set)
    files_list = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and f.endswith('.py')]
    if not files_list:
        raise Exception(f"Scriptlet {set}::{name} not existing")
    filename = files_list[0]
    type_ = git_module.infer_type_from_filename(filename)
    committed = kind not in ("debug", "draft")
    code = git_module.read_content(repo, name, type_, committed=committed)
    return code

def process_imports(text: str, current_set: str, kind: str, libs: list, overrides_dict: dict = None, seen_libs: set = None, depth: int = 0, git_module = None) -> str:
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
            if not n:
                continue
            lib_key = f"{g}::{n}"
            if lib_key in seen_libs:
                continue
            seen_libs.add(lib_key)
            try:
                if overrides_dict and lib_key in overrides_dict:
                    lib_code = overrides_dict[lib_key]
                else:
                    if git_module is None:
                        from app.lib import git
                        git_module = git
                    lib_code = loadLib(g, n, kind, git_module)
                # Recurse sub-deps
                lib_code = process_imports(lib_code, g, kind, libs, overrides_dict, seen_libs, depth + 1, git_module)
                libs.append({"code": lib_code, "filename": lib_key})
            except Exception as e:
                logger.error(f"Failed to load library {lib_key}: {e}")
                raise
    return text

def process_install_lines(lines, venv_path: str, pkg_path: str, set: str, job_id: str, stdout_file, stderr_file):
    """Process #! lines for installs; uses env prop + CWD for shebang/path resolution."""
    venv_activate = os.path.join(venv_path, set, 'bin', 'activate')
    # Bootstrap: Upgrade core packaging tools to ensure proper dependency resolution
    # and help newer venv packages override any older system-level versions
    subprocess.run(
        f". {venv_activate} && pip install --upgrade pip setuptools wheel",
        shell=True, check=True, stdout=stdout_file, stderr=stderr_file
    )
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
                    result = subprocess.run(
                        ['git'] + clone_args, stdout=stdout_file, stderr=stderr_file, text=True,
                        cwd=os.path.join(pkg_path, set)
                    )
                    if result.returncode != 0:
                        raise Exception(f"git clone {arg1} failed: {result.stderr}")
                else:
                    if arg2:
                        ck_result = subprocess.run(
                            ['git', '-C', repo_dir, 'checkout', arg2],
                            stdout=stdout_file, stderr=stderr_file, text=True, cwd=repo_dir
                        )
                        if ck_result.returncode != 0:
                            raise Exception(f"git checkout {arg2} in {repo_dir} failed: {ck_result.stderr}")
                    pull_result = subprocess.run(
                        ['git', '-C', repo_dir, 'pull'],
                        stdout=stdout_file, stderr=stderr_file, text=True, cwd=repo_dir
                    )
                    if pull_result.returncode != 0:
                        raise Exception(f"git pull in {repo_dir} failed: {pull_result.stderr}")
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                install_git_repo(pip_path, repo_dir, stdout_file, stderr_file)
            else:
                # Skip if it looks like a library directive (starts with { or contains ::)
                if rest.startswith('{') or '::' in rest:
                    continue
                # Split rest into pip install arguments
                args = rest.split()
                # Use safe flags to avoid conflicts with system site-packages
                cmd = f". {venv_activate} && pip install --no-cache-dir --upgrade --upgrade-strategy eager {' '.join(args)}"
                result = subprocess.run(cmd, shell=True, stdout=stdout_file, stderr=stderr_file, text=True, check=True)

    stdout_file.flush()
    stderr_file.flush()
    return lines

def process_code_installs(param: str, code: str, libs: list, venv_path: str, pkg_path: str, set: str, job_id: str, stdout_file, stderr_file) -> tuple:
    """Process #!pip/#!git installs in param, code, and libs"""
    # Process param
    if param:
        param_lines = param.splitlines(keepends=True)
        new_param_lines = process_install_lines(param_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)
        param = ''.join(new_param_lines)

    # Process libs
    new_libs = []
    for lib in libs:
        l_code = lib["code"]
        l_lines = l_code.splitlines(keepends=True)
        new_l_lines = process_install_lines(l_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)
        new_lib = {"code": ''.join(new_l_lines), "filename": lib["filename"]}
        new_libs.append(new_lib)
    libs = new_libs

    # Process code
    code_lines = code.splitlines(keepends=True)
    new_code_lines = process_install_lines(code_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file)
    code = ''.join(new_code_lines)

    return param, code, libs

def send_completion(runner_sock_path: str, msg: dict):
    """Send job completion message to runner via ZMQ REQ/REP.

    Blocks until ACK received. No retry - IPC in same container is reliable.
    """
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect(f"ipc://{runner_sock_path}")
    try:
        packed = msgpack.packb(msg)
        socket.send(packed)
        socket.recv()  # Wait for ACK - blocks until received
        logger.debug(f"Sent completion for job {msg['id']}")
    except zmq.ZMQError as e:
        logger.error(f"Failed to send completion for job {msg['id']}: {e}")
    finally:
        socket.close()
        context.term()
