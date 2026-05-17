"""Session daemon lifecycle: spawn, ready handshake, control-socket
ops, double-attach refusal, clean teardown.

This is the layer between `debug start` (server-side: enqueue +
runner spawns the job + bootstrap binds the listener) and the
actually-debugging tests in `test_debug_actual_debugging.py`. It
proves the daemon comes up, its UDS is reachable, the `state` op
returns a sane snapshot, and `detach` tears everything down.
"""
import json
import os
import time

import pytest


_BASIC_SCRIPTLET = "print('hi')\n"


def _create_scriptlet(run_ctl, pyopsctl_bin, set_name, name, code):
    run_ctl(pyopsctl_bin, "scriptlet", "create", set_name, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "save", set_name, name,
            "--stdin", input_text=code)


def _attach(run_ctl, pyopsctl_bin, job_id):
    """`debug attach` blocks until the daemon spawns + connects to pdb.
    `PdbClient.connect` retries on ECONNREFUSED so this implicitly
    waits for the listener to come up."""
    return json.loads(run_ctl(pyopsctl_bin, "debug", "attach", job_id,
                              "--json").stdout.strip())


def _session_dir(session_dir_root: str, job_id: str) -> str:
    return os.path.join(session_dir_root, job_id)


def test_attach_spawns_daemon_with_live_pid_and_session_dir(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_isess",
                      _BASIC_SCRIPTLET)
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_isess", "--json"
    ).stdout.strip())
    job_id = started["job_id"]

    try:
        snapshot = _attach(run_ctl, pyopsctl_bin, job_id)
        # `state` snapshot has the canonical shape.
        assert snapshot["state"] in ("stopped", "running")
        if snapshot["state"] == "stopped":
            assert snapshot["frame"] is not None
            assert "line" in snapshot["frame"]

        sd = _session_dir(pyopsctl_env["session_dir"], job_id)
        # pid + meta + sock all exist after a successful attach.
        assert os.path.isfile(os.path.join(sd, "pid"))
        assert os.path.isfile(os.path.join(sd, "meta.json"))
        assert os.path.exists(os.path.join(sd, "sock"))

        with open(os.path.join(sd, "pid")) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # daemon process alive — raises if dead
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_attach_twice_refuses_second(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    """`debug attach` against a job that already has a live session
    refuses with a clear error rather than spawning a second daemon."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_dbl",
                      _BASIC_SCRIPTLET)
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_dbl", "--json"
    ).stdout.strip())
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        # Second attach: refused, non-zero exit, error mentions the existing daemon.
        result = run_ctl(pyopsctl_bin, "debug", "attach", job_id, "--json",
                         check=False)
        assert result.returncode != 0, "second attach should have failed"
        err = (result.stderr + result.stdout).lower()
        assert "already attached" in err or "daemon pid" in err, (
            f"expected refusal message, got stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_detach_tears_daemon_down_and_cleans_session_dir(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_det",
                      _BASIC_SCRIPTLET)
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_det", "--json"
    ).stdout.strip())
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        sd = _session_dir(pyopsctl_env["session_dir"], job_id)
        assert os.path.isfile(os.path.join(sd, "pid"))
        with open(os.path.join(sd, "pid")) as f:
            pid = int(f.read().strip())

        # Detach.
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                "--json")

        # Daemon process gone within a short window.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, OSError):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"session daemon pid {pid} did not exit after detach")

        # Session dir gone (paths.cleanup unlinks files + rmdir's root).
        # daemon.log may linger if non-empty; that's fine.
        assert not os.path.exists(os.path.join(sd, "pid"))
        assert not os.path.exists(os.path.join(sd, "sock"))
    finally:
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_session_list_shows_active_attach(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, pyopsctl_env,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_list",
                      _BASIC_SCRIPTLET)
    started = json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", pyops_set, "scr_list", "--json"
    ).stdout.strip())
    job_id = started["job_id"]
    leased_port = started["port"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        rows = json.loads(run_ctl(
            pyopsctl_bin, "debug", "session", "list", "--json"
        ).stdout.strip())
        match = next((r for r in rows if r["job_id"] == job_id), None)
        assert match is not None, f"job {job_id} missing from {rows}"
        assert match["set"] == pyops_set
        assert match["name"] == "scr_list"
        assert match["pdb_port"] == leased_port
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)
