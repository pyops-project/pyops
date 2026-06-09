"""Argparse wiring + request body shape for `pyopsctl job run`.

Verifies the load-bearing request the CLI builds:
  PUT /scriptlet/job/item
  body = {"set": …, "name": …, "kind": …, "param": …,
          "drop_done": …, "max_retries": …, "overrides"?: {…}}

The `overrides` key is only included when --overrides-dir is given and
the kind permits it (debug | test) — see `pyopsctl.overrides` and the
server-side acceptance contract in `app/routers/scriptlet/job.py`.
"""
from unittest.mock import MagicMock, patch

import pytest


def _run_pyopsctl(argv, mock_put_response=None):
    """Drive `pyopsctl.__main__.main(argv)` with a MagicMock Client.

    Returns (exit_code, client_mock) — the Client mock records every
    PUT/GET call site so tests can assert on the request body.
    """
    from pyopsctl import __main__ as cli
    from pyopsctl.config import Profile

    profile = Profile(name="t", url="http://server", user="ralph", api_key="",
                      debug_ports=())

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.put.return_value = mock_put_response or {"job": "JOBID123"}

    with patch("pyopsctl.commands.job.load", return_value=profile), \
         patch("pyopsctl.commands.job.Client", return_value=client_mock):
        exit_code = cli.main(argv)
    return exit_code, client_mock


def test_run_default_task_kind_no_overrides_key():
    """`job run` without --overrides-dir submits a body without the
    `overrides` key — the server treats absent and {} differently for
    the existence-check bypass."""
    rc, c = _run_pyopsctl(
        ["job", "run", "myset", "myscript", "--json"],
    )
    assert rc in (0, None)  # `_run` returns implicitly; sys.exit(None) == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["kind"] == "task"
    assert "overrides" not in body


def test_run_kind_test_with_overrides_dir_walks_set_subdir(tmp_path):
    """--kind test + --overrides-dir packs every <set>/<name>.<type>.py
    file under DIR/<set>/ into the overrides map, keyed `set::name`.

    Matches the debug-start shape — same shared helper, same wire
    format on the server side."""
    set_dir = tmp_path / "myset"
    set_dir.mkdir()
    (set_dir / "myscript.task.py").write_text("x = 1\n")
    (set_dir / "helper.library.py").write_text("def f(): pass\n")

    rc, c = _run_pyopsctl(
        ["job", "run", "myset", "myscript",
         "--kind", "test", "--overrides-dir", str(tmp_path), "--json"],
    )
    assert rc in (0, None)  # `_run` returns implicitly; sys.exit(None) == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["kind"] == "test"
    assert body["overrides"] == {
        "myset::myscript": "x = 1\n",
        "myset::helper": "def f(): pass\n",
    }


def test_run_kind_test_without_overrides_dir_omits_overrides_key():
    """`--kind test` alone enqueues against the committed tree — no
    overrides key in the body. (Matches debug-start's behaviour when
    --overrides-dir is absent.)"""
    rc, c = _run_pyopsctl(
        ["job", "run", "myset", "myscript", "--kind", "test", "--json"],
    )
    assert rc in (0, None)  # `_run` returns implicitly; sys.exit(None) == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["kind"] == "test"
    assert "overrides" not in body


def test_run_overrides_dir_with_non_override_kind_rejected(tmp_path):
    """The server only consumes `overrides` for kind in (debug, test).
    Reject the combination client-side rather than silently shipping a
    payload the server will drop."""
    with pytest.raises(SystemExit, match="--overrides-dir"):
        _run_pyopsctl(
            ["job", "run", "myset", "myscript",
             "--kind", "task", "--overrides-dir", str(tmp_path), "--json"],
        )


