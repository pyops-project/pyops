"""`pyopsctl debug` — start/attach/stop debug jobs and read the configured
debug port.

The marquee feature is `debug start`, which extends what VSIX exposes:

  * VSIX hard-codes `param='#>AWAIT_DEBUG'`. pyopsctl defaults to
    **detached** debug (debugpy listens, doesn't wait), takes `--await`
    to inject the directive on demand, and takes `--param TEXT` /
    `--param-file PATH` so an arbitrary script-shaped param can be
    submitted.
  * `job.param` is treated as a multi-line Python script and may carry
    other `#`-prefixed directives. With `--await` the merge prepends
    `#>AWAIT_DEBUG\\n` so the user's directives and code stay intact on
    their own lines.

Endpoints used:
  PUT  /scriptlet/job/item   (kind=debug)
  GET  /scriptlet/job/item?id=…  (when --wait-running)
  GET  /debug/port
  GET  /scriptlet/job/cancel?id=…  (debug stop)
"""
import argparse
import os
import time
from typing import Iterable

from pyopsctl.client import Client
from pyopsctl.config import load
from pyopsctl.output import emit, emit_composite

AWAIT_DIRECTIVE = "#>AWAIT_DEBUG"


def _merge_debug_param(*, await_flag: bool, user_param: str | None) -> str:
    """Build the `job.param` string for a debug job.

    Param is treated as a multi-line Python script that may carry other
    `#`-prefixed directives, so when both `--await` and a user param are
    given we prepend the `#>AWAIT_DEBUG` directive on its own line
    (newline separator, not space) — the user's script-shaped param
    stays a valid script with its own directives intact.

    Truth table:
      | --await | --param          | result                                |
      |---------|------------------|---------------------------------------|
      | True    | None / ""        | "#>AWAIT_DEBUG"                       |
      | True    | "x = 1"          | "#>AWAIT_DEBUG\\nx = 1"               |
      | True    | "#!pip foo\\n…"  | "#>AWAIT_DEBUG\\n#!pip foo\\n…"       |
      | False   | "x = 1"          | "x = 1" (verbatim)                    |
      | False   | None / ""        | ""                                    |
    """
    body = user_param or ""
    if await_flag:
        return AWAIT_DIRECTIVE + ("\n" + body if body else "")
    return body


def _resolve_user_param(args) -> str | None:
    """Read --param TEXT or --param-file PATH (mutually exclusive)."""
    if args.param is not None and args.param_file is not None:
        raise SystemExit("pyopsctl: --param and --param-file are mutually exclusive")
    if args.param is not None:
        return args.param
    if args.param_file is not None:
        with open(args.param_file, encoding="utf-8") as f:
            return f.read()
    return None


def _build_overrides(overrides_dir: str | None, set_name: str) -> dict[str, str] | None:
    """Walk `overrides_dir/<set>/*.py` and assemble the overrides dict that
    matches VSIX `DebugService.buildOverrides`. Without `--overrides-dir`
    returns None — pyops then runs against the server-stored code only.

    Keys must satisfy pyops's `^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$` regex
    (see `app/routers/scriptlet/job.py:84`). The walk targets `*.py`
    only — `.html`/`.md`/`.js` overrides aren't part of the debug flow.
    """
    if not overrides_dir:
        return None
    set_dir = os.path.join(overrides_dir, set_name)
    if not os.path.isdir(set_dir):
        return {}
    result: dict[str, str] = {}
    for entry in sorted(os.listdir(set_dir)):
        if not entry.endswith(".py"):
            continue
        # Strip both `.task.py`, `.service.py`, etc. and plain `.py`.
        stem = entry.split(".", 1)[0]
        path = os.path.join(set_dir, entry)
        with open(path, encoding="utf-8") as f:
            result[f"{set_name}::{stem}"] = f.read()
    return result


def _poll_until_running(c: Client, job_id: str, *, timeout: float = 30.0,
                       interval: float = 0.5) -> dict:
    """Block until the debug job reaches state=running or terminal.

    Raises RuntimeError on terminal-without-running so the caller doesn't
    print a port number for a job that already failed.
    """
    deadline = time.monotonic() + timeout
    while True:
        job = c.get("/scriptlet/job/item", params={"id": job_id})
        state = (job or {}).get("state", "")
        if state == "running":
            return job
        if state in ("done", "failed", "cancelled", "remove", "removed"):
            raise RuntimeError(f"job {job_id} reached terminal state {state!r} before running")
        if time.monotonic() > deadline:
            raise RuntimeError(f"job {job_id} did not reach running within {timeout}s (last state: {state!r})")
        time.sleep(interval)


