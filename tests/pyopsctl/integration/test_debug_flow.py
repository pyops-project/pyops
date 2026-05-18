"""End-to-end debug job flow against a real pyops.

Each test:
  1. creates a scriptlet,
  2. invokes `pyopsctl debug start` with various flag combinations,
  3. fetches the job back via `pyopsctl job list --json`,
  4. asserts the resulting `job.param` contains the remote-pdb
     bootstrap (and the user's optional preamble below it).
"""
import json
import os

import pytest


def _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, name, code="print('hi')\n"):
    """Create then save so the scriptlet has body content."""
    run_ctl(pyopsctl_bin, "scriptlet", "create", pyops_set, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "draft", pyops_set, name,
            "--stdin", input_text=code)


def _enqueue_debug(run_ctl, pyopsctl_bin, set_name, name, *extra):
    """Run `debug start --json`, return the job_id."""
    result = run_ctl(pyopsctl_bin, "debug", "start", set_name, name,
                     "--json", *extra)
    payload = json.loads(result.stdout.strip())
    assert payload.get("job_id"), f"no job_id in {payload!r}"
    return payload["job_id"]


def _job_row(run_ctl, pyopsctl_bin, job_id):
    rows = json.loads(run_ctl(pyopsctl_bin, "job", "list", "--json").stdout.strip())
    row = next((r for r in rows if r["id"] == job_id), None)
    assert row is not None, f"job {job_id} not in list"
    return row


def test_debug_start_param_carries_pdb_bootstrap(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_default")
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_default")
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    param = row.get("param") or ""
    assert "#!pip remote-pdb" in param
    assert "RemotePdb('0.0.0.0'," in param
    assert ".set_trace()" in param
    assert row.get("kind") == "debug"


def test_debug_start_user_param_appended_below_bootstrap(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_user")
    user_body = "# preamble\nx = 1\n"
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_user",
                            "--param", user_body)
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    param = row.get("param") or ""
    assert param.startswith("#!pip remote-pdb\n")
    assert param.endswith("x = 1\n")


def test_debug_start_param_file_appended(
    pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, tmp_path,
):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_pfile")
    pfile = tmp_path / "preamble.py"
    pfile.write_text("import sys\nprint(sys.version)\n")
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_pfile",
                            "--param-file", str(pfile))
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    param = row.get("param") or ""
    assert param.startswith("#!pip remote-pdb\n")
    assert "import sys" in param
