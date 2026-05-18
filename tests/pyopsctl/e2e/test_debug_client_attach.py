"""TCP-level e2e: leased port + remote-pdb listener actually opens on it.

Verifies the *cross-system* contract: pyopsctl picks a port from the
profile pool, the runner brings up a job whose `param` calls
`RemotePdb(host, port).set_trace()`, and a raw TCP connect to that
port succeeds. `debug stop` releases the lease and the job is gone on
the next port-reuse attempt.

These tests pay the per-set-venv setup cost (the runner pip-installs
`remote-pdb` into the freshly-created venv on first job in the set),
so allow generous timeouts.
"""
import json
import os
import socket
import time

import pytest


# A short scriptlet that exits quickly after `continue` is issued (or
# after pdb's EOF-on-disconnect releases set_trace). Keeps post-attach
# state clean for the test's assertions.
_BASIC_SCRIPTLET = "print('hi from scriptlet')\n"


def _create_scriptlet(run_ctl, pyopsctl_bin, set_name, name, code):
    run_ctl(pyopsctl_bin, "scriptlet", "create", set_name, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "draft", set_name, name,
            "--stdin", input_text=code)


def _wait_tcp_listen(host: str, port: int, *, timeout: float = 180.0,
                     interval: float = 0.5) -> bool:
    """Poll TCP connect until accepted, or return False after timeout.
    Closes the connection immediately after success — the caller is
    only proving the listener is bound."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=2.0)
            s.close()
            return True
        except ConnectionRefusedError:
            time.sleep(interval)
        except (socket.timeout, OSError):
            time.sleep(interval)
    return False


def _wait_tcp_refused(host: str, port: int, *, timeout: float = 60.0,
                      interval: float = 0.5) -> bool:
    """Poll until TCP connect is refused (= listener gone), or False
    on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            s.close()
        except ConnectionRefusedError:
            return True
        except (socket.timeout, OSError):
            pass
        time.sleep(interval)
    return False


def _lease_files(leases_dir: str, profile: str = "local") -> list[str]:
    p = os.path.join(leases_dir, profile)
    if not os.path.isdir(p):
        return []
    return [f for f in os.listdir(p) if f.endswith(".lease")]


def test_debug_start_leases_port_and_opens_listener(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    """`debug start` writes a lease file under the profile dir and the
    job's `param` brings up an accept()-ing TCP listener on the leased
    port."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_listen",
                      _BASIC_SCRIPTLET)

    # Pool is empty before start.
    assert _lease_files(pyopsctl_env["leases_dir"]) == []

    result = run_ctl(pyopsctl_bin, "debug", "start", pyops_set, "scr_listen",
                     "--json")
    payload = json.loads(result.stdout.strip())
    job_id = payload["job_id"]
    port = payload["port"]
    host = payload["host"]
    assert port in pyopsctl_env["debug_ports"], \
        f"leased port {port} not in pool {pyopsctl_env['debug_ports']}"

    try:
        # Lease file exists under profile=local with the port in the name.
        files = _lease_files(pyopsctl_env["leases_dir"])
        assert f"{port}.lease" in files, \
            f"lease file for port {port} not found in {files}"

        # The remote-pdb listener actually came up. First-time path on a
        # fresh venv pulls remote-pdb from the index — generous timeout.
        assert _wait_tcp_listen(host, port, timeout=180.0), (
            f"remote-pdb listener never came up on {host}:{port}; "
            f"check server.log at {pyops_server_for_ctl['log_path']}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_debug_stop_releases_lease(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    """After `debug stop`, the lease file is gone and the port is
    available to the next `debug start`."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_stop",
                      _BASIC_SCRIPTLET)

    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_stop", "--json"
    ).stdout.strip())
    job_id = started["job_id"]
    port = started["port"]

    try:
        # Wait for the listener so we know the runner actually got into
        # the param block; stopping before that risks racing the spawn.
        host = started["host"]
        assert _wait_tcp_listen(host, port, timeout=180.0)

        # Stop: cancel the job AND release the lease.
        stopped = json.loads(run_ctl(
            pyopsctl_bin, "debug", "stop", job_id, "--json"
        ).stdout.strip())
        assert stopped.get("released_port") == port

        # Lease file gone.
        files = _lease_files(pyopsctl_env["leases_dir"])
        assert f"{port}.lease" not in files
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_pool_first_port_reused_after_stop(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    """After releasing the first port, the next `debug start` picks
    the same port back up — the pool walks in order, first free wins."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_reuse",
                      _BASIC_SCRIPTLET)

    first = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_reuse", "--json"
    ).stdout.strip())
    first_port = first["port"]
    assert first_port == pyopsctl_env["debug_ports"][0]
    run_ctl(pyopsctl_bin, "debug", "stop", first["job_id"], "--json")

    second = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_reuse", "--json"
    ).stdout.strip())
    try:
        assert second["port"] == first_port, (
            f"expected reuse of {first_port}, got {second['port']}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", second["job_id"], check=False)


def test_pool_walks_to_next_port_when_first_held(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    """Two concurrent debug jobs lease two different ports, in pool
    order. Validates the no-double-allocation contract."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_a",
                      _BASIC_SCRIPTLET)
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_b",
                      _BASIC_SCRIPTLET)

    a = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_a", "--json"
    ).stdout.strip())
    b = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_b", "--json"
    ).stdout.strip())
    try:
        assert a["port"] == pyopsctl_env["debug_ports"][0]
        assert b["port"] == pyopsctl_env["debug_ports"][1]
        assert a["port"] != b["port"]
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", a["job_id"], check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", b["job_id"], check=False)