def test_run_kind_test_overrides_dir_missing_set_yields_empty_map(tmp_path):
    """`overrides_dir` set, but no `<set>/` subdir present: ship an
    explicit empty overrides map. Server treats {} the same as a map
    with no matching key (falls back to git for the main scriptlet),
    but the explicit empty signals operator intent."""
    rc, c = _run_pyopsctl(
        ["job", "run", "myset", "myscript",
         "--kind", "test", "--overrides-dir", str(tmp_path), "--json"],
    )
    assert rc in (0, None)  # `_run` returns implicitly; sys.exit(None) == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["overrides"] == {}


def test_list_filter_by_set_matches_rows_with_set_field():
    """Regression: `pyopsctl job list --set <name>` filters on
    `row.get("set") == args.set`. That match only works if the server
    actually returns the `set` field — pre-3.3.0 the
    `/scriptlet/job/list` SELECT omitted it, so every `--set`
    invocation silently returned zero rows even when the job existed.

    Mocks the API response with `set` / `name` fields populated (the
    post-fix shape) and asserts the CLI keeps the matching row.
    """
    rows = [
        {"id": "j1", "set": "cami", "name": "causal_service", "kind": "service", "state": "running", "owner": "u"},
        {"id": "j2", "set": "myset", "name": "other",          "kind": "task",    "state": "done",    "owner": "u"},
    ]
    from pyopsctl import __main__ as cli
    from pyopsctl.config import Profile
    profile = Profile(name="t", url="http://server", user="u", api_key="",
                      debug_ports=())
    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.get.return_value = rows
    emitted = []
    with patch("pyopsctl.commands.job.load", return_value=profile), \
         patch("pyopsctl.commands.job.Client", return_value=client_mock), \
         patch("pyopsctl.commands.job.emit",
               side_effect=lambda args, data, **kw: emitted.append(data)):
        cli.main(["job", "list", "--set", "cami", "--json"])

    client_mock.get.assert_called_with("/scriptlet/job/list")
    assert emitted, "emit not called"
    filtered = emitted[0]
    assert len(filtered) == 1, f"--set filter must keep cami row only; got {filtered!r}"
    assert filtered[0]["id"] == "j1"
    assert filtered[0]["set"] == "cami"
    assert filtered[0]["name"] == "causal_service"


def test_run_param_file_combines_with_overrides(tmp_path):
    """--param-file and --overrides-dir coexist: param gets the file
    body, overrides get the working-copy scriptlets."""
    set_dir = tmp_path / "myset"
    set_dir.mkdir()
    (set_dir / "myscript.task.py").write_text("y = 2\n")
    param_file = tmp_path / "preamble.py"
    param_file.write_text("setup = True\n")

    rc, c = _run_pyopsctl(
        ["job", "run", "myset", "myscript",
         "--kind", "test", "--overrides-dir", str(tmp_path),
         "--param-file", str(param_file), "--json"],
    )
    assert rc in (0, None)  # `_run` returns implicitly; sys.exit(None) == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["param"] == "setup = True\n"
    assert body["overrides"] == {"myset::myscript": "y = 2\n"}


def test_view_renders_kind_and_param(capsys):
    """`pyopsctl job view` surfaces `kind` and `param` in human output.

    Depends on `/scriptlet/job/item` returning those fields (3.3.2 — the
    detail endpoint previously omitted name/kind/param, so the view
    rendered an empty name and showed neither kind nor param)."""
    job = {
        "id": "JOBID123", "set": "reporting", "name": "export_report",
        "kind": "draft", "state": "done", "owner": "u",
        "param": "customer = '000000_sysup'", "detail": None,
        "stdout": None, "stderr": None,
    }
    from pyopsctl import __main__ as cli
    from pyopsctl.config import Profile
    profile = Profile(name="t", url="http://server", user="u", api_key="",
                      debug_ports=())
    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.get.return_value = job
    with patch("pyopsctl.commands.job.load", return_value=profile), \
         patch("pyopsctl.commands.job.Client", return_value=client_mock):
        cli.main(["job", "view", "JOBID123"])

    out = capsys.readouterr().out
    assert "set/name: reporting::export_report" in out
    assert "kind:     draft" in out
    assert "customer = '000000_sysup'" in out
