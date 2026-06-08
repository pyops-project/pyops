"""`pyopsctl debug` — start/attach/inspect/step/stop debug jobs.

pyops itself no longer injects a debugger; the bootstrap is the client's
job. pyopsctl composes a remote-pdb bootstrap into the job's `param`,
leases a TCP port from the profile's `debug_ports` pool, and (on attach)
spawns a session daemon that holds one connection to the remote pdb
listener for the lifetime of the debug session.

Architecture and design rationale live in
`docs/insights_ctl.md` § "Interactive debug — session daemon".
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
from typing import Any

from pyopsctl import debug_format
from pyopsctl import ports
from pyopsctl.client import Client, HTTPError
from pyopsctl.config import load
from pyopsctl.output import emit, emit_composite, emit_error
from pyopsctl.overrides import build_overrides
from pyopsctl.session import client as session_client
from pyopsctl.session import daemon as session_daemon
from pyopsctl.session import paths as session_paths
from pyopsctl.session import spawn as session_spawn


# ---------------------------------------------------------------------------
# Param composition
# ---------------------------------------------------------------------------


PDB_BOOTSTRAP_TEMPLATE = """\
#!pip remote-pdb
from remote_pdb import RemotePdb
RemotePdb({host!r}, {port}).set_trace()
"""


def _compose_debug_param(*, port: int, user_param: str | None,
                         bind_host: str = "0.0.0.0") -> str:
    """Build the `job.param` body that brings up remote-pdb in the job
    process. User's preamble (if any) is appended *after* the bootstrap
    so the operator's setup runs once the debugger is attached and
    paused at the first stop.
    """
    body = PDB_BOOTSTRAP_TEMPLATE.format(host=bind_host, port=port)
    if user_param:
        body += "\n" + user_param
    return body


def _resolve_user_param(args) -> str | None:
    if args.param is not None and args.param_file is not None:
        raise SystemExit("pyopsctl: --param and --param-file are mutually exclusive")
    if args.param is not None:
        return args.param
    if args.param_file is not None:
        with open(args.param_file, encoding="utf-8") as f:
            return f.read()
    return None


def _host_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname or url


# Note on "wait for ready" semantics for kind=debug:
#
# Under the new server semantics, `state=running` only fires AFTER
# `exec(param)` returns. For debug jobs, the param block calls
# `RemotePdb(...).set_trace()` which blocks until the operator connects
# AND issues `continue`. So polling `state=running` to mean "debugger
# ready to attach" would wait forever.
#
# The correct readiness signal for debug-kind is "the remote-pdb TCP
# listener is bound and accepting connections". `PdbClient.connect()`
# implements this directly via a retry-on-ECONNREFUSED loop, so the
# session daemon does the wait at exactly the point that matters and
# pyopsctl needs no separate poll endpoint or flag.


def _poll_terminal(c: Client, job_id: str, *, timeout: float,
                   interval: float = 0.5) -> str | None:
    """Poll `GET /scriptlet/job/item` until the job reaches a terminal
    state. Returns the terminal state, or None on timeout. Used by tests
    and (potentially) by a future `debug wait` verb."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get("/scriptlet/job/item", params={"id": job_id})
        state = (job or {}).get("state", "")
        if state in ("done", "failed", "cancelled", "remove", "removed"):
            return state
        time.sleep(interval)
    return None


# ---------------------------------------------------------------------------
# `debug start`
# ---------------------------------------------------------------------------


