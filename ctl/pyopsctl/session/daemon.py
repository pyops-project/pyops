"""Session daemon main loop.

Spawned by `pyopsctl debug attach JOB_ID`. Holds the TCP connection to
the remote pdb listener the job's `param` brought up, exposes a Unix
domain socket the CLI subcommands talk to, dispatches ops, sends back
results.

Lifecycle:
  1. setsid + detach stdio (via spawn.detach_stdio)
  2. Write pid file, write meta.json
  3. Open TCP to <pdb_host>:<pdb_port>
  4. Read the initial pdb prompt (synchronous; satisfied as soon as our
     connect releases the program from `set_trace()`'s accept())
  5. Bind UDS, write "ready" to the parent pipe
  6. Accept loop: one op envelope per connection, dispatch, write
     response, close
  7. Tear down on `detach` / `terminate` op or SIGTERM/SIGINT

The model is much simpler than the previous DAP daemon: pdb is
synchronous request/response (no event stream), so there are no
background watchers, no per-stop variable-ref generations, no friendly-
name resolver. The materialised source layout under `.debug/src/<set>/
{task,library}/<name>.py` gives us absolute paths pdb understands
directly, and the path tail `/.debug/src/<set>/<kind>/<name>.py` parses
back to `<set>::<name>` for friendly-name display.
"""
import errno
import json
import os
import re
import signal
import socket
import threading
import time
from typing import Any

from pyopsctl.client import Client as HttpClient
from pyopsctl.config import Profile
from pyopsctl.pdb.client import BreakpointRecord, PdbClient, PdbError, StopInfo
from pyopsctl.session import paths, protocol, spawn


_DEBUG_SRC_RE = re.compile(
    r"/\.debug/src/(?P<set>[^/]+)/(?:task|library)/(?P<name>[^/]+)\.py$"
)


def _friendly_from_path(path: str) -> str | None:
    """`/data/jobs/foo/JOBID/.debug/src/foo/task/scr.py` → `foo::scr`."""
    if not path:
        return None
    m = _DEBUG_SRC_RE.search(path)
    if not m:
        return None
    return f"{m.group('set')}::{m.group('name')}"


# ----------------------------------------------------------------------------
# Daemon state
# ----------------------------------------------------------------------------


class Daemon:
    """All mutable state the dispatcher reads/writes."""

    def __init__(self, sp: paths.SessionPaths, meta: dict, pdb: PdbClient,
                 http: HttpClient):
        self.sp = sp
        self.meta = meta
        self.pdb = pdb
        self.http = http
        # Serialise ops behind one lock so pdb's request/response wire
        # never interleaves with itself.
        self._lock = threading.RLock()
        self._last_stop: dict | None = None
        # Source cache: friendly -> list[str] of lines (fetched once from
        # /scriptlet/inventory/code; pdb's `l` is line-windowed and
        # we want full source on demand).
        self._source_cache: dict[str, list[str]] = {}
        # The job process chdirs into its workspace before running param,
        # so pdb's cwd IS the workspace. Materialised sources live at
        # `<cwd>/.debug/src/<set>/{task,library}/<name>.py`. Cached lazily
        # via pdb's `p __import__('os').getcwd()`.
        self._workspace_cwd: str | None = None
        self._exit_event = threading.Event()
        # Capture pdb's opening banner as the initial state so
        # `debug state` right after attach has something to show.
        self._absorb_response(pdb.initial_banner, reason="attach")

    # ---- shutdown plumbing

    def request_exit(self, reason: str) -> None:
        self._exit_reason = reason
        self._exit_event.set()

    def should_exit(self) -> bool:
        return self._exit_event.is_set()

    # ---- snapshot building from pdb output

    def _absorb_response(self, text: str, *, reason: str | None = None) -> dict:
        """Parse pdb's most recent output and store it as `_last_stop`.

        pdb emits the `> path(line)func()` cursor line whenever execution
        stops at a new location (step/next/return/continue-hit-bp). We
        save the most-recent cursor as the current frame; the daemon's
        verbs that read state (`state`, `stack`, `locals`, etc.) build
        on this cached cursor + on-demand pdb calls.
        """
        info = PdbClient.parse_stop_info(text)
        if info is None:
            # No new cursor in this response (e.g. `b foo:42` doesn't
            # move execution). Leave `_last_stop` alone but record the
            # raw text in case `state` wants to surface it.
            if self._last_stop is None:
                self._last_stop = {
                    "state": "stopped" if "(Pdb)" not in text else "running",
                    "reason": reason,
                    "frame": None,
                    "raw": text,
                }
            return self._last_stop or {}
        frame = _frame_payload(info)
        self._last_stop = {
            "state": "stopped",
            "reason": reason,
            "frame": frame,
            "raw": text,
        }
        return self._last_stop


