"""End-to-end debug job flow with the `#>AWAIT_DEBUG\\n + user_param`
merge — the load-bearing piece of pyopsctl's debug surface, the bit VSIX
can't do.

Each test:
  1. creates a scriptlet,
  2. invokes `pyopsctl debug start` with various flag combinations,
  3. fetches the job back via `pyopsctl job list --json`,
  4. asserts the resulting `job.param` matches what `_merge_debug_param`
     promised: detached (empty), await-only, param-only, or merged with
     a `#>AWAIT_DEBUG\\n` prefix preserving the user's multi-line script.
"""
import json
import time

import pytest


def _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, name, code="print('hi')\n"):
    """Create then draft+save so the scriptlet has body content."""
    run_ctl(pyopsctl_bin, "scriptlet", "create", pyops_set, name,
            "--kind", "task")
    run_ctl(pyopsctl_bin, "scriptlet", "save", pyops_set, name,
            "--stdin", input_text=code)


def _enqueue_debug(run_ctl, pyopsctl_bin, set_name, name, *extra):
    """Run `debug start --json`, return the job_id."""
    result = run_ctl(pyopsctl_bin, "debug", "start", set_name, name,
                     "--json", *extra)
    payload = json.loads(result.stdout.strip())
    assert payload.get("job_id"), f"no job_id in {payload!r}"
    return payload["job_id"]


def _job_row(run_ctl, pyopsctl_bin, job_id):
    """Pull the matching row from `pyopsctl job list --json`. The list
    endpoint exposes `param`, which the per-id GET endpoint does not."""
    rows = json.loads(run_ctl(pyopsctl_bin, "job", "list", "--json").stdout.strip())
    row = next((r for r in rows if r["id"] == job_id), None)
    assert row is not None, f"job {job_id} not in list"
    return row


def test_debug_detached_default_emits_empty_param(pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_detached")
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_detached")
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    assert row.get("param", "") == ""
    assert row.get("kind") == "debug"


def test_debug_await_only_param_is_directive(pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_awaitonly")
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_awaitonly", "--await")
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    assert row.get("param") == "#>AWAIT_DEBUG"


def test_debug_param_only_passes_verbatim(pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set):
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_paramonly")
    body = "x = 1\nprint(x)\n"
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_paramonly", "--param", body)
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    assert row.get("param") == body


def test_debug_await_plus_param_merges_with_newline(pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set):
    """The load-bearing assertion: --await + --param produces
    `#>AWAIT_DEBUG\\n<user>` so the user's script and any embedded
    `#`-directives stay valid below the directive line."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_merge")
    user_param = "#!pip somepkg\n# preamble\nx = 1\n"
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_merge",
                            "--await", "--param", user_param)
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    param = row.get("param", "")
    assert param.startswith("#>AWAIT_DEBUG\n")
    assert "\n#!pip somepkg\n" in param  # embedded directive survives
    assert param.endswith("x = 1\n")


def test_debug_param_file_merges_same_as_param(pyopsctl_bin, run_ctl, pyops_server_for_ctl, pyops_set, tmp_path):
    """Same merge rule via --param-file: the file content takes the
    user-param slot."""
    _create_scriptlet(run_ctl, pyopsctl_bin, pyops_set, "scr_pfile")
    pfile = tmp_path / "preamble.py"
    pfile.write_text("# from file\nimport sys\nprint(sys.version)\n")
    job_id = _enqueue_debug(run_ctl, pyopsctl_bin, pyops_set, "scr_pfile",
                            "--await", "--param-file", str(pfile))
    row = _job_row(run_ctl, pyopsctl_bin, job_id)
    param = row.get("param", "")
    assert param.startswith("#>AWAIT_DEBUG\n")
    assert "# from file" in param and "import sys" in param