def _start(args) -> int:
    profile = load(args)
    user_param = _resolve_user_param(args)
    param = _merge_debug_param(await_flag=args.await_flag, user_param=user_param)
    overrides = _build_overrides(args.overrides_dir, args.set)

    body = {
        "set": args.set,
        "name": args.name,
        "kind": "debug",
        "param": param,
        "drop_done": False,
        "max_retries": 0,
    }
    if overrides is not None:
        body["overrides"] = overrides

    with Client(profile, timeout=args.timeout) as c:
        resp = c.put("/scriptlet/job/item", json_body=body)
        # The endpoint returns {"job": "<id>"} — runner.enqueue returns the job ID
        # string, not a Job model. See app/lib/runner.py:enqueue.
        job_id = (resp or {}).get("job")

        if not args.wait_running:
            emit(args, {"job_id": job_id, "set": args.set, "name": args.name,
                        "param": param, "awaiting": args.await_flag},
                 id_field="job_id",
                 human=f"debug job {job_id} enqueued ({args.set}::{args.name}, "
                       f"{'await' if args.await_flag else 'detached'})")
            return 0

        running_job = _poll_until_running(c, job_id)
        port_resp = c.get("/debug/port")
        port = (port_resp or {}).get("port")
        emit_composite(
            args,
            {"job_id": job_id, "state": running_job.get("state"),
             "port": port, "host": _host_of(profile.url)},
            meta={"server": profile.url, "profile": profile.name,
                  "endpoints": ["/scriptlet/job/item (PUT)", "/scriptlet/job/item (GET)", "/debug/port"]},
            human=f"debug job {job_id} running; attach to {_host_of(profile.url)}:{port}",
        )
        return 0


def _attach(args) -> int:
    profile = load(args)
    with Client(profile, timeout=args.timeout) as c:
        if args.wait_running:
            _poll_until_running(c, args.job_id)
        port_resp = c.get("/debug/port")
        port = (port_resp or {}).get("port")
        emit(args, {"job_id": args.job_id, "port": port, "host": _host_of(profile.url)},
             id_field="port",
             human=f"attach to {_host_of(profile.url)}:{port} (job {args.job_id})")
        return 0


def _port(args) -> int:
    profile = load(args)
    with Client(profile, timeout=args.timeout) as c:
        resp = c.get("/debug/port")
        port = (resp or {}).get("port")
        emit(args, {"port": port, "user": args.user or profile.user},
             id_field="port",
             human=str(port))
        return 0


def _stop(args) -> int:
    profile = load(args)
    with Client(profile, timeout=args.timeout) as c:
        c.get("/scriptlet/job/cancel", params={"id": args.job_id})
        emit(args, {"job_id": args.job_id, "state": "cancel"},
             id_field="job_id",
             human=f"debug job {args.job_id} cancel requested")
        return 0


def _host_of(url: str) -> str:
    """Strip scheme + port to get the bare hostname for the debugger
    client. Useful in the human output of `debug start --wait-running`."""
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    host = host.split(":", 1)[0]
    return host


def register(subparsers, parents):
    p = subparsers.add_parser("debug", help="start, attach, or stop debug jobs",
                              parents=parents)
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    start = sub.add_parser("start", help="enqueue a debug job (detached by default)",
                           parents=parents)
    start.add_argument("set")
    start.add_argument("name")
    start.add_argument("--await", dest="await_flag", action="store_true",
                       help="inject the #>AWAIT_DEBUG directive so the runner waits for a debugger client")
    start.add_argument("--param", metavar="TEXT", default=None,
                       help="param to pass to the scriptlet (multi-line accepted)")
    start.add_argument("--param-file", metavar="PATH", default=None,
                       help="read --param from a file (mutually exclusive with --param)")
    start.add_argument("--wait-running", action="store_true",
                       help="poll until state=running, then print the debug port")
    start.add_argument("--overrides-dir", metavar="DIR", default=None,
                       help="walk DIR/<set>/*.py to build overrides for in-flight debugging of local edits")
    start.set_defaults(func=_start)

    attach = sub.add_parser("attach", help="print the debug port for an already-running job",
                            parents=parents)
    attach.add_argument("job_id")
    attach.add_argument("--wait-running", action="store_true",
                        help="poll until the job reaches state=running before fetching the port")
    attach.set_defaults(func=_attach)

    port = sub.add_parser("port", help="print the configured debug port for the resolved user",
                          parents=parents)
    port.set_defaults(func=_port)

    stop = sub.add_parser("stop", help="cancel a debug job (alias for `job cancel` in debug context)",
                          parents=parents)
    stop.add_argument("job_id")
    stop.set_defaults(func=_stop)
