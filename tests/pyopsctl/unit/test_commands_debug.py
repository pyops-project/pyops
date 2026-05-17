"""End-to-end argparse wiring + request body shape for `pyopsctl debug start`.

Verifies the load-bearing request the CLI builds:
  PUT /scriptlet/job/item
  body = {"set": …, "name": …, "kind": "debug", "param": <composed>,
          "drop_done": False, "max_retries": 0, "overrides"?: {…}}

`param` is composed of: the remote-pdb bootstrap + (optional) user param
body separated by a newline. The port plugged into the bootstrap is
leased from the profile's `debug_ports` pool.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


def _run_pyopsctl(argv, profile, mock_put_response=None, tmp_lease_dir=None):
    """Invoke pyopsctl.__main__.main with argv, with config.load() short-
    circuited to return the given Profile, a single MagicMock Client
    capturing every PUT/GET it sees, and the port-lease dir pointed at
    a tmp path so tests don't trample each other."""
    from pyopsctl import __main__ as cli

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.put.return_value = mock_put_response or {"job": "JOBID123"}
    client_mock.get.return_value = {"id": "JOBID123", "state": "running"}

    env_overrides = {}
    if tmp_lease_dir is not None:
        env_overrides["PYOPS_DEBUG_PORTS_DIR"] = str(tmp_lease_dir)

    with patch.dict(os.environ, env_overrides, clear=False), \
         patch("pyopsctl.commands.debug.load", return_value=profile), \
         patch("pyopsctl.commands.debug.Client", return_value=client_mock):
        exit_code = cli.main(argv)

    return exit_code, client_mock


def _profile(*, debug_ports=(45679,)):
    from pyopsctl.config import Profile
    return Profile(name="t", url="http://server", user="ralph", api_key="",
                   debug_ports=tuple(debug_ports))


def test_debug_start_default_composes_pdb_bootstrap(tmp_path):
    """No --param → param is exactly the pdb bootstrap, with the leased
    port plugged in."""
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--json"],
        _profile(debug_ports=(45679,)),
        tmp_lease_dir=tmp_path,
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["set"] == "myset"
    assert body["name"] == "myscript"
    assert body["kind"] == "debug"
    assert body["drop_done"] is False
    assert body["max_retries"] == 0
    assert "RemotePdb('0.0.0.0', 45679).set_trace()" in body["param"]
    assert body["param"].startswith("#!pip remote-pdb\n")


def test_debug_start_user_param_appended_below_bootstrap(tmp_path):
    """User --param is appended after the bootstrap, separated by a
    blank line, so the user's preamble runs once pdb is attached."""
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript",
         "--param", "x = 1\nprint(x)", "--json"],
        _profile(),
        tmp_lease_dir=tmp_path,
    )
    assert rc == 0
    param = c.put.call_args.kwargs["json_body"]["param"]
    assert param.startswith("#!pip remote-pdb\n")
    assert param.endswith("x = 1\nprint(x)")


def test_debug_start_param_file_reads_from_disk(tmp_path):
    f = tmp_path / "preamble.py"
    f.write_text("import sys\nprint(sys.version)\n")
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript",
         "--param-file", str(f), "--json"],
        _profile(),
        tmp_lease_dir=tmp_path,
    )
    assert rc == 0
    param = c.put.call_args.kwargs["json_body"]["param"]
    assert "import sys" in param
    assert param.startswith("#!pip remote-pdb\n")


def test_debug_start_overrides_dir_walks_set_subdir(tmp_path):
    set_dir = tmp_path / "myset"
    set_dir.mkdir()
    (set_dir / "myscript.task.py").write_text("x = 1\n")
    (set_dir / "helper.library.py").write_text("def f(): pass\n")
    lease_dir = tmp_path / "leases"

    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript",
         "--overrides-dir", str(tmp_path), "--json"],
        _profile(),
        tmp_lease_dir=lease_dir,
    )
    assert rc == 0
    body = c.put.call_args.kwargs["json_body"]
    assert body["overrides"] == {
        "myset::myscript": "x = 1\n",
        "myset::helper": "def f(): pass\n",
    }


def test_debug_start_param_and_param_file_are_mutually_exclusive(tmp_path):
    f = tmp_path / "p.py"
    f.write_text("x = 1\n")
    with pytest.raises(SystemExit):
        _run_pyopsctl(
            ["debug", "start", "myset", "myscript",
             "--param", "y = 2", "--param-file", str(f), "--json"],
            _profile(),
            tmp_lease_dir=tmp_path,
        )


def test_debug_start_no_ports_in_pool_fails_cleanly(tmp_path, capsys):
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--json"],
        _profile(debug_ports=()),
        tmp_lease_dir=tmp_path,
    )
    assert rc != 0
    # Server was never hit because the lease failed before enqueue.
    c.put.assert_not_called()


def test_debug_start_pool_exhausted_fails_cleanly(tmp_path):
    """When the only port in the pool is already leased to another job,
    we fail without hitting the server."""
    from pyopsctl import ports
    lease_dir = tmp_path / "leases"
    with patch.dict(os.environ, {"PYOPS_DEBUG_PORTS_DIR": str(lease_dir)}):
        # Pre-lease the single port.
        ports.lease("t", (45679,), "OTHER_JOB", is_stale=lambda _j: False)

    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--json"],
        _profile(debug_ports=(45679,)),
        tmp_lease_dir=lease_dir,
    )
    assert rc != 0
    c.put.assert_not_called()


def test_debug_start_lease_rewritten_to_real_job_id(tmp_path):
    """After the server returns the real job_id, the lease file
    contents are rewritten so subsequent attach/stop can find this
    job's port by reverse lookup."""
    from pyopsctl import ports
    lease_dir = tmp_path / "leases"
    rc, c = _run_pyopsctl(
        ["debug", "start", "myset", "myscript", "--json"],
        _profile(debug_ports=(45679,)),
        mock_put_response={"job": "REAL_JOB_ID"},
        tmp_lease_dir=lease_dir,
    )
    assert rc == 0
    with patch.dict(os.environ, {"PYOPS_DEBUG_PORTS_DIR": str(lease_dir)}):
        leases = ports.list_leases("t")
    assert [l.job_id for l in leases] == ["REAL_JOB_ID"]
    assert [l.port for l in leases] == [45679]