def _start(args) -> int:
    profile = load(args)
    user_param = _resolve_user_param(args)

    # Lease a port from the profile pool. Stale-lease cleanup: ask the
    # server whether the leasing job_id is still alive; release if not.
    with Client(profile, timeout=args.timeout) as c:
        def _is_stale(jid: str) -> bool:
            try:
                row = c.get("/scriptlet/job/item", params={"id": jid})
            except HTTPError:
                # If the server doesn't know the job, the lease is
                # definitely stale.
                return True
            state = (row or {}).get("state", "")
            return state in ("done", "failed", "cancelled", "remove", "removed", "")

        # Tentative job_id: leasing happens before enqueue, so we use a
        # placeholder. We'll rewrite the lease to the real job_id after
        # the server tells us what it picked.
        placeholder = f"pending_{os.getpid()}_{time.time_ns()}"
        try:
            lease = ports.lease(profile.name, profile.debug_ports, placeholder,
                                is_stale=_is_stale)
        except ports.NoPortsAvailable as ex:
            emit_error(args, str(ex))
            return 4

        param = _compose_debug_param(port=lease.port, user_param=user_param)
        overrides = build_overrides(args.overrides_dir, args.set)
        body = {
            "set": args.set, "name": args.name, "kind": args.kind,
            "param": param, "drop_done": False, "max_retries": 0,
        }
        if overrides is not None:
            body["overrides"] = overrides

        try:
            resp = c.put("/scriptlet/job/item", json_body=body)
        except HTTPError:
            ports.release(lease)
            raise
        job_id = (resp or {}).get("job")
        if not job_id:
            ports.release(lease)
            emit_error(args, "server returned no job id")
            return 5

        # Rewrite the lease file from placeholder -> real job_id.
        try:
            with open(lease.path, "w", encoding="ascii") as f:
                f.write(job_id)
        except OSError:
            pass  # best-effort

        emit(args, {"job_id": job_id, "set": args.set, "name": args.name,
                    "kind": args.kind, "port": lease.port,
                    "host": _host_of(profile.url)},
             id_field="job_id",
             human=f"{args.kind} job {job_id} enqueued ({args.set}::{args.name}, "
                   f"pdb on {_host_of(profile.url)}:{lease.port}); "
                   f"attach via `pyopsctl debug attach {job_id}`")
        return 0


# ---------------------------------------------------------------------------
# `debug attach`
# ---------------------------------------------------------------------------


def _find_job(c: Client, job_id: str) -> dict:
    """Return the job-list row for `job_id`, enriched with `set` and
    `name` parsed from the job id."""
    rows = c.get("/scriptlet/job/list") or []
    for r in rows:
        if r.get("id") == job_id:
            owner, set_name, scriptlet_name = _parse_job_id(job_id)
            return {**r, "set": set_name, "name": scriptlet_name, "owner_parsed": owner}
    raise RuntimeError(f"job {job_id} not found")


def _parse_job_id(job_id: str) -> tuple[str, str, str]:
    """Split a pyops job id into (owner, set, name)."""
    import re
    match = re.match(r"^(.+)_(\d{8}T\d+)$", job_id)
    if not match:
        return ("", "", "")
    head = match.group(1)
    parts = head.split("_", 2)
    if len(parts) != 3:
        return ("", "", "")
    return parts[0], parts[1], parts[2]


