"""E2E for the pyopsctl debug surface — verifies the interplay of three
systems: pyopsctl (CLI), pyops (server + runner spawning debugpy), and
a real debugger client (a raw TCP socket here, since the only thing
this test cares about is that the listener side of the contract is up
and accepting connections).

Integration tests already cover what pyopsctl *sends* to the API. This
suite covers what *happens* downstream when pyopsctl actually drives a
real debug job: did the runner open the debug port, did `--await`
translate into a real `wait_for_client()` on the runner side, can an
external client attach.
"""
import json
import socket
import time

import pytest


def _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, name, code):
    run_ctl(pyopsctl_bin, "scriptlet", "create", pyops_set, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "save", pyops_set, name,
            "--stdin", input_text=code)


def _port_is_listening(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """TCP connect probe. True if a process accepts a connection on
    (host, port) within `timeout`. Used to verify debugpy actually
    opened its listener after pyopsctl said it was running."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            # Some servers (debugpy included) hold the connection waiting for
            # protocol bytes. A bare TCP connect succeeding proves the
            # listener is up; we don't speak DAP here.
            return True
    except OSError:
        return False


def _wait_for_listener(host: str, port: int, *, timeout: float = 30.0) -> None:
    """Wait until something accepts TCP connections on (host, port).
    Fails the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_listening(host, port):
            return
        time.sleep(0.5)
    pytest.fail(f"nothing listening on {host}:{port} within {timeout}s")


def _wait_for_no_listener(host: str, port: int, *, timeout: float = 30.0) -> None:
    """Wait until (host, port) stops accepting connections — proof the
    runner cleaned up after `debug stop`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_is_listening(host, port):
            return
        time.sleep(0.5)
    pytest.fail(f"port {port} still listening within {timeout}s — runner did not tear down debugpy")


def test_await_debug_job_opens_real_tcp_listener_on_reported_port(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    """pyopsctl says `--await --wait-running` → port, and that port is
    actually a live listener accepting TCP. Proves the three systems
    cooperate: pyopsctl correctly sent the directive; the runner saw
    `#>AWAIT_DEBUG` and ran `debugpy.listen()`; the listener is on the
    port `/debug/port` reports.
    """
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_attach",
                      "print('hello from debug job')\n")

    # debug start --await --wait-running: pyopsctl polls until state=running,
    # then reads /debug/port and returns the host+port for an attacher.
    result = run_ctl(pyopsctl_bin, "debug", "start", pyops_set, "scr_attach",
                     "--await", "--wait-running", "--json")
    composite = json.loads(result.stdout.strip())
    data = composite["data"]
    port = data["port"]
    job_id = data["job_id"]
    assert port == pyops_server_for_ctl["debug_port"], \
        f"port mismatch: pyopsctl reported {port}, config said {pyops_server_for_ctl['debug_port']}"

    try:
        _wait_for_listener("127.0.0.1", port, timeout=15.0)
        # A second connect should also succeed — debugpy allows reattaching.
        assert _port_is_listening("127.0.0.1", port)
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id)


def test_detached_debug_job_also_opens_listener_without_await(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    """Without --await the runner still calls `debugpy.listen()` — it
    just skips `wait_for_client()`. The detached flow should therefore
    expose the same listening port, ready for a `pyopsctl debug attach`
    later. The contract is: detached means "no wait", not "no listen".
    """
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_detached_e2e",
                      "import time\ntime.sleep(60)\n")

    result = run_ctl(pyopsctl_bin, "debug", "start", pyops_set, "scr_detached_e2e",
                     "--wait-running", "--json")
    composite = json.loads(result.stdout.strip())
    data = composite["data"]
    port = data["port"]
    job_id = data["job_id"]

    try:
        _wait_for_listener("127.0.0.1", port, timeout=15.0)
        # And `pyopsctl debug attach JOBID` should report the same port
        # without re-triggering the runner — the listener is already up.
        attach_result = run_ctl(pyopsctl_bin, "debug", "attach", job_id, "--json")
        attach_payload = json.loads(attach_result.stdout.strip())
        assert attach_payload["port"] == port
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id)


def test_debug_stop_tears_down_listener(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    """Cancelling a debug job has to close the listener so the next
    debug job for the same user can reuse the configured port. Proves
    the runner's debug-job teardown path through to the OS socket."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_teardown",
                      "import time\ntime.sleep(60)\n")
    result = run_ctl(pyopsctl_bin, "debug", "start", pyops_set, "scr_teardown",
                     "--wait-running", "--json")
    data = json.loads(result.stdout.strip())["data"]
    port = data["port"]
    job_id = data["job_id"]

    _wait_for_listener("127.0.0.1", port, timeout=15.0)
    run_ctl(pyopsctl_bin, "debug", "stop", job_id)
    _wait_for_no_listener("127.0.0.1", port, timeout=30.0)
