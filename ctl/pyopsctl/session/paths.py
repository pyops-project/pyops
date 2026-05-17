"""State-directory layout for pyopsctl debug sessions.

Per-job session dir resolution chain:
  1. `$PYOPS_SESSION_DIR` (tests pin this to tmp_path)
  2. `$XDG_RUNTIME_DIR/pyops/sessions/`
  3. `~/.cache/pyops/sessions/`

Layout per session: `<root>/<job_id>/` containing
  pid          — daemon PID (one int)
  sock         — UDS the daemon listens on
  meta.json    — session metadata (server, profile, dap port, …)
  daemon.log   — daemon stdout/stderr (append)
"""
import json
import os
from dataclasses import dataclass


def sessions_root() -> str:
    explicit = os.environ.get("PYOPS_SESSION_DIR")
    if explicit:
        return explicit
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "pyops", "sessions")
    return os.path.expanduser("~/.cache/pyops/sessions")


def session_dir(job_id: str) -> str:
    return os.path.join(sessions_root(), _safe(job_id))


def _safe(component: str) -> str:
    """Sanitize a path component. Job IDs from pyops use
    `<owner>_<set>_<name>_<timestamp>` shape — already filesystem-safe —
    but defensively strip path separators just in case."""
    return component.replace("/", "_").replace("\\", "_")


@dataclass
class SessionPaths:
    job_id: str
    root: str

    @property
    def pid_file(self) -> str:
        return os.path.join(self.root, "pid")

    @property
    def sock_file(self) -> str:
        return os.path.join(self.root, "sock")

    @property
    def meta_file(self) -> str:
        return os.path.join(self.root, "meta.json")

    @property
    def log_file(self) -> str:
        return os.path.join(self.root, "daemon.log")


def for_job(job_id: str) -> SessionPaths:
    return SessionPaths(job_id=job_id, root=session_dir(job_id))


def ensure_dir(paths: SessionPaths) -> None:
    os.makedirs(paths.root, mode=0o700, exist_ok=True)


def read_meta(paths: SessionPaths) -> dict | None:
    try:
        with open(paths.meta_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_meta(paths: SessionPaths, meta: dict) -> None:
    tmp = paths.meta_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, paths.meta_file)


def read_pid(paths: SessionPaths) -> int | None:
    try:
        with open(paths.pid_file, encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_pid(paths: SessionPaths, pid: int) -> None:
    with open(paths.pid_file, "w", encoding="ascii") as f:
        f.write(f"{pid}\n")


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def cleanup(paths: SessionPaths) -> None:
    """Remove everything in the session dir (best-effort, idempotent)."""
    for name in ("sock", "pid", "meta.json"):
        try:
            os.unlink(os.path.join(paths.root, name))
        except FileNotFoundError:
            pass
        except OSError:
            pass
    # Keep daemon.log around for postmortem; remove only if empty.
    try:
        if os.path.getsize(paths.log_file) == 0:
            os.unlink(paths.log_file)
    except OSError:
        pass
    try:
        os.rmdir(paths.root)
    except OSError:
        pass


def list_sessions() -> list[SessionPaths]:
    root = sessions_root()
    if not os.path.isdir(root):
        return []
    out: list[SessionPaths] = []
    for name in sorted(os.listdir(root)):
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate):
            out.append(SessionPaths(job_id=name, root=candidate))
    return out
