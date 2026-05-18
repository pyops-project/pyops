"""TCP wire client for a remote pdb session.

Drives a `(Pdb) `-prompt REPL over a single TCP connection. One command
at a time, synchronous request/response — pdb writes its output, then
the prompt; we send a command, read until the next prompt, parse what
came before.

Why this works:

  - pdb's prompt is the literal byte sequence `(Pdb) ` (5 ASCII chars +
    trailing space, no newline) — emitted only when pdb is ready for
    the next command. That makes "read until ready" a substring match.
  - pdb's command surface (`b`, `n`, `s`, `r`, `c`, `p`, `pp`, `l`,
    `w`, `cl`, `clear`, `up`, `down`, `q`) is stable across Python
    versions because it's stdlib. The exact text of the responses is
    stable enough to parse with simple regexes for the small set of
    shapes the daemon needs: current frame ("> path(line)func()"),
    arrow line ("-> source"), `where` stack frames, breakpoint listings.
  - For arbitrary expression evaluation (`p locals()`, `p someobj`,
    `p user_expr`), we hand the raw repr back to the caller — the
    daemon turns that into the JSON `value` field. No structured
    variable shape, no variablesReferences — drilling is just "evaluate
    `obj.attr`" the next call.

Connection lifecycle: the daemon opens TCP, drives the session, closes
on `detach` / `terminate` / daemon exit. Re-attach after disconnect is
not supported in v1 (would require the param's bootstrap to re-arm
`set_trace()` via a signal handler). Operator restarts the debug job
to get a fresh session.

Pause-mid-run is also not supported in v1 (the operator sets
breakpoints upfront before `continue`).
"""
import os
import re
import socket
import time
from dataclasses import dataclass


PROMPT = b"(Pdb) "
# `(Pdb)` with no trailing space appears at the end of pdb's "exit by EOF"
# message; we tolerate either as a terminator.
PROMPT_NO_SPACE = b"(Pdb)"

_FRAME_LINE = re.compile(
    r"^> (?P<path>.+?)\((?P<line>\d+)\)(?P<func>[^\n]*?)$",
    re.MULTILINE,
)
_STACK_FRAME = re.compile(
    r"^(?P<cur>[> ]) (?P<path>.+?)\((?P<line>\d+)\)(?P<func>[^\n]*?)$",
    re.MULTILINE,
)
_BREAKPOINT_TABLE_ROW = re.compile(
    r"^(?P<id>\d+)\s+breakpoint\s+(?P<status>keep|del)\s+(?P<enabled>yes|no)"
    r"\s+at\s+(?P<path>[^:\s]+):(?P<line>\d+)\s*$",
    re.MULTILINE,
)
# Conditional line follows a breakpoint row, indented:
#     stop only if x > 0
_BREAKPOINT_COND = re.compile(
    r"^\s+stop only if\s+(?P<cond>.+?)\s*$",
    re.MULTILINE,
)


class PdbError(Exception):
    """Wire error or pdb-side failure (e.g. `*** SyntaxError: ...`)."""

    def __init__(self, message: str, *, raw: str = ""):
        super().__init__(message)
        self.raw = raw


@dataclass
class StopInfo:
    """Where pdb is paused after the most recent command. `path` is the
    raw absolute path pdb reported; `arrow_line` is the `->` source
    snippet (one line of code) if pdb included it."""
    path: str
    line: int
    func: str
    arrow_line: str = ""


@dataclass
class BreakpointRecord:
    """One row from `b` (no-args breakpoint list)."""
    id: int
    path: str
    line: int
    enabled: bool
    condition: str = ""


