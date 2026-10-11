"""First phase of two-phase job execution.

Runs in the per-set venv. Performs all directive-driven installs (pip and
git) under the set's bootstrap flock, then `os.execv`s into `job_runner.py`
so the user-code interpreter starts *fresh* — `site.py` runs at startup and
ingests every `.pth` file written by `pip install -e <git-repo>`, making
editable packages importable from the very first user `import`.

Without this split, the running Python process never re-reads `.pth` files
after their creation: `pip install -e` in the same interpreter completes
on disk but `sys.path` stays stale → `ModuleNotFoundError` on first job
run, success only on retry once a fresh interpreter happens to spawn.

Same PID across both phases (via `os.execv`). The runner's
`processes[pid]`, `assignJob(pid)`, and signal delivery all keep
working without changes.
"""

import json
import os
import sys
import traceback

# Add project root to sys.path for importing app modules in isolated venv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.lib import files as files_lib
from app.lib import job as job_lib


_project_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _init_files_config(kwargs: dict) -> None:
    """Mirror `app.lib.files.Config` state from kwargs so any `lib/*` code
    reached during the install phase (notably `sets._bootstrap_lock_path`
    via `set_install_lock`) can call `files.conf_runner()` etc. — they were
    initialized in `runner.py` via `Config.init(path)` but that state does
    not survive subprocess spawn / `os.execv`."""
    raw = {
        "data": kwargs["conf_data"],
        "runner": kwargs["conf_runner"],
        "users": kwargs["conf_users"],
        "web": kwargs["conf_web"],
    }
    if kwargs.get("conf_log") is not None:
        raw["log"] = kwargs["conf_log"]
    files_lib.Config.init_from_dict(raw)


def _setup_workspace(data_base: str, setname: str, job_id: str) -> str:
    setspace = os.path.abspath(os.path.join(data_base, setname))
    workspace = os.path.abspath(os.path.join(setspace, job_id))
    os.makedirs(setspace, exist_ok=True)
    os.makedirs(workspace, exist_ok=True)
    return workspace


def _exec_runner(json_kwargs: str, workspace: str) -> None:
    """Replace this process with job_runner.py in the same venv. The fresh
    interpreter reads `site.py` at startup, picking up every `.pth` written
    above. Returns only on failure to exec (in which case the caller should
    report and exit non-zero).

    Kwargs go via a file in the workspace, not argv: the serialised job
    context (param + code + libs + VSIX overrides) can be hundreds of KB
    and Linux execve(2) refuses combined argv+env beyond ARG_MAX. Same
    reason `runner.py` pipes kwargs into our stdin instead of putting
    them in our argv. The file lives at `<workspace>/.kwargs.json` and
    gets removed with the rest of the workspace on drop_done."""
    venv_python = sys.executable
    runner_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'job_runner.py'))
    kwargs_path = os.path.join(workspace, '.kwargs.json')
    with open(kwargs_path, 'w') as f:
        f.write(json_kwargs)
    os.execv(venv_python, [venv_python, runner_path, kwargs_path])


def main(json_kwargs: str) -> None:
    kwargs = json.loads(json_kwargs)
    _init_files_config(kwargs)
    job_id = kwargs["job_id"]
    setname = kwargs["setname"]
    param = kwargs.get("param") or ""
    code = kwargs.get("code") or ""
    libs = kwargs.get("libs") or []
    runner_sock_path = kwargs["runner_sock_path"]
    conf_data = kwargs["conf_data"]
    conf_runner = kwargs["conf_runner"]

    data_jobs = os.path.join(_project_root, conf_data["jobs"])
    venv_path = os.path.join(_project_root, conf_runner["venv"])
    pkg_path = os.path.join(_project_root, conf_runner["pkg"])

    workspace = _setup_workspace(data_jobs, setname, job_id)
    os.chdir(workspace)

    # 'w' so the file starts empty; job_runner.py opens 'a' to append the
    # exec-phase output below the install logs preserved here.
    stdout_file = open("stdout.log", 'w', 1)
    stderr_file = open("stderr.log", 'w', 1)

    try:
        job_lib.process_code_installs(
            param, code, libs, venv_path, pkg_path, setname, job_id,
            stdout_file, stderr_file,
        )
    except Exception as exc:
        # Surface the install failure to the runner via the same ZMQ
        # completion channel job_runner.py uses, so the job is marked
        # failed promptly with a useful detail instead of waiting for
        # stale-PID detection.
        detail = f"Install processing failed for job {job_id}: {exc}\n{traceback.format_exc()}"
        try:
            stderr_file.write(detail)
            stderr_file.flush()
        except Exception:
            pass
        try:
            job_lib.send_completion(
                runner_sock_path,
                {"id": job_id, "state": "failed", "detail": detail},
                result_file=job_lib.result_file(workspace),
            )
        except Exception:
            pass
        stdout_file.close()
        stderr_file.close()
        sys.exit(1)
        return  # belt-and-braces if sys.exit is patched out in tests

    stdout_file.flush()
    stdout_file.close()
    stderr_file.flush()
    stderr_file.close()

    # Hand off to job_runner.py with the unmodified kwargs string.
    _exec_runner(json_kwargs, workspace)


if __name__ == "__main__":
    try:
        # kwargs come in via stdin, not argv — the runner pipes them in
        # because the serialised job context can exceed ARG_MAX. See
        # `runner.py`'s Popen block for the matching writer.
        main(sys.stdin.read())
    except Exception as exc:
        print(f"Unhandled error in job_setup.py: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)