def _attach(args) -> int:
    profile = load(args)
    sp = session_paths.for_job(args.job_id)

    existing_pid = session_paths.read_pid(sp) if os.path.isdir(sp.root) else None
    if existing_pid is not None and session_paths.is_alive(existing_pid):
        emit_error(args, f"already attached to {args.job_id} "
                          f"(daemon pid {existing_pid}); detach first")
        return 3
    if existing_pid is not None and not session_paths.is_alive(existing_pid):
        session_paths.cleanup(sp)

    # The port comes from our own lease file — the operator's machine
    # is the source of truth for "what port did pyopsctl assign to this
    # job". The server never knew.
    port = ports.find_port_for_job(profile.name, args.job_id)
    if port is None:
        emit_error(args, f"no debug-port lease for job {args.job_id} in "
                         f"profile {profile.name!r} — was the job started "
                         f"by this profile?")
        return 4

    with Client(profile, timeout=args.timeout) as c:
        try:
            job = _find_job(c, args.job_id)
        except RuntimeError as ex:
            emit_error(args, str(ex))
            return 4

    pdb_host = args.debug_host or _host_of(profile.url)
    set_name = job.get("set")
    scriptlet_name = job.get("name")

    def _child(sp_inner, write_fd):
        try:
            session_daemon.daemon_main(
                sp_inner, write_fd, profile=profile,
                pdb_host=pdb_host, pdb_port=int(port),
                job_id=args.job_id, set_name=set_name,
                scriptlet_name=scriptlet_name,
            )
        except SystemExit:
            raise
        except Exception as ex:  # noqa: BLE001
            try:
                os.write(write_fd, f"error: {ex}\n".encode("utf-8"))
            except OSError:
                pass

    try:
        session_spawn.fork_daemon(sp, _child)
    except RuntimeError as ex:
        emit_error(args, f"session daemon failed to start: {ex}")
        return 3

    try:
        snapshot = session_client.call(sp, "state")
    except session_client.SessionError as ex:
        emit_error(args, f"could not retrieve initial state: {ex}")
        return 3
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _stop(args) -> int:
    profile = load(args)
    with Client(profile, timeout=args.timeout) as c:
        c.get("/scriptlet/job/cancel", params={"id": args.job_id})
        # Release the port lease so the pool reclaims it for the next
        # `debug start` against this profile.
        released = ports.release_for_job(profile.name, args.job_id)
        emit(args, {"job_id": args.job_id, "state": "cancel",
                    "released_port": released if released > 0 else None},
             id_field="job_id",
             human=f"debug job {args.job_id} cancel requested" + (
                 f"; released port {released}" if released > 0 else ""))
        return 0


# ---------------------------------------------------------------------------
# Daemon-routed ops
# ---------------------------------------------------------------------------


def _via_daemon(args, op: str, op_args: dict | None = None) -> Any:
    try:
        sp = session_client.resolve_target(getattr(args, "job_id", None))
        return session_client.call(sp, op, op_args or {}, timeout=args.timeout)
    except session_client.SessionError as ex:
        emit_error(args, str(ex))
        sys.exit(3 if ex.code in ("NO_SESSION", "AMBIGUOUS_SESSION", "BAD_REQUEST") else 4)


def _state(args) -> int:
    snapshot = _via_daemon(args, "state")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _stack(args) -> int:
    snapshot = _via_daemon(args, "stack")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _locals(args) -> int:
    result = _via_daemon(args, "locals")
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_variables("locals", result.get("locals") or []))
    return 0


def _globals(args) -> int:
    result = _via_daemon(args, "globals")
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_variables("globals", result.get("globals") or []))
    return 0


def _evaluate(args) -> int:
    result = _via_daemon(args, "evaluate",
                         {"expression": args.expression})
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_evaluate(result))
    return 0


def _break_set(args) -> int:
    friendly, _, line_str = args.ref.rpartition(":")
    if not line_str or not line_str.isdigit():
        emit_error(args, f"break set: ref must be <set>::<name>:<line>, got {args.ref!r}")
        return 2
    result = _via_daemon(args, "break_set",
                         {"friendly": friendly, "line": int(line_str),
                          "condition": args.condition})
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_breakpoint(result))
    return 0


def _break_list(args) -> int:
    result = _via_daemon(args, "break_list")
    bps = (result or {}).get("breakpoints") or []
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_breakpoints(bps))
    return 0


def _break_remove(args) -> int:
    op_args = {"all": True} if args.all else {"id": args.id}
    result = _via_daemon(args, "break_remove", op_args)
    if args.json_mode:
        emit(args, result)
    else:
        removed = (result or {}).get("removed")
        if removed == "all":
            print("removed: all")
        elif isinstance(removed, list) and removed:
            print(f"removed: {' '.join(removed)}")
        else:
            print("removed: (none)")
    return 0