class PdbClient:
    """Synchronous pdb-over-TCP driver. One connection per session."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buf = bytearray()
        # The pdb instance prints its initial banner + first prompt
        # the moment our TCP connect satisfies its accept(). Consume
        # that here so the daemon's first op sees a clean state.
        self._initial = self._read_until_prompt()

    @classmethod
    def connect(cls, host: str, port: int, *, timeout: float = 30.0,
                connect_retry: float | None = None,
                retry_interval: float = 0.5) -> "PdbClient":
        """Open a TCP connection to a remote-pdb listener.

        Retries on `ConnectionRefusedError` for up to `connect_retry`
        seconds — the listener isn't bound until the job process
        reaches the bootstrap's `set_trace()` call, which (for the
        first job in a per-set venv) waits for the runner to
        materialise the venv + pip-install `remote-pdb`. That can
        easily take tens of seconds on a cold path.

        `connect_retry` defaults to `$PYOPSCTL_PDB_CONNECT_RETRY` if
        set, else 120 seconds. Tests that exercise the re-attach-after-
        detach path (where there's no listener to wait for) lower this
        to fail fast.
        """
        if connect_retry is None:
            env_override = os.environ.get("PYOPSCTL_PDB_CONNECT_RETRY")
            connect_retry = float(env_override) if env_override else 120.0
        deadline = time.monotonic() + connect_retry
        while True:
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
                sock.settimeout(60.0)
                return cls(sock)
            except ConnectionRefusedError as ex:
                if time.monotonic() >= deadline:
                    raise PdbError(
                        f"remote-pdb listener at {host}:{port} did not come "
                        f"up within {connect_retry:.0f}s: {ex}"
                    ) from ex
                time.sleep(retry_interval)
            except OSError as ex:
                raise PdbError(
                    f"cannot connect to remote-pdb at {host}:{port}: {ex}"
                ) from ex

    @property
    def initial_banner(self) -> str:
        """Text pdb emitted between connect and its first prompt — the
        opening frame info, basically. Available so the daemon's first
        `state` op has something to return."""
        return self._initial

    # ------------------------------------------------------------------ wire

    def _read_until_prompt(self, *, timeout: float | None = None) -> str:
        """Read from the socket until a `(Pdb) ` prompt is seen. Returns
        everything *before* the prompt (with the prompt stripped).

        Tolerates the prompt arriving with or without the trailing space
        — pdb 3.11+ emits `(Pdb) `, but EOF-on-stdin paths print just
        `(Pdb)` then close. Both are valid terminators.
        """
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            while True:
                idx = self._buf.find(PROMPT)
                if idx >= 0:
                    out = bytes(self._buf[:idx]).decode("utf-8", errors="replace")
                    del self._buf[: idx + len(PROMPT)]
                    return out
                # Fallback: prompt at end-of-stream with no trailing space.
                # Only accept this if we got an EOF on the next read.
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout as ex:
                    buf_snippet = bytes(self._buf).decode(
                        "utf-8", errors="replace"
                    )[:512]
                    raise PdbError(
                        f"timeout reading pdb prompt: {ex} "
                        f"(buffer so far, first 512 chars: {buf_snippet!r})"
                    ) from ex
                except OSError as ex:
                    raise PdbError(f"socket error reading pdb: {ex}") from ex
                if not chunk:
                    # EOF. If the trailing buffer ends with the no-space
                    # variant, accept it; otherwise propagate as error.
                    if self._buf.endswith(PROMPT_NO_SPACE):
                        out = bytes(self._buf[: -len(PROMPT_NO_SPACE)]).decode(
                            "utf-8", errors="replace"
                        )
                        self._buf.clear()
                        return out
                    raw = bytes(self._buf).decode("utf-8", errors="replace")
                    raise PdbError(
                        f"pdb closed the connection unexpectedly", raw=raw
                    )
                self._buf.extend(chunk)
        finally:
            self._sock.settimeout(60.0)

    def _send(self, line: str) -> None:
        try:
            self._sock.sendall(line.encode("utf-8") + b"\n")
        except OSError as ex:
            raise PdbError(f"socket error writing pdb: {ex}") from ex

    def command(self, line: str, *, timeout: float | None = None) -> str:
        """Send one pdb command, return everything pdb wrote in
        response (up to but not including the next prompt). Pdb-side
        errors (lines beginning `*** `) bubble up as PdbError so the
        daemon can surface them as DAP_ERROR-equivalent."""
        self._send(line)
        text = self._read_until_prompt(timeout=timeout)
        # Pdb prints `*** <ErrorClass>: <message>` for evaluation
        # failures. The error is non-fatal — pdb is still at a prompt,
        # ready for the next command — so we raise but the session
        # stays usable.
        first_stars = next(
            (ln for ln in text.splitlines() if ln.startswith("*** ")),
            None,
        )
        if first_stars is not None:
            raise PdbError(first_stars[len("*** "):].strip(), raw=text)
        return text

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def parse_stop_info(text: str) -> StopInfo | None:
        """Find the `> path(line)func()` cursor line in `text`. The most
        recently emitted one wins (a step that traverses multiple stops
        only shows the final position at the prompt; in practice pdb
        emits one `>` per command response)."""
        matches = list(_FRAME_LINE.finditer(text))
        if not matches:
            return None
        m = matches[-1]
        # `-> source` line follows `> ...` immediately when pdb prints
        # the current source line. Capture it for the daemon's display.
        arrow = ""
        rest = text[m.end():].lstrip("\n")
        if rest.startswith("-> "):
            arrow = rest.split("\n", 1)[0][len("-> "):]
        return StopInfo(
            path=m.group("path"),
            line=int(m.group("line")),
            func=m.group("func").strip() or "<module>",
            arrow_line=arrow,
        )

    @staticmethod
    def parse_stack(text: str) -> list[StopInfo]:
        """`w` (where) — frames newest-last. The current frame is marked
        with `> ` at column 0; other frames have `  ` (two spaces)."""
        out: list[StopInfo] = []
        for m in _STACK_FRAME.finditer(text):
            out.append(StopInfo(
                path=m.group("path"),
                line=int(m.group("line")),
                func=m.group("func").strip() or "<module>",
            ))
        return out

    @staticmethod
    def parse_breakpoints(text: str) -> list[BreakpointRecord]:
        """`b` no-args breakpoint table.

        Standard pdb format:
          Num Type         Disp Enb   Where
          1   breakpoint   keep yes   at /path/to/file.py:42
          2   breakpoint   keep yes   at /path/to/file.py:50
                  stop only if x > 0

        The conditional clause lives on its own indented line below the
        row it qualifies — match the row first, then scan for any
        condition line until the next row (or end of text).
        """
        rows = list(_BREAKPOINT_TABLE_ROW.finditer(text))
        out: list[BreakpointRecord] = []
        for i, m in enumerate(rows):
            end = rows[i + 1].start() if i + 1 < len(rows) else len(text)
            tail = text[m.end():end]
            cond_match = _BREAKPOINT_COND.search(tail)
            condition = cond_match.group("cond") if cond_match else ""
            out.append(BreakpointRecord(
                id=int(m.group("id")),
                path=m.group("path"),
                line=int(m.group("line")),
                enabled=(m.group("enabled") == "yes"),
                condition=condition,
            ))
        return out

    @staticmethod
    def parse_list(text: str) -> list[tuple[int, str, bool]]:
        """`l` (list source) — yields (line_no, text, is_current). The
        current line is marked with `->` (or `>>` for an exception).
        Both markers and any surrounding whitespace are stripped."""
        out: list[tuple[int, str, bool]] = []
        for raw in text.splitlines():
            stripped = raw.lstrip()
            if not stripped or not stripped[0].isdigit():
                continue
            head, _, rest = stripped.partition(" ")
            if not head.isdigit():
                continue
            n = int(head)
            tail = rest.lstrip()
            is_current = False
            if tail.startswith(("->", ">>")):
                tail = tail[2:].lstrip()
                is_current = True
            out.append((n, tail, is_current))
        return out
