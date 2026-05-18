"""Control-socket request/response envelope for the session daemon.

Newline-delimited JSON over a Unix domain socket. One request → one
response per CLI invocation. The daemon serialises ops behind a single
lock so the pdb wire stays well-framed (one command, one response).

Envelope:

  Request:  {"op": "<verb>", "args": {...}}
  Response: {"ok": true,  "result": {...}}
            {"ok": false, "error": "...", "code": "<sym>"}

Error codes:
  PDB_ERROR           — pdb returned `*** <Class>: <message>` or the
                        wire died mid-request
  NO_STOP             — the operation needs a stopped state but pdb
                        is currently running (rare in v1; the operator
                        sets breakpoints upfront before `continue`)
  UNKNOWN_REF         — a friendly `<set>::<name>` ref doesn't resolve
                        to a known materialised source file
  TIMEOUT             — daemon timed out waiting for pdb's prompt
  BAD_REQUEST         — malformed op or args
  AMBIGUOUS_SESSION   — CLI-side: non-attach call without --job-id
                        when multiple sessions are alive
  NO_SESSION          — CLI-side: non-attach call when no session is
                        alive
"""
import json
import socket
from typing import Any


# Vocabulary the daemon's dispatcher understands. Kept here so CLI and
# daemon validate against the same set.
OPS = frozenset({
    "state",
    "stack",
    "locals", "globals", "evaluate",
    "break_set", "break_list", "break_remove",
    "step", "step_in", "step_out", "continue",
    "source",
    "detach", "terminate", "info",
})


def encode(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def decode(line: bytes) -> dict:
    return json.loads(line.decode("utf-8"))


def read_line(sock: socket.socket, *, limit: int = 8 * 1024 * 1024) -> bytes | None:
    """Read one `\\n`-terminated frame. Returns None on clean EOF. The
    `limit` guards against an attacker-or-bug filling memory with an
    unbounded request — control-socket messages are normally <1 KB."""
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return bytes(buf) if buf else None
        buf.extend(chunk)
        idx = buf.find(b"\n")
        if idx >= 0:
            return bytes(buf[:idx])
        if len(buf) > limit:
            raise ValueError(f"control-socket message exceeded {limit} bytes")


def write_line(sock: socket.socket, message: dict) -> None:
    sock.sendall(encode(message) + b"\n")


def ok(result: Any = None) -> dict:
    return {"ok": True, "result": result if result is not None else {}}


def err(code: str, message: str, *, details: Any = None) -> dict:
    out = {"ok": False, "code": code, "error": message}
    if details is not None:
        out["details"] = details
    return out
