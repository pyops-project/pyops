"""Client side of the session control socket.

Used by every `debug` subcommand except `attach`, `start`, `port`, and
`stop`. Opens the UDS, sends one op envelope, reads the response,
closes — the daemon does the heavy lifting and serializes ops itself.

Target-session resolution per the plan:
  1. explicit `--job-id` flag
  2. else if exactly one live session exists, use it
  3. else error: pyopsctl: ambiguous — pass --job-id (run 'debug session list')
"""
import os
import socket
from typing import Any

from pyopsctl.session import paths, protocol


class SessionError(Exception):
    """A control-socket call failed at the protocol level (daemon
    returned `ok: false` or the socket itself misbehaved)."""

    def __init__(self, message: str, *, code: str | None = None,
                 details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


def resolve_target(explicit_job_id: str | None) -> paths.SessionPaths:
    """Return the SessionPaths for the target job."""
    if explicit_job_id:
        sp = paths.for_job(explicit_job_id)
        if not os.path.isdir(sp.root):
            raise SessionError(
                f"no session for job_id={explicit_job_id!r}",
                code="NO_SESSION",
            )
        pid = paths.read_pid(sp)
        if pid is None or not paths.is_alive(pid):
            raise SessionError(
                f"session for job_id={explicit_job_id!r} is stale (pid {pid} dead)",
                code="NO_SESSION",
            )
        return sp

    live = []
    for sp in paths.list_sessions():
        pid = paths.read_pid(sp)
        if pid is not None and paths.is_alive(pid):
            live.append(sp)
    if not live:
        raise SessionError(
            "no debug session is attached — run 'pyopsctl debug attach JOB_ID' first",
            code="NO_SESSION",
        )
    if len(live) > 1:
        raise SessionError(
            "ambiguous: multiple debug sessions are attached — pass --job-id "
            "(see 'pyopsctl debug session list')",
            code="AMBIGUOUS_SESSION",
            details=[sp.job_id for sp in live],
        )
    return live[0]


def call(sp: paths.SessionPaths, op: str, args: dict | None = None, *,
         timeout: float = 60.0) -> Any:
    """Send one control envelope, wait for the response, return its
    `result`. Raises SessionError on `ok: false` or transport failure."""
    if op not in protocol.OPS:
        raise SessionError(f"unknown op {op!r}", code="BAD_REQUEST")
    envelope = {"op": op, "args": args if args is not None else {}}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            # AF_UNIX paths are capped at ~108 bytes on Linux. Use the
            # same trick as the daemon: chdir to the session dir and
            # connect with the relative name `sock`.
            cwd = os.getcwd()
            try:
                os.chdir(sp.root)
                sock.connect("sock")
            finally:
                os.chdir(cwd)
        except OSError as ex:
            raise SessionError(
                f"cannot reach session daemon at {sp.sock_file}: {ex}",
                code="NO_SESSION",
            ) from ex
        protocol.write_line(sock, envelope)
        line = protocol.read_line(sock)
        if line is None:
            raise SessionError("session daemon closed the control socket")
        response = protocol.decode(line)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not response.get("ok", False):
        raise SessionError(
            response.get("error") or "session daemon returned failure",
            code=response.get("code"),
            details=response.get("details"),
        )
    return response.get("result")
