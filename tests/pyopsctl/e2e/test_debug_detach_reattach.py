"""Detach + (no-)re-attach behaviour for debug sessions.

v1 contract:
  - `debug detach` closes the pdb session daemon cleanly; the session
    dir is removed.
  - The underlying job either continues (pdb returns from set_trace
    after EOF, the program runs to completion) or has already
    terminated — either way pyopsctl observes a sane state for
    `session list` (the entry is gone) and `job list` (the job exists
    with a terminal or running state).
  - Re-attach after detach is not supported in v1 because remote-pdb's
    `set_trace()` is one-shot. A second `debug attach` against the
    same job either fails cleanly (listener gone) or — if the job
    happened to call `set_trace()` again somehow — would succeed.
    Our bootstrap calls `set_trace()` exactly once, so re-attach
    against the same job always fails after detach.
"""
import json
import os
import socket
import time

import pytest


# Long-running scriptlet — gives the test a window where the job is
# still alive after detach, so we can observe the "job continued" state.
_LONG_SCRIPTLET = """\
import time
for _ in range(300):
    time.sleep(0.1)
"""


def _create(run_ctl, pyopsctl_bin, set_name, name, code=_LONG_SCRIPTLET):
    run_ctl(pyopsctl_bin, "scriptlet", "create", set_name, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "save", set_name, name,
            "--stdin", input_text=code)


def _attach(run_ctl, pyopsctl_bin, job_id):
    return json.loads(run_ctl(
        pyopsctl_bin, "debug", "attach", job_id, "--json"
    ).stdout.strip())


def test_detach_removes_session_dir_and_listener_is_gone(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_det1")
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_det1", "--json"
    ).stdout.strip())
    job_id = started["job_id"]
    port = started["port"]
    host = started["host"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        # Detach + cleanup
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id, "--json")

        # Session dir gone.
        sd = os.path.join(pyopsctl_env["session_dir"], job_id)
        # tolerant: paths.cleanup unlinks files + rmdirs root; the root
        # may linger briefly under the parent.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not os.path.isfile(os.path.join(sd, "pid")):
                break
            time.sleep(0.1)
        assert not os.path.isfile(os.path.join(sd, "pid")), (
            f"session pid file still present after detach: {sd}"
        )

        # The remote-pdb listener accepted exactly one connection
        # (the daemon's). After detach, that connection is closed; the
        # listener is gone too (RemotePdb closes the listen socket
        # after accept()). A fresh TCP connect refuses.
        deadline = time.monotonic() + 5.0
        refused = False
        while time.monotonic() < deadline:
            try:
                s = socket.create_connection((host, port), timeout=0.5)
                s.close()
            except ConnectionRefusedError:
                refused = True
                break
            except (socket.timeout, OSError):
                pass
            time.sleep(0.2)
        assert refused, (
            f"port {port} still accepts connections after detach"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_session_list_no_longer_shows_detached(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_det2")
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_det2", "--json"
    ).stdout.strip())
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        rows = json.loads(run_ctl(
            pyopsctl_bin, "debug", "session", "list", "--json"
        ).stdout.strip())
        assert any(r["job_id"] == job_id for r in rows), (
            f"job {job_id} expected in session list before detach"
        )

        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id, "--json")

        # session list prunes dead-session dirs.
        rows = json.loads(run_ctl(
            pyopsctl_bin, "debug", "session", "list", "--json"
        ).stdout.strip())
        assert not any(r["job_id"] == job_id for r in rows), (
            f"job {job_id} unexpectedly still in session list "
            f"after detach: {rows}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_reattach_after_detach_refused_because_listener_one_shot(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
    monkeypatch,
):
    """Documented v1 behaviour: `set_trace()` accepts exactly one
    connection. A second `debug attach` after detach has no listener
    to talk to and should fail cleanly (rather than spinning forever
    or panicking).

    We lower `PYOPSCTL_PDB_CONNECT_RETRY` to 5 seconds for the second
    attach so it fails fast — the default 120s retry window is right
    for *first* attach (where the listener might still be coming up
    behind a venv install) but pointless when we already know the
    listener is gone.
    """
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_re")
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_re", "--json"
    ).stdout.strip())
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id, "--json")

        monkeypatch.setenv("PYOPSCTL_PDB_CONNECT_RETRY", "5")
        result = run_ctl(pyopsctl_bin, "debug", "attach", job_id, "--json",
                         check=False)
        assert result.returncode != 0, (
            f"re-attach unexpectedly succeeded: {result.stdout!r}"
        )
        # Error should mention either the listener never coming up or
        # the connection being refused — the operator-readable hint
        # that this is a one-shot listener.
        err = (result.stderr + result.stdout).lower()
        assert ("did not come up" in err or "refused" in err
                or "cannot connect" in err), (
            f"unexpected re-attach failure shape: {result.stderr!r}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)
