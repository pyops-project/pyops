"""Job execution utilities shared between runner.py and job_runner.py."""
import signal
import os
import re
import subprocess
import time
import json
import logging

import psutil
import zmq
import msgpack

logger = logging.getLogger(__name__)

def kill_proc_tree(processes: list, sig=signal.SIGTERM):
    """Kill all processes in the list using specified signal.

    Prefer `kill_proc_group` for job-process termination — it covers the
    whole tree atomically without the children-orphaned-before-we-enumerate
    race that `get_process_tree` has (`psutil.Process.children` returns
    empty if the parent died first, leaking subprocesses to PID 1).
    """
    for proc in processes:
        try:
            os.kill(proc.pid, sig)
        except OSError:
            pass  # Process already dead


def kill_proc_group(pgid: int, sig=signal.SIGTERM) -> None:
    """Send `sig` to every process in process-group `pgid`.

    Jobs are spawned with `preexec_fn=os.setsid` so the job's PID equals
    its PGID, and every subprocess the job spawns (debugpy adapter,
    gdbserver, …) inherits that PGID. One `killpg` syscall reaches them
    all atomically — no race window where the parent dies first and
    children re-parent to PID 1 holding sockets open.

    Silently ignores `ProcessLookupError` (group already gone) and
    `PermissionError` (signal not deliverable). Other `OSError`s
    propagate."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def proc_group_alive(pgid: int) -> bool:
    """True iff at least one process in `pgid`'s group still exists.

    Implemented via `killpg(0)` which is a no-op signal that returns
    success if any process in the group can be signalled. The check is
    cheap (one syscall) and atomic w.r.t. the kernel's group table."""
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def get_process_tree(pid: int) -> list[psutil.Process]:
    """Get the process tree for a given PID.

    Beware the orphan-race: if `pid`'s process dies between assignment
    and this call, children re-parent to PID 1 and disappear from this
    traversal. Use `kill_proc_group` for kill paths instead — it covers
    them via PGID."""
    try:
        parent = psutil.Process(pid)
        tree = [parent]
        tree.extend(parent.children(recursive=True))
        return tree
    except psutil.NoSuchProcess:
        return []