def _step(args) -> int:
    snapshot = _via_daemon(args, "step")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _step_in(args) -> int:
    snapshot = _via_daemon(args, "step_in")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _step_out(args) -> int:
    snapshot = _via_daemon(args, "step_out")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _continue(args) -> int:
    snapshot = _via_daemon(args, "continue")
    if args.json_mode:
        emit(args, snapshot)
    else:
        print(debug_format.render_state_dump(snapshot))
    return 0


def _source(args) -> int:
    friendly = args.ref
    op_args: dict[str, Any] = {"friendly": friendly}
    if args.lines:
        try:
            start, end = args.lines.split(":", 1)
            op_args["start_line"] = int(start)
            op_args["end_line"] = int(end)
        except ValueError:
            emit_error(args, f"--lines must be START:END, got {args.lines!r}")
            return 2
    result = _via_daemon(args, "source", op_args)
    if args.json_mode:
        emit(args, result)
    else:
        print(debug_format.render_source(result))
    return 0


def _detach(args) -> int:
    result = _via_daemon(args, "detach")
    if args.json_mode:
        emit(args, result)
    else:
        print(f"detached job_id={result.get('job_id')}")
    return 0


def _terminate(args) -> int:
    result = _via_daemon(args, "terminate")
    if args.json_mode:
        emit(args, result)
    else:
        print(f"terminated job_id={result.get('job_id')}")
    return 0


def _session_list(args) -> int:
    rows = []
    for sp in session_paths.list_sessions():
        pid = session_paths.read_pid(sp)
        if pid is None or not session_paths.is_alive(pid):
            session_paths.cleanup(sp)
            continue
        meta = session_paths.read_meta(sp) or {}
        rows.append({
            "job_id": sp.job_id,
            "set": meta.get("set"),
            "name": meta.get("name"),
            "pdb_port": meta.get("pdb_port"),
            "pid": pid,
            "attached_at": meta.get("attached_at"),
        })
    if args.json_mode:
        emit(args, rows)
    else:
        print(debug_format.render_session_list(rows))
    return 0