def _frame_payload(info: StopInfo) -> dict:
    friendly = _friendly_from_path(info.path)
    if friendly:
        set_name, scriptlet_name = friendly.split("::", 1)
    else:
        set_name, scriptlet_name = None, None
    return {
        "name": info.func,
        "source": {
            "set": set_name,
            "name": scriptlet_name,
            "path": info.path,
            "is_shim": False,
        },
        "line": info.line,
        "column": None,
        "arrow_line": info.arrow_line,
    }


# ----------------------------------------------------------------------------
# Source fetch (server-side scriptlet body, full text)
# ----------------------------------------------------------------------------


def _fetch_source_lines(d: Daemon, friendly: str | None) -> list[str]:
    if not friendly:
        return []
    cached = d._source_cache.get(friendly)
    if cached is not None:
        return cached
    try:
        set_name, scriptlet_name = friendly.split("::", 1)
        resp = d.http.get("/scriptlet/inventory/code",
                          params={"set": set_name, "name": scriptlet_name})
        code = (resp or {}).get("code", "")
        lines = code.splitlines()
        d._source_cache[friendly] = lines
        return lines
    except Exception:  # noqa: BLE001
        return []


def _code_window(d: Daemon, friendly: str | None, current_line: int | None,
                 *, half: int = 10) -> dict | None:
    if not friendly or current_line is None:
        return None
    lines = _fetch_source_lines(d, friendly)
    if not lines:
        return None
    n = len(lines)
    start = max(1, current_line - half)
    end = min(n, current_line + half)
    out_lines = []
    for ln in range(start, end + 1):
        entry = {"n": ln, "text": lines[ln - 1]}
        if ln == current_line:
            entry["current"] = True
        out_lines.append(entry)
    set_name, scriptlet_name = friendly.split("::", 1)
    return {
        "set": set_name,
        "name": scriptlet_name,
        "current_line": current_line,
        "start_line": start,
        "end_line": end,
        "lines": out_lines,
    }


# ----------------------------------------------------------------------------
# Op dispatch helpers
# ----------------------------------------------------------------------------


def _eval_pdb(d: Daemon, expression: str) -> tuple[str, str]:
    """`p <expression>` — return (value_repr, raw_text). The repr is
    pdb's `repr(value)` output; the raw is everything pdb wrote so the
    caller can surface multi-line responses (`pp`-style)."""
    raw = d.pdb.command(f"p {expression}")
    value = raw.strip()
    return value, raw


def _stop_snapshot(d: Daemon, *, reason: str | None = None) -> dict:
    """Build the full state-dump payload from the current pdb cursor.
    Pulls locals/globals/code window/stack via on-demand pdb calls."""
    with d._lock:
        if d._last_stop is None or d._last_stop.get("frame") is None:
            raise PdbError("not at a stop — issue `continue` or set a breakpoint")
        frame = d._last_stop["frame"]
        friendly_main = (
            f"{frame['source']['set']}::{frame['source']['name']}"
            if frame["source"]["set"] else None
        )

        # locals / globals — pdb evaluates these as Python expressions
        # in the current frame.
        try:
            locals_repr, _ = _eval_pdb(d, "locals()")
        except PdbError:
            locals_repr = "{}"
        try:
            globals_repr, _ = _eval_pdb(d, "globals()")
        except PdbError:
            globals_repr = "{}"

        # `w` for the stack.
        try:
            stack_raw = d.pdb.command("w")
        except PdbError:
            stack_raw = ""
        stack = [
            _frame_payload(s) for s in PdbClient.parse_stack(stack_raw)
        ]

        # code window from the server-side scriptlet body
        code = _code_window(d, friendly_main, frame.get("line"))

        snapshot = {
            "state": "stopped",
            "reason": reason or d._last_stop.get("reason"),
            "frame": frame,
            "stack": stack,
            "locals": _explode_repr_dict(locals_repr),
            "globals": _explode_repr_dict(globals_repr),
            "code": code,
        }
        d._last_stop = {**d._last_stop, **snapshot}
        return snapshot


