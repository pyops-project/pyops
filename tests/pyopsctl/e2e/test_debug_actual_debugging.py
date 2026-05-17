"""E2E for actual debugger operations: attach → break set → continue →
hit BP → locals/evaluate → step → terminate, driven against a real
remote-pdb + a real running scriptlet.

This is the test the original "test_debug_actual_debugging.py" was for.
If any link in the chain breaks — the param's set_trace() never opens
the listener, the daemon can't read pdb prompts, the friendly-name →
path resolver mis-routes, breakpoints don't fire when continuing into
materialised source — the test fails at that step.

Layout of the scriptlet under .debug/src/<set>/task/<name>.py (the
server-side materialisation):
    1: counter = 0
    2: while True:
    3:     counter = counter + 1     ← break here
    4:     if counter > 10**3:
    5:         break

The loop bound is 10**3 so the program is guaranteed to terminate even
if our breakpoint+continue logic accidentally lets it run unbounded.
"""
import json
import os

import pytest


SCRIPTLET = """\
counter = 0
while True:
    counter = counter + 1
    if counter > 10**3:
        break
"""
BP_LINE = 3


def _create(run_ctl, pyopsctl_bin, set_name, name):
    run_ctl(pyopsctl_bin, "scriptlet", "create", set_name, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "save", set_name, name,
            "--stdin", input_text=SCRIPTLET)


def _start(run_ctl, pyopsctl_bin, set_name, name):
    return json.loads(run_ctl(
        pyopsctl_bin, "debug", "start", set_name, name, "--json"
    ).stdout.strip())


def _attach(run_ctl, pyopsctl_bin, job_id):
    return json.loads(run_ctl(
        pyopsctl_bin, "debug", "attach", job_id, "--json"
    ).stdout.strip())


def test_break_continue_hits_user_bp_with_locals_evaluate_step(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_debug")
    started = _start(run_ctl, pyopsctl_bin, pyops_set, "scr_debug")
    job_id = started["job_id"]

    try:
        # Attach. PdbClient retries the TCP connect until the listener
        # binds (the runner may still be pip-installing remote-pdb into
        # the freshly-created per-set venv).
        attached = _attach(run_ctl, pyopsctl_bin, job_id)
        assert attached["state"] == "stopped", (
            f"attach didn't land at a stop: {attached}"
        )

        # Set the breakpoint in the user scriptlet.
        bp = json.loads(run_ctl(
            pyopsctl_bin, "debug", "break", "set",
            f"{pyops_set}::scr_debug:{BP_LINE}", "--json"
        ).stdout.strip())
        assert bp.get("verified") is True, f"break set didn't verify: {bp}"
        assert bp.get("line") == BP_LINE

        # Continue. Pdb releases set_trace; param exec returns;
        # execute_job_code starts the user scriptlet; the breakpoint
        # fires at line BP_LINE.
        at_bp = json.loads(run_ctl(
            pyopsctl_bin, "debug", "continue", "--json"
        ).stdout.strip())
        assert at_bp.get("state") == "stopped", (
            f"continue didn't stop at the bp: {at_bp}"
        )
        frame = at_bp.get("frame") or {}
        assert frame.get("source", {}).get("set") == pyops_set
        assert frame.get("source", {}).get("name") == "scr_debug"
        assert frame.get("line") == BP_LINE, (
            f"stopped at wrong line: {frame.get('line')} (expected {BP_LINE})"
        )

        # locals: counter must be present and an integer >= 1
        # (line 3 increments counter; the body has already executed
        # at least once by the time we land back at line 3).
        locals_payload = json.loads(run_ctl(
            pyopsctl_bin, "debug", "locals", "--json"
        ).stdout.strip())
        names = {v["name"] for v in locals_payload.get("locals", [])}
        assert "counter" in names, f"counter not in locals: {names}"

        # evaluate: counter + 100 — pdb's `p` returns the int repr.
        ev = json.loads(run_ctl(
            pyopsctl_bin, "debug", "evaluate", "counter + 100", "--json"
        ).stdout.strip())
        # `value` is pdb's repr of the result — int has no quotes.
        assert ev["value"].lstrip("-").isdigit(), (
            f"evaluate counter+100 not an integer repr: {ev}"
        )
        assert int(ev["value"]) >= 100

        # step over: lands on the next executable line (4) or wraps
        # back to the loop header (2).
        stepped = json.loads(run_ctl(
            pyopsctl_bin, "debug", "step", "--json"
        ).stdout.strip())
        assert stepped.get("state") == "stopped"
        assert stepped["frame"]["source"]["name"] == "scr_debug"
        assert stepped["frame"]["line"] != BP_LINE, (
            "step didn't advance off the bp line"
        )

        # terminate: send pdb `q`. The job ends.
        run_ctl(pyopsctl_bin, "debug", "terminate", "--json")
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_source_returns_full_scriptlet_text(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    """`debug source <set>::<name>` reads from
    `GET /scriptlet/inventory/code` (not from pdb), so it works even
    before any stop has happened — and round-trips the bytes the
    operator just saved."""
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_src")
    started = _start(run_ctl, pyopsctl_bin, pyops_set, "scr_src")
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        src = json.loads(run_ctl(
            pyopsctl_bin, "debug", "source",
            f"{pyops_set}::scr_src", "--json"
        ).stdout.strip())
        text = "\n".join(l["text"] for l in src["lines"])
        assert "counter = counter + 1" in text
        assert src["set"] == pyops_set
        assert src["name"] == "scr_src"
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)


def test_break_set_with_unknown_friendly_raises_clearly(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    """If the operator asks for a breakpoint in a set::name that
    pyops doesn't have on disk yet, the daemon should refuse with
    a clear error rather than mis-routing or silently accepting."""
    _create(run_ctl, pyopsctl_bin, pyops_set, "scr_known")
    started = _start(run_ctl, pyopsctl_bin, pyops_set, "scr_known")
    job_id = started["job_id"]

    try:
        _attach(run_ctl, pyopsctl_bin, job_id)
        # Note: the friendly name passes the resolver because we derive
        # the path from the .debug/src layout convention, but pdb itself
        # will refuse to set the breakpoint because the file doesn't
        # exist. Either way we expect a non-zero exit and an error.
        result = run_ctl(
            pyopsctl_bin, "debug", "break", "set",
            f"{pyops_set}::scr_does_not_exist:1", "--json", check=False,
        )
        # Either the resolver refused or pdb's `b` command refused.
        assert result.returncode != 0, (
            f"break set on unknown scriptlet should fail; got rc=0, "
            f"stdout={result.stdout!r}"
        )
    finally:
        run_ctl(pyopsctl_bin, "debug", "detach", "--job-id", job_id,
                check=False)
        run_ctl(pyopsctl_bin, "debug", "stop", job_id, check=False)
