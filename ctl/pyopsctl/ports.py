"""Per-profile debug-port leasing.

pyopsctl picks a TCP port from the profile's `debug_ports` pool when it
starts a debug job and writes a per-port lease file so concurrent
invocations don't double-allocate the same port. The lease records the
job_id so `debug attach` knows which port belongs to which job and
`debug stop` (or the job ending) returns it to the pool.

Layout:

    $XDG_RUNTIME_DIR/pyops/debug-ports/<profile>/<port>.lease
      JOB_ID

The file is created with O_CREAT|O_EXCL so two concurrent leasers race
deterministically — exactly one wins, the loser retries on the next port
in the pool.

Stale leases (lease file still present but the job is gone) are pruned
by `lease()` itself on the way through: it consults the server's job
list for the profile and drops any lease whose job_id has reached a
terminal state.
"""
import errno
import os
from dataclasses import dataclass


DEFAULT_DIR_RUNTIME = "/run/user"  # only used to construct the fallback path


def _lease_root() -> str:
    explicit = os.environ.get("PYOPS_DEBUG_PORTS_DIR")
    if explicit:
        return explicit
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "pyops", "debug-ports")
    return os.path.expanduser("~/.cache/pyops/debug-ports")


def _profile_dir(profile_name: str) -> str:
    return os.path.join(_lease_root(), _safe(profile_name))


def _safe(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


def _lease_path(profile_name: str, port: int) -> str:
    return os.path.join(_profile_dir(profile_name), f"{port}.lease")


@dataclass
class Lease:
    profile: str
    port: int
    job_id: str
    path: str


class NoPortsAvailable(Exception):
    """The profile's `debug_ports` pool is empty or every port is taken
    by a live lease."""


def lease(profile_name: str, pool: tuple[int, ...] | list[int], job_id: str,
          *, is_stale=lambda _job_id: False) -> Lease:
    """Lease the first free port from `pool` for `job_id`.

    `is_stale(job_id)` is called for each occupied port; returning True
    drops that lease so the port is reclaimed. The caller wires this to
    a check against the server's job list — that's the only place
    that knows whether the job is still alive.

    Raises `NoPortsAvailable` if the pool is empty or fully booked.
    """
    if not pool:
        raise NoPortsAvailable(
            f"profile {profile_name!r} has no debug_ports configured — "
            f"add `debug_ports = [<port>, ...]` to the profile"
        )

    pdir = _profile_dir(profile_name)
    os.makedirs(pdir, mode=0o700, exist_ok=True)

    for port in pool:
        path = _lease_path(profile_name, port)
        # First, try to claim. O_EXCL ensures we win or lose deterministically.
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as ex:
            if ex.errno != errno.EEXIST:
                raise
            # Someone else holds it — check if it's stale.
            try:
                with open(path, encoding="ascii") as f:
                    holder = f.read().strip()
            except OSError:
                # Disappeared between EEXIST and open; treat as free and retry.
                continue
            if holder and is_stale(holder):
                # Reclaim: unlink the stale lease, retry this port.
                try:
                    os.unlink(path)
                except OSError:
                    pass
                # Don't retry inline — the next loop pass will hit the
                # same port and try to O_EXCL it again.
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except OSError:
                    continue
            else:
                continue
        try:
            os.write(fd, job_id.encode("utf-8"))
        finally:
            os.close(fd)
        return Lease(profile=profile_name, port=port, job_id=job_id, path=path)

    raise NoPortsAvailable(
        f"profile {profile_name!r} debug_ports pool is fully leased "
        f"({sorted(pool)}); release a port (cancel the job that holds it) or "
        f"add more ports to the profile"
    )


def release(lease_obj: Lease) -> None:
    """Idempotent: delete the lease file. No-op if already gone."""
    try:
        os.unlink(lease_obj.path)
    except FileNotFoundError:
        pass


def release_for_job(profile_name: str, job_id: str) -> int:
    """Release any port in this profile leased to `job_id`. Returns the
    port number released, or -1 if none. Used by `debug stop` so the
    pool reclaims the port immediately rather than waiting for stale
    sweep on the next lease()."""
    pdir = _profile_dir(profile_name)
    if not os.path.isdir(pdir):
        return -1
    for entry in os.listdir(pdir):
        if not entry.endswith(".lease"):
            continue
        path = os.path.join(pdir, entry)
        try:
            with open(path, encoding="ascii") as f:
                holder = f.read().strip()
        except OSError:
            continue
        if holder == job_id:
            try:
                os.unlink(path)
                return int(entry[: -len(".lease")])
            except (OSError, ValueError):
                return -1
    return -1


def find_port_for_job(profile_name: str, job_id: str) -> int | None:
    """Reverse lookup: which port did we lease for this job? Used by
    `debug attach` to learn the port without depending on the server."""
    pdir = _profile_dir(profile_name)
    if not os.path.isdir(pdir):
        return None
    for entry in os.listdir(pdir):
        if not entry.endswith(".lease"):
            continue
        path = os.path.join(pdir, entry)
        try:
            with open(path, encoding="ascii") as f:
                holder = f.read().strip()
        except OSError:
            continue
        if holder == job_id:
            try:
                return int(entry[: -len(".lease")])
            except ValueError:
                return None
    return None


def list_leases(profile_name: str) -> list[Lease]:
    pdir = _profile_dir(profile_name)
    if not os.path.isdir(pdir):
        return []
    out: list[Lease] = []
    for entry in sorted(os.listdir(pdir)):
        if not entry.endswith(".lease"):
            continue
        path = os.path.join(pdir, entry)
        try:
            with open(path, encoding="ascii") as f:
                holder = f.read().strip()
        except OSError:
            continue
        try:
            port = int(entry[: -len(".lease")])
        except ValueError:
            continue
        out.append(Lease(profile=profile_name, port=port, job_id=holder, path=path))
    return out