def _explode_repr_dict(repr_text: str) -> list[dict]:
    """`{'a': 1, 'b': 'x'}` → [{name: 'a', value: '1', type: ''},
    {name: 'b', value: \"'x'\", type: ''}].

    We do not try to eval — Python `eval(repr_text)` is unsafe and
    `ast.literal_eval` rejects most non-trivial reprs. Instead we
    accept that pdb's `repr(locals())` is one line of `{'k': v, ...}`
    and split with a token-aware splitter that respects nested
    brackets and string quotes. The repr of a non-literal (e.g.
    `<module 'os' from '...'>`) appears as `v` verbatim.
    """
    s = repr_text.strip()
    if not s.startswith("{") or not s.endswith("}"):
        return [{"name": "(unparseable)", "value": s, "type": ""}]
    inner = s[1:-1].strip()
    if not inner:
        return []
    out: list[dict] = []
    for pair in _split_top_level(inner, ","):
        pair = pair.strip()
        if not pair:
            continue
        # Key is a quoted string up to `: ` at depth 0.
        try:
            colon = _find_top_level_colon(pair)
        except ValueError:
            out.append({"name": "(malformed pair)", "value": pair, "type": ""})
            continue
        key_repr = pair[:colon].strip()
        val_repr = pair[colon + 1:].strip()
        key = _unquote_key(key_repr)
        out.append({"name": key, "value": val_repr, "type": ""})
    return out


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split `s` on `sep`, but only at depth 0 (outside of (), [], {}
    brackets and outside string quotes)."""
    out, buf, depth = [], [], 0
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


def _find_top_level_colon(s: str) -> int:
    """Index of the first `:` outside string quotes / brackets."""
    depth = 0
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\" and i + 1 < len(s):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return i
        i += 1
    raise ValueError("no top-level colon")


def _unquote_key(key_repr: str) -> str:
    """`'a'` → `a`, `"x"` → `x`, anything else returned as-is. Pdb's
    `locals()` keys are str so this almost always hits."""
    if len(key_repr) >= 2 and key_repr[0] in "'\"" and key_repr[-1] == key_repr[0]:
        return key_repr[1:-1]
    return key_repr


# ----------------------------------------------------------------------------
# Verbs
# ----------------------------------------------------------------------------


def _op_state(d: Daemon, args: dict) -> dict:
    with d._lock:
        if d._last_stop is None:
            return {"state": "running", "frame": None, "stack": [],
                    "locals": [], "globals": [], "code": None,
                    "hint": "no stop captured yet"}
        # `state` is a cached read — return whatever we last built.
        cached = dict(d._last_stop)
        # If we only have the frame cursor (no full snapshot yet), build
        # one now so the consumer sees the full state-dump shape.
        if cached.get("frame") and "locals" not in cached:
            return _stop_snapshot(d, reason=cached.get("reason"))
        return cached


def _op_stack(d: Daemon, args: dict) -> dict:
    return _stop_snapshot(d, reason="inspect")


def _op_locals(d: Daemon, args: dict) -> dict:
    snapshot = _stop_snapshot(d, reason="inspect")
    return {"locals": snapshot["locals"], "frame": snapshot["frame"]}


def _op_globals(d: Daemon, args: dict) -> dict:
    snapshot = _stop_snapshot(d, reason="inspect")
    return {"globals": snapshot["globals"], "frame": snapshot["frame"]}


def _op_evaluate(d: Daemon, args: dict) -> dict:
    expr = args.get("expression") or ""
    if not expr:
        raise PdbError("evaluate: expression is required")
    with d._lock:
        value, raw = _eval_pdb(d, expr)
    return {"expression": expr, "value": value, "type": "", "raw": raw}


def _op_break_set(d: Daemon, args: dict) -> dict:
    """Set a breakpoint at `<set>::<name>:<line>`.

    The materialised source layout under `<workspace>/.debug/src/<set>/
    {task,library}/<name>.py` doesn't tell pyopsctl which of task vs
    library a given scriptlet is — that's a server-side scriptlet-
    inventory fact. Rather than fetch the inventory, we try task first
    and fall back to library on a pdb file-not-found error. Both paths
    are passed to pdb absolute (we ask pdb for its cwd via
    `_workspace_cwd` and prepend it), so pdb resolves them directly
    without depending on the current stack frame.
    """
    friendly = args.get("friendly")
    line = int(args.get("line") or 0)
    condition = args.get("condition")
    if not friendly or line <= 0:
        raise PdbError("break_set: friendly and line required")
    set_name, _, scriptlet_name = friendly.partition("::")
    if not set_name or not scriptlet_name:
        raise PdbError(f"break_set: malformed friendly {friendly!r}")

    workspace = _ensure_workspace_cwd(d)
    if workspace is None:
        raise PdbError(
            f"break_set: cannot determine job workspace cwd via pdb; "
            f"is pdb at a prompt?"
        )

    # Try task then library. Pdb reports `*** End of file` for a path
    # whose source linecache is empty; PdbClient.command raises PdbError
    # on `***` lines, so we catch and try the alternate.
    last_error = None
    for kind in ("task", "library"):
        path = f"{workspace}/.debug/src/{set_name}/{kind}/{scriptlet_name}.py"
        cmd = f"b {path}:{line}"
        if condition:
            cmd += f", {condition}"
        try:
            with d._lock:
                raw = d.pdb.command(cmd)
        except PdbError as ex:
            last_error = ex
            continue
        bp_id = _parse_new_bp_id(raw)
        if bp_id is None:
            # Pdb sometimes prints nothing on duplicate breakpoint or
            # unresolvable line; that's not a `*** ` error so command()
            # didn't raise. Treat as "didn't take" and try the next kind.
            last_error = PdbError(
                f"pdb did not confirm a breakpoint id for {path}:{line}",
                raw=raw,
            )
            continue
        return {
            "id": bp_id,
            "friendly": friendly,
            "path": path,
            "line": line,
            "condition": condition,
            "verified": True,
            "pending": False,
            "raw": raw,
        }

    raise PdbError(
        f"break_set: pdb refused {friendly}:{line} for both task and "
        f"library layouts: {last_error}"
    )


_NEW_BP_RE = re.compile(r"^Breakpoint\s+(\d+)\s+at\s+", re.MULTILINE)


def _parse_new_bp_id(raw: str) -> int | None:
    m = _NEW_BP_RE.search(raw)
    return int(m.group(1)) if m else None


_REPR_STR_PATH = re.compile(r"^'(.+)'$|^\"(.+)\"$")


def _ensure_workspace_cwd(d: Daemon) -> str | None:
    """Cache + return the job process's cwd, which equals the workspace
    root (the runner chdirs there before running param). We ask pdb to
    evaluate `__import__('os').getcwd()` once; pdb's `p` prints the
    repr, which for a str is `'/path/...'`."""
    if d._workspace_cwd is not None:
        return d._workspace_cwd
    try:
        raw = d.pdb.command("p __import__('os').getcwd()")
    except PdbError:
        return None
    repr_text = raw.strip()
    m = _REPR_STR_PATH.match(repr_text)
    if not m:
        return None
    d._workspace_cwd = m.group(1) or m.group(2)
    return d._workspace_cwd


def _op_break_list(d: Daemon, args: dict) -> dict:
    with d._lock:
        raw = d.pdb.command("b")
    records = []
    for r in PdbClient.parse_breakpoints(raw):
        records.append({
            "id": r.id,
            "path": r.path,
            "friendly": _friendly_from_path(r.path),
            "line": r.line,
            "condition": r.condition,
            "verified": r.enabled,
            "pending": False,
        })
    return {"breakpoints": records, "raw": raw}


def _op_break_remove(d: Daemon, args: dict) -> dict:
    bp_id = args.get("id")
    remove_all = bool(args.get("all"))
    with d._lock:
        if remove_all:
            # `cl` with no args clears all; pdb asks for confirmation
            # (`Clear all breaks? `) which we satisfy with `yes`.
            raw = d.pdb.command("cl")
            if raw.strip().endswith("Clear all breaks?"):
                raw += d.pdb.command("yes")
            return {"removed": "all", "raw": raw}
        if not bp_id:
            raise PdbError("break_remove: id or --all required")
        raw = d.pdb.command(f"cl {bp_id}")
        return {"removed": [str(bp_id)], "raw": raw}


def _op_step(d: Daemon, args: dict) -> dict:
    return _step_then_snapshot(d, "n")


def _op_step_in(d: Daemon, args: dict) -> dict:
    return _step_then_snapshot(d, "s")


def _op_step_out(d: Daemon, args: dict) -> dict:
    return _step_then_snapshot(d, "r")


def _op_continue(d: Daemon, args: dict) -> dict:
    return _step_then_snapshot(d, "c")


def _step_then_snapshot(d: Daemon, command: str) -> dict:
    with d._lock:
        raw = d.pdb.command(command, timeout=None)
        # Detect natural termination — pdb prints
        # `--Return--` ... `--Call--` ... or just exits to the client.
        if "The program finished" in raw or "Program terminated" in raw:
            d._last_stop = {"state": "terminated", "frame": None, "raw": raw}
            return {"state": "terminated", "raw": raw}
        d._absorb_response(raw, reason=command)
    return _stop_snapshot(d, reason=command)


def _op_source(d: Daemon, args: dict) -> dict:
    friendly = args.get("friendly")
    if not friendly:
        raise PdbError("source: friendly required")
    lines = _fetch_source_lines(d, friendly)
    if not lines:
        raise PdbError(f"no source available for {friendly}")
    lo = args.get("start_line")
    hi = args.get("end_line")
    start = max(1, int(lo)) if lo else 1
    end = min(len(lines), int(hi)) if hi else len(lines)
    out_lines = [{"n": i, "text": lines[i - 1]} for i in range(start, end + 1)]
    set_name, scriptlet_name = friendly.split("::", 1)
    return {
        "set": set_name, "name": scriptlet_name, "path": None,
        "start_line": start, "end_line": end, "lines": out_lines,
    }


def _op_detach(d: Daemon, args: dict) -> dict:
    """Close TCP. pdb sees EOF on next read and the program continues
    from where `set_trace()` was called. Job keeps running."""
    try:
        # `c` first so pdb resumes cleanly instead of getting EOF mid-prompt.
        d.pdb.command("c", timeout=2.0)
    except PdbError:
        pass
    try:
        d.pdb.close()
    except Exception:  # noqa: BLE001
        pass
    d.request_exit("detach requested")
    return {"state": "detached", "job_id": d.sp.job_id}


def _op_terminate(d: Daemon, args: dict) -> dict:
    """`q` tells pdb to raise BdbQuit; the job's outer except in
    job_runner catches it as a normal exception and the job ends
    failed. For a clean teardown the operator should usually
    `debug stop` (cancel the job via HTTP) instead."""
    try:
        d.pdb.command("q", timeout=2.0)
    except PdbError:
        pass
    try:
        d.pdb.close()
    except Exception:  # noqa: BLE001
        pass
    d.request_exit("terminate requested")
    return {"state": "terminated", "job_id": d.sp.job_id}


def _op_info(d: Daemon, args: dict) -> dict:
    bp = []
    try:
        bp = _op_break_list(d, {}).get("breakpoints", [])
    except PdbError:
        pass
    return {
        "meta": d.meta,
        "last_stop": d._last_stop,
        "breakpoints": bp,
    }


OP_TABLE = {
    "state": _op_state,
    "stack": _op_stack,
    "locals": _op_locals,
    "globals": _op_globals,
    "evaluate": _op_evaluate,
    "break_set": _op_break_set,
    "break_list": _op_break_list,
    "break_remove": _op_break_remove,
    "step": _op_step,
    "step_in": _op_step_in,
    "step_out": _op_step_out,
    "continue": _op_continue,
    "source": _op_source,
    "detach": _op_detach,
    "terminate": _op_terminate,
    "info": _op_info,
}


# ----------------------------------------------------------------------------
# UDS accept loop
# ----------------------------------------------------------------------------


def _bind_uds(sp: paths.SessionPaths) -> socket.socket:
    """Bind the per-job control socket. AF_UNIX paths are capped at
    ~108 bytes on Linux; under deep pytest tmp_path trees the absolute
    path easily exceeds that. We `chdir` into the session dir and bind
    against the relative name `sock`, keeping the sockaddr short. The
    file still ends up at `sp.sock_file` on disk."""
    try:
        os.unlink(sp.sock_file)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cwd = os.getcwd()
    try:
        os.chdir(sp.root)
        sock.bind("sock")
    finally:
        os.chdir(cwd)
    os.chmod(sp.sock_file, 0o600)
    sock.listen(8)
    sock.settimeout(0.5)
    return sock


def _serve_one(d: Daemon, client: socket.socket) -> None:
    try:
        line = protocol.read_line(client)
        if not line:
            return
        try:
            envelope = protocol.decode(line)
        except Exception as ex:  # noqa: BLE001
            protocol.write_line(client, protocol.err("BAD_REQUEST", str(ex)))
            return
        op = envelope.get("op")
        args = envelope.get("args") or {}
        handler = OP_TABLE.get(op)
        if handler is None:
            protocol.write_line(client, protocol.err(
                "BAD_REQUEST", f"unknown op {op!r}"))
            return
        try:
            with d._lock:
                result = handler(d, args)
            protocol.write_line(client, protocol.ok(result))
        except PdbError as ex:
            protocol.write_line(client, protocol.err(
                "PDB_ERROR", str(ex), details=getattr(ex, "raw", "")))
        except Exception as ex:  # noqa: BLE001
            protocol.write_line(client, protocol.err(
                "PDB_ERROR", f"daemon exception: {ex}"))
    finally:
        try:
            client.close()
        except OSError:
            pass


def _accept_loop(d: Daemon, sock: socket.socket) -> None:
    while not d.should_exit():
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            continue
        except OSError as ex:
            if ex.errno == errno.EBADF or d.should_exit():
                break
            continue
        _serve_one(d, conn)


# ----------------------------------------------------------------------------
# Daemon entry point
# ----------------------------------------------------------------------------


def daemon_main(sp: paths.SessionPaths, write_fd: int, *, profile: Profile,
                pdb_host: str, pdb_port: int, job_id: str,
                set_name: str | None, scriptlet_name: str | None) -> None:
    """Daemon entry. Runs in the forked child after `spawn.fork_daemon`.

    Errors before the "ready" signal are reported via the parent pipe;
    errors after are logged to daemon.log and trigger teardown.
    """
    os.setsid()
    spawn.detach_stdio(sp.log_file)
    paths.write_pid(sp, os.getpid())

    def _fail(reason: str) -> None:
        import traceback
        traceback.print_exc()
        try:
            os.write(write_fd, f"error: {reason}\n".encode("utf-8"))
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass
        os._exit(1)

    def _signal_ready() -> None:
        try:
            os.write(write_fd, b"ready\n")
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass

    # Open TCP to the remote pdb listener.
    try:
        pdb_client = PdbClient.connect(pdb_host, pdb_port, timeout=30.0)
    except (OSError, PdbError) as ex:
        _fail(f"cannot connect to pdb at {pdb_host}:{pdb_port}: {ex}")
        return

    http = HttpClient(profile, timeout=30.0)
    paths.write_meta(sp, {
        "job_id": job_id,
        "server_url": profile.url,
        "profile": profile.name,
        "pdb_host": pdb_host,
        "pdb_port": pdb_port,
        "set": set_name,
        "name": scriptlet_name,
        "attached_at": time.time(),
    })
    d = Daemon(sp, {
        "job_id": job_id, "server_url": profile.url, "profile": profile.name,
        "pdb_host": pdb_host, "pdb_port": pdb_port,
        "set": set_name, "name": scriptlet_name,
    }, pdb_client, http)

    # Wire SIGTERM/SIGINT → request_exit
    def _on_signal(signum, frame):  # noqa: ARG001
        d.request_exit(f"signal {signum}")
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Bind control socket and signal ready
    try:
        uds = _bind_uds(sp)
    except OSError as ex:
        _fail(f"cannot bind control socket {sp.sock_file}: {ex}")
        return
    _signal_ready()

    try:
        _accept_loop(d, uds)
    finally:
        try:
            uds.close()
        except OSError:
            pass
        try:
            d.pdb.close()
        except Exception:  # noqa: BLE001
            pass
        paths.cleanup(sp)
