"""Spawn the session daemon.

`pyopsctl debug attach` runs in the operator's terminal; it forks the
daemon, waits on a pipe for "ready" from the child, then returns the
attach result. The child becomes the daemon: `setsid`, closes stdio,
redirects 0/1/2 to /dev/null + a log file, runs the main loop.

setsid on a child that no longer has a controlling tty is enough — no
double-fork.

Pipe handshake:
  Parent writes nothing; reads one line from `read_fd`.
  Child writes a single line on `write_fd`:
    "ready\\n"             — daemon up, control socket bound, DAP bootstrapped
    "error: <message>\\n"  — daemon failed to start; details in daemon.log

If the parent reads "error: ..." it exits non-zero with that message.
If the parent reads "ready" it requests the initial state from the
daemon over the control socket and prints it.
"""
import os
import sys
from typing import Callable

from pyopsctl.session import paths


READY_LINE = b"ready\n"


def fork_daemon(sp: paths.SessionPaths,
                daemon_main: Callable[[paths.SessionPaths, int], None]) -> None:
    """Fork once and run `daemon_main(sp, write_fd)` in the child.

    The parent blocks reading one line from a pipe the child uses to
    signal readiness, then either returns (success) or raises (failure).

    `daemon_main` is responsible for:
      - calling `os.setsid()`
      - closing/redirecting stdio
      - binding the control socket at `sp.sock_file`
      - bootstrapping DAP, doing the first-stop fixup
      - writing "ready\\n" to `write_fd` (then closing it)
      - running the daemon's event loop

    If `daemon_main` raises before signalling, it should write
    "error: <message>\\n" to `write_fd` itself (caller-side); this
    helper doesn't trap exceptions across the fork boundary.
    """
    paths.ensure_dir(sp)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        # Child
        os.close(read_fd)
        try:
            daemon_main(sp, write_fd)
        finally:
            os._exit(0)
    # Parent
    os.close(write_fd)
    try:
        line = _readline(read_fd)
    finally:
        os.close(read_fd)
    if line == READY_LINE.rstrip(b"\n"):
        return
    if line.startswith(b"error:"):
        raise RuntimeError(line.decode("utf-8", errors="replace").strip())
    # Empty / unexpected — assume the child died before signaling.
    raise RuntimeError(
        f"session daemon did not signal ready (got {line!r}); "
        f"see {sp.log_file} for details"
    )


def _readline(fd: int, *, limit: int = 4096) -> bytes:
    buf = bytearray()
    while len(buf) < limit:
        chunk = os.read(fd, 1)
        if not chunk:
            break
        buf.extend(chunk)
        if chunk == b"\n":
            break
    return bytes(buf).rstrip(b"\n")


def detach_stdio(log_path: str) -> None:
    """Run inside the child after `setsid`. Redirects fds 0/1/2 to
    /dev/null (stdin) and the append log (stdout+stderr) so the
    daemon has nowhere to write that would interfere with the parent's
    terminal."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    logfd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(devnull, 0)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    if devnull > 2:
        os.close(devnull)
    if logfd > 2:
        os.close(logfd)