def install_git_repo(pip_path: str, repo_dir: str, stdout_file, stderr_file, upgrade: bool = False):
    """Install git repo as editable package.

    The `.pth` file pip writes here is picked up on the next interpreter
    start — by design, `job_setup.py` `os.execv`s into `job_runner.py` after
    all installs are done so the user-code interpreter sees a complete
    `sys.path`.

    `upgrade=False` (the default, used by per-job installs): plain
    `pip install -e`. If the package is already installed at any version,
    pip says "already satisfied" and returns in milliseconds — no index
    lookup, no network round-trip. This makes second+ job spawns fast.

    `upgrade=True` (used by the operator-triggered "Update Dependencies"
    flow): adds `--upgrade --upgrade-strategy eager` so pip re-resolves
    against the index and pulls any newer commit's setup.py changes.
    """
    cmd = [pip_path, 'install', '--no-cache-dir']
    if upgrade:
        cmd += ['--upgrade', '--upgrade-strategy', 'eager']
    cmd += ['-e', repo_dir]
    result = subprocess.run(
        cmd,
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

def process_install_lines(lines, venv_path: str, pkg_path: str, set: str, job_id: str, stdout_file, stderr_file, upgrade: bool = False):
    """Process #! lines for installs; uses env prop + CWD for shebang/path
    resolution. The caller (process_code_installs) must hold
    `sets.set_install_lock(set)` for the duration — pip has no venv-wide
    lock and concurrent jobs in the same set would otherwise corrupt
    `site-packages` or race on `git clone` into the same target dir.

    `upgrade=False` (per-job default): plain `pip install <pkg>` and
    no `git pull` on existing repos. If the package or repo is already
    in the venv, pip / the existing checkout are reused as-is — no index
    lookups, no network round-trips, milliseconds per directive. The
    explicit "Update Dependencies" set-level action is the only path that
    refreshes versions.

    `upgrade=True` (Update Dependencies path): pip with `--upgrade
    --upgrade-strategy eager`, plus `git pull` on existing repos. Does the
    network round-trip per directive.

    For pinned pip directives (`#!<pkg>==<ver>`), the version constraint
    is in the directive text — pip honours it whether `upgrade` is true or
    false. For git directives with a branch (`#!<url> <branch>`), the
    branch is `git checkout`-ed even on the per-job path so a scriptlet
    that switches branches between runs is honoured; only the `git pull`
    that would advance to the upstream tip is the upgrade-only step.
    """
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
                # Mirrors app/pyops/set_update_deps.task.py::_install_git
                # so both paths land in the same directory. Strip a trailing
                # `.git` only — the package is reached via `pip install -e`
                # which resolves the name from setup.py/pyproject.toml, so
                # the directory name doesn't need to be a Python identifier.
                repo_name = os.path.basename(arg1.rstrip('/'))
                if repo_name.endswith('.git'):
                    repo_name = repo_name[:-4]
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
                    if upgrade:
                        pull_result = subprocess.run(
                            ['git', '-C', repo_dir, 'pull'],
                            stdout=stdout_file, stderr=stderr_file, text=True, cwd=repo_dir
                        )
                        if pull_result.returncode != 0:
                            raise Exception(f"git pull in {repo_dir} failed: {pull_result.stderr}")
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                install_git_repo(pip_path, repo_dir, stdout_file, stderr_file, upgrade=upgrade)
            else:
                # Skip if it looks like a library directive (starts with { or contains ::)
                if rest.startswith('{') or '::' in rest:
                    continue
                # Split rest into pip install arguments
                args = rest.split()
                pip_path = os.path.join(venv_path, set, 'bin', 'pip')
                cmd = [pip_path, 'install', '--no-cache-dir']
                if upgrade:
                    cmd += ['--upgrade', '--upgrade-strategy', 'eager']
                cmd += args
                result = subprocess.run(cmd, stdout=stdout_file, stderr=stderr_file, text=True, check=True)

    stdout_file.flush()
    stderr_file.flush()
    return lines

def _has_install_directives(text: str) -> bool:
    """True if `text` has any `#!`-prefixed line that is a real install
    directive (pip pkg or git url) — not a library import (`#!{set::name}`)
    which is handled by `process_imports`. Used to short-circuit
    `process_code_installs` when there's no install work to do — the
    common case for jobs without directives — so we skip both the lock
    acquisition and the per-line subprocess setup entirely."""
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith('#!'):
            continue
        rest = stripped[2:].lstrip()
        if rest.startswith('{') or '::' in rest:
            continue
        if rest:
            return True
    return False


def process_code_installs(param: str, code: str, libs: list, venv_path: str, pkg_path: str, set: str, job_id: str, stdout_file, stderr_file, upgrade: bool = False) -> tuple:
    """Process #!pip / #!git installs in param, libs, and code, all under
    one per-set bootstrap flock when there is anything to do.

    Holding the lock across the whole call (not per-`process_install_lines`
    invocation) gives the job *job-level atomicity*: another job in the same
    set cannot slip between this job's param-install and libs-install and
    swap a package version underneath. Different sets remain parallel.

    Short-circuits to a no-op (no lock, no subprocess) when none of the
    inputs contain a real install directive — the common case for jobs
    that just `import` from already-installed packages.

    `upgrade=False` (per-job): no `--upgrade` flag, no `git pull`. Already-
    installed packages and existing repos are reused as-is. The "Update
    Dependencies" set-level action passes `upgrade=True`.
    """
    # Cheap pre-check: skip the lock entirely when nothing to do.
    have_work = (
        (param and _has_install_directives(param))
        or any(_has_install_directives(lib.get("code", "")) for lib in libs)
        or _has_install_directives(code)
    )
    if not have_work:
        return param, code, libs

    # Imported locally to avoid pulling `app.lib.sets` (which imports
    # `app.lib.git`) at module load time from contexts that don't have
    # that subtree on sys.path (e.g. minimal job_runner setups in tests).
    from app.lib.sets import set_install_lock

    with set_install_lock(set):
        if param:
            param_lines = param.splitlines(keepends=True)
            new_param_lines = process_install_lines(param_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file, upgrade=upgrade)
            param = ''.join(new_param_lines)

        new_libs = []
        for lib in libs:
            l_code = lib["code"]
            l_lines = l_code.splitlines(keepends=True)
            new_l_lines = process_install_lines(l_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file, upgrade=upgrade)
            new_lib = {"code": ''.join(new_l_lines), "filename": lib["filename"]}
            new_libs.append(new_lib)
        libs = new_libs

        code_lines = code.splitlines(keepends=True)
        new_code_lines = process_install_lines(code_lines, venv_path, pkg_path, set, job_id, stdout_file, stderr_file, upgrade=upgrade)
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