def _session_info(args) -> int:
    result = _via_daemon(args, "info")
    if args.json_mode:
        emit(args, result)
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers, parents):
    p = subparsers.add_parser("debug",
                              help="debug jobs: start, attach, step, inspect, break, ...",
                              parents=parents)
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    start = sub.add_parser("start",
                           help="enqueue a debug job (remote-pdb bootstrap in param)",
                           parents=parents)
    start.add_argument("set"); start.add_argument("name")
    start.add_argument("--kind", choices=["debug", "testdebug"], default="debug",
                       help="debug (default): production codepath under "
                            "pdb. testdebug: __name__=__test__ block under "
                            "pdb (sources still under .debug/src/ + a "
                            ".test symlink)")
    start.add_argument("--param", metavar="TEXT", default=None,
                       help="user param appended after the pdb bootstrap")
    start.add_argument("--param-file", metavar="PATH", default=None)
    start.add_argument("--overrides-dir", metavar="DIR", default=None)
    start.set_defaults(func=_start)

    attach = sub.add_parser("attach",
                            help="start a session daemon, attach pdb, return current state",
                            parents=parents)
    attach.add_argument("job_id")
    attach.add_argument("--debug-host", default=None, metavar="HOST",
                        help="pdb host (default: hostname of the profile URL)")
    attach.set_defaults(func=_attach)

    stop = sub.add_parser("stop",
                          help="cancel a debug job and release its port lease",
                          parents=parents)
    stop.add_argument("job_id")
    stop.set_defaults(func=_stop)

    # --- daemon-routed verbs ---

    state = sub.add_parser("state",
                           help="show current state (read-only, no pdb traffic)",
                           parents=parents)
    state.add_argument("--job-id", default=None, dest="job_id")
    state.set_defaults(func=_state)

    stack = sub.add_parser("stack", help="refresh + show the full state dump",
                           parents=parents)
    stack.add_argument("--job-id", default=None, dest="job_id")
    stack.set_defaults(func=_stack)

    locs = sub.add_parser("locals", help="show locals of the current frame",
                          parents=parents)
    locs.add_argument("--job-id", default=None, dest="job_id")
    locs.set_defaults(func=_locals)

    glbs = sub.add_parser("globals", help="show globals of the current frame",
                          parents=parents)
    glbs.add_argument("--job-id", default=None, dest="job_id")
    glbs.set_defaults(func=_globals)

    ev = sub.add_parser("evaluate", help="evaluate an expression in the current frame",
                        parents=parents)
    ev.add_argument("expression")
    ev.add_argument("--job-id", default=None, dest="job_id")
    ev.set_defaults(func=_evaluate)

    brk = sub.add_parser("break", help="set/list/remove breakpoints",
                         parents=parents)
    brk_sub = brk.add_subparsers(dest="break_action", required=True)

    bs = brk_sub.add_parser("set", help="set a line breakpoint",
                            parents=parents)
    bs.add_argument("ref", help="<set>::<name>:<line>")
    bs.add_argument("--condition", default=None)
    bs.add_argument("--job-id", default=None, dest="job_id")
    bs.set_defaults(func=_break_set)

    bl = brk_sub.add_parser("list", help="list current breakpoints",
                            parents=parents)
    bl.add_argument("--job-id", default=None, dest="job_id")
    bl.set_defaults(func=_break_list)

    br = brk_sub.add_parser("remove", help="remove a breakpoint or all",
                            parents=parents)
    br.add_argument("id", nargs="?", default=None)
    br.add_argument("--all", action="store_true")
    br.add_argument("--job-id", default=None, dest="job_id")
    br.set_defaults(func=_break_remove)

    stp = sub.add_parser("step", help="step over the current line (pdb: n)",
                         parents=parents)
    stp.add_argument("--job-id", default=None, dest="job_id")
    stp.set_defaults(func=_step)

    sti = sub.add_parser("step-in", help="step into a callee (pdb: s)",
                         parents=parents)
    sti.add_argument("--job-id", default=None, dest="job_id")
    sti.set_defaults(func=_step_in)

    sto = sub.add_parser("step-out", help="step out of the current frame (pdb: r)",
                         parents=parents)
    sto.add_argument("--job-id", default=None, dest="job_id")
    sto.set_defaults(func=_step_out)

    cnt = sub.add_parser("continue", help="continue execution until the next stop (pdb: c)",
                         parents=parents)
    cnt.add_argument("--job-id", default=None, dest="job_id")
    cnt.set_defaults(func=_continue)

    src = sub.add_parser("source", help="print full or partial source for a scriptlet",
                         parents=parents)
    src.add_argument("ref", help="<set>::<name>")
    src.add_argument("--lines", metavar="START:END", default=None)
    src.add_argument("--job-id", default=None, dest="job_id")
    src.set_defaults(func=_source)

    det = sub.add_parser("detach",
                         help="close the pdb session (job continues from set_trace)",
                         parents=parents)
    det.add_argument("--job-id", default=None, dest="job_id")
    det.set_defaults(func=_detach)

    term = sub.add_parser("terminate",
                          help="send pdb `q` (BdbQuit) so the job ends",
                          parents=parents)
    term.add_argument("--job-id", default=None, dest="job_id")
    term.set_defaults(func=_terminate)

    sess = sub.add_parser("session", help="list/inspect debug sessions",
                          parents=parents)
    sess_sub = sess.add_subparsers(dest="session_action", required=True)

    sl = sess_sub.add_parser("list", help="list active debug sessions",
                             parents=parents)
    sl.set_defaults(func=_session_list)

    si = sess_sub.add_parser("info", help="dump session metadata + last stop",
                             parents=parents)
    si.add_argument("--job-id", default=None, dest="job_id")
    si.set_defaults(func=_session_info)
