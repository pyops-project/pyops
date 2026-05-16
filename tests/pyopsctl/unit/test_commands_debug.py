"""End-to-end argparse wiring + request body shape for `pyopsctl debug start`.

Verifies the load-bearing request the CLI builds:
  PUT /scriptlet/job/item
  body = {"set": …, "name": …, "kind": "debug", "param": <merged>,
          "drop_done": False, "max_retries": 0, "overrides"?: {…}}

The merge of `--await` + `--param`/`--param-file` is the bit VSIX can't
do; the test_debug_param_merge module covers the merge function itself —
this module verifies it's wired into the actual PUT body that goes on
the wire.
"""
import os
from unittest.mock import MagicMock, patch


def _run_pyopsctl(argv, profile, mock_put_response=None):
    """Invoke pyopsctl.__main__.main with argv, with config.load() short-
    circuited to return the given Profile and a single MagicMock Client
    capturing every PUT/GET it sees."""
    from pyopsctl import __main__ as cli

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.put.return_value = mock_put_response or {"job": "JOBID123"}
    client_mock.get.return_value = {"port": 45678}

    with patch("pyopsctl.commands.debug.load", return_value=profile), \
         patch("pyopsctl.commands.debug.Client", return_value=client_mock):
        exit_code = cli.main(argv)

    return exit_code, client_mock


def _profile():
    from pyopsctl.config import Profile
    return Profile(name="t", url="http://server", user="ralph", api_key="")


def test_debug_start_detached_default_emits_empty_param():
    """No --await, no --param → param is empty string. Kind is debug,
    drop_done=False, max_retries=0, no overrides key."""
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--json"],
        _profile(),
    )
    assert rc == 0
    args, kwargs = c.put.call_args
    assert args[0] == "/scriptlet/job/item"
    body = kwargs["json_body"]
    assert body == {
        "set": "myset",
        "name": "myscript",
        "kind": "debug",
        "param": "",
        "drop_done": False,
        "max_retries": 0,
    }


def test_debug_start_await_only_injects_directive():
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--await", "--json"],
        _profile(),
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["param"] == "#>AWAIT_DEBUG"


def test_debug_start_param_only_passes_verbatim():
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--param", "x = 1\nprint(x)", "--json"],
        _profile(),
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["param"] == "x = 1\nprint(x)"


def test_debug_start_await_plus_param_merges_with_newline_separator():
    """The contract VSIX can't reach: `--await` + `--param` produces
    `#>AWAIT_DEBUG\\n<user>` so the user's script + embedded directives
    stay valid below the directive line."""
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--await",
         "--param", "#!pip somepkg\nx = 1", "--json"],
        _profile(),
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["param"].startswith("#>AWAIT_DEBUG\n")
    assert "\n#!pip somepkg\n" in body["param"]
    assert body["param"].endswith("x = 1")


def test_debug_start_param_file_reads_from_disk_and_merges(tmp_path):
    """`--param-file` swap: same merge semantics, content comes from a
    file. Useful when the param is a real Python script the operator
    wants to keep in version control."""
    f = tmp_path / "preamble.py"
    f.write_text("# auto-instrumentation\nimport sys\nprint(sys.version)\n")

    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--await",
         "--param-file", str(f), "--json"],
        _profile(),
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["param"].startswith("#>AWAIT_DEBUG\n")
    assert "import sys" in body["param"]


def test_debug_start_overrides_dir_walks_set_subdir(tmp_path):
    """`--overrides-dir` walks DIR/<set>/*.py and adds an overrides dict
    matching pyops's `^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$` key shape."""
    set_dir = tmp_path / "myset"
    set_dir.mkdir()
    (set_dir / "myscript.task.py").write_text("x = 1\n")
    (set_dir / "helper.library.py").write_text("def f(): pass\n")

    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript",
         "--overrides-dir", str(tmp_path), "--json"],
        _profile(),
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["overrides"] == {
        "myset::myscript": "x = 1\n",
        "myset::helper": "def f(): pass\n",
    }


def test_debug_start_wait_running_polls_then_fetches_port():
    """With --wait-running the command polls /scriptlet/job/item until
    state=running, then GETs /debug/port. The mock differentiates by
    URL so the poll resolves on first hit and the port lookup follows."""
    from pyopsctl import __main__ as cli
    from unittest.mock import MagicMock, patch

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.put.return_value = {"job": "JOBID123"}

    def _get(path, **kw):
        if path == "/scriptlet/job/item":
            return {"id": "JOBID123", "state": "running"}
        if path == "/debug/port":
            return {"port": 45678}
        raise AssertionError(f"unexpected GET {path}")
    client_mock.get.side_effect = _get

    with patch("pyopsctl.commands.debug.load", return_value=_profile()), \
         patch("pyopsctl.commands.debug.Client", return_value=client_mock):
        rc = cli.main([
            "debug", "start", "myset", "myscript",
            "--wait-running", "--await", "--json",
        ])

    assert rc == 0
    assert client_mock.put.call_count == 1
    # First GET is the poll (state), second is the port lookup.
    call_paths = [call.args[0] for call in client_mock.get.call_args_list]
    assert call_paths == ["/scriptlet/job/item", "/debug/port"]


def test_debug_start_param_and_param_file_are_mutually_exclusive(tmp_path):
    """Mutually-exclusive flags raise SystemExit (caught by argparse +
    our explicit check)."""
    f = tmp_path / "p.py"
    f.write_text("x = 1\n")
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        _run_pyopsctl(
            ["debug", "start", "myset", "myscript",
             "--param", "y = 2", "--param-file", str(f), "--json"],
            _profile(),
        )
