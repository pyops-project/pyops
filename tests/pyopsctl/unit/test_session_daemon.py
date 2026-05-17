"""Unit tests for ctl/pyopsctl/session/daemon.py helpers.

Covers the pure-function pieces in isolation (no real pdb, no real
HTTP) and the `_op_*` dispatchers against a stub PdbClient. End-to-end
behaviour with a real remote-pdb listener is in
tests/pyopsctl/e2e/test_debug_actual_debugging.py — this module is the
fast unit-level safety net.
"""
import json

import pytest


# ----------------------------------------------------------------------------
# _friendly_from_path
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("path, expected", [
    (
        "/data/jobs/myset/JOBID/.debug/src/myset/task/scr.py",
        "myset::scr",
    ),
    (
        "/data/jobs/foo/abc_def_20260517T120000123456/"
        ".debug/src/foo/library/util.py",
        "foo::util",
    ),
    (
        "/anywhere/else/.debug/src/with-dashes/task/snake_case.py",
        "with-dashes::snake_case",
    ),
])
def test_friendly_from_path_extracts_set_and_scriptlet(path, expected):
    from pyopsctl.session.daemon import _friendly_from_path
    assert _friendly_from_path(path) == expected


@pytest.mark.parametrize("path", [
    "",
    None,
    "/some/regular/file.py",
    "/data/jobs/foo/.debug/notsrc/foo/task/scr.py",
    "/.debug/src/foo/wrong_kind/scr.py",  # only task/library accepted
])
def test_friendly_from_path_returns_none_for_unrecognised(path):
    from pyopsctl.session.daemon import _friendly_from_path
    assert _friendly_from_path(path) is None


# ----------------------------------------------------------------------------
# _explode_repr_dict — locals/globals parser
# ----------------------------------------------------------------------------


def test_explode_repr_dict_empty():
    from pyopsctl.session.daemon import _explode_repr_dict
    assert _explode_repr_dict("{}") == []


def test_explode_repr_dict_simple_scalars():
    from pyopsctl.session.daemon import _explode_repr_dict
    out = _explode_repr_dict("{'a': 1, 'b': 'hello', 'c': True}")
    by_name = {e["name"]: e["value"] for e in out}
    assert by_name == {"a": "1", "b": "'hello'", "c": "True"}


def test_explode_repr_dict_nested_structures_kept_together():
    """Brackets and commas inside values must not split the pair."""
    from pyopsctl.session.daemon import _explode_repr_dict
    out = _explode_repr_dict("{'xs': [1, 2, 3], 'd': {'k': 'v, w'}}")
    by_name = {e["name"]: e["value"] for e in out}
    assert by_name["xs"] == "[1, 2, 3]"
    assert by_name["d"] == "{'k': 'v, w'}"


def test_explode_repr_dict_quoted_string_with_colon_inside():
    from pyopsctl.session.daemon import _explode_repr_dict
    out = _explode_repr_dict("{'k': 'a:b:c'}")
    assert out == [{"name": "k", "value": "'a:b:c'", "type": ""}]


def test_explode_repr_dict_non_literal_value_repr():
    """Pdb's `repr(locals())` returns `<module 'os' from '...'>` for
    non-literals — we keep it verbatim as the value string."""
    from pyopsctl.session.daemon import _explode_repr_dict
    out = _explode_repr_dict("{'os': <module 'os' from '/lib/os.py'>}")
    assert out == [{
        "name": "os",
        "value": "<module 'os' from '/lib/os.py'>",
        "type": "",
    }]


def test_explode_repr_dict_garbage_returns_unparseable_marker():
    from pyopsctl.session.daemon import _explode_repr_dict
    out = _explode_repr_dict("not a dict at all")
    assert out == [{"name": "(unparseable)", "value": "not a dict at all",
                    "type": ""}]


# ----------------------------------------------------------------------------
# _frame_payload
# ----------------------------------------------------------------------------


def test_frame_payload_includes_friendly_when_path_is_under_debug_src():
    from pyopsctl.pdb.client import StopInfo
    from pyopsctl.session.daemon import _frame_payload

    info = StopInfo(
        path="/data/jobs/foo/JOB/.debug/src/foo/task/scr.py",
        line=3,
        func="my_func()",
        arrow_line="counter += 1",
    )
    out = _frame_payload(info)
    assert out["line"] == 3
    assert out["name"] == "my_func()"
    assert out["arrow_line"] == "counter += 1"
    assert out["source"]["set"] == "foo"
    assert out["source"]["name"] == "scr"
    assert out["source"]["path"] == info.path
    assert out["source"]["is_shim"] is False


def test_frame_payload_unknown_path_keeps_path_but_no_friendly():
    from pyopsctl.pdb.client import StopInfo
    from pyopsctl.session.daemon import _frame_payload

    info = StopInfo(path="/random/file.py", line=1, func="<module>")
    out = _frame_payload(info)
    assert out["source"]["path"] == "/random/file.py"
    assert out["source"]["set"] is None
    assert out["source"]["name"] is None


# ----------------------------------------------------------------------------
# Stub-driven Daemon: a fake PdbClient lets us drive the op dispatch
# table without spinning up a real TCP listener.
# ----------------------------------------------------------------------------


class StubPdb:
    """Drop-in for PdbClient. Each call to `command()` consumes the next
    scripted response in `responses` and records the issued command."""

    def __init__(self, responses, initial_banner=""):
        self.initial_banner = initial_banner
        self._responses = list(responses)
        self.sent: list[str] = []
        self.closed = False

    def command(self, line, *, timeout=None):
        self.sent.append(line)
        if not self._responses:
            raise AssertionError(
                f"StubPdb out of responses when handling {line!r}; "
                f"already sent: {self.sent}"
            )
        return self._responses.pop(0)

    def close(self):
        self.closed = True


class StubHttp:
    """HTTP fake used by _fetch_source_lines."""

    def __init__(self, code_by_friendly=None):
        self._code = code_by_friendly or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((path, params))
        friendly = f"{params.get('set', '')}::{params.get('name', '')}"
        if friendly in self._code:
            return {"code": self._code[friendly]}
        raise KeyError(friendly)


def _make_daemon(initial_banner="", pdb_responses=(), http_code=None):
    from pyopsctl.session import paths
    from pyopsctl.session.daemon import Daemon

    sp = paths.SessionPaths(job_id="JOB123", root="/nonexistent")
    pdb = StubPdb(list(pdb_responses), initial_banner=initial_banner)
    http = StubHttp(http_code or {})
    d = Daemon(sp, meta={"job_id": "JOB123"}, pdb=pdb, http=http)
    return d, pdb, http


# ----------------------------------------------------------------------------
# _absorb_response + _op_state
# ----------------------------------------------------------------------------


def test_initial_banner_is_absorbed_as_first_stop():
    banner = (
        "> /data/jobs/foo/JOB/.debug/src/foo/task/scr.py(5)<module>()\n"
        "-> print('hello')\n"
    )
    d, _pdb, _http = _make_daemon(initial_banner=banner)
    assert d._last_stop is not None
    assert d._last_stop["state"] == "stopped"
    assert d._last_stop["frame"]["line"] == 5
    assert d._last_stop["frame"]["source"]["set"] == "foo"


def test_op_state_returns_running_when_no_stop_yet():
    from pyopsctl.session.daemon import _op_state
    d, _pdb, _http = _make_daemon(initial_banner="(no cursor in banner)")
    # The banner without a `>` cursor leaves _last_stop["frame"]=None.
    result = _op_state(d, {})
    assert result["state"] in ("running", "stopped")
    # Either way, there's no frame.
    assert result.get("frame") is None


# ----------------------------------------------------------------------------
# _op_evaluate
# ----------------------------------------------------------------------------


def test_op_evaluate_passes_through_pdb_repr():
    from pyopsctl.session.daemon import _op_evaluate
    banner = "> /a/b.py(1)<module>()\n-> x\n"
    d, pdb, _ = _make_daemon(initial_banner=banner, pdb_responses=["42\n"])
    result = _op_evaluate(d, {"expression": "6*7"})
    assert pdb.sent == ["p 6*7"]
    assert result["expression"] == "6*7"
    assert result["value"] == "42"


def test_op_evaluate_requires_expression():
    from pyopsctl.session.daemon import _op_evaluate
    from pyopsctl.pdb.client import PdbError
    d, _, _ = _make_daemon()
    with pytest.raises(PdbError):
        _op_evaluate(d, {"expression": ""})


# ----------------------------------------------------------------------------
# _op_break_set
# ----------------------------------------------------------------------------


def test_op_break_set_sends_b_command_with_workspace_resolved_path():
    """`b <workspace>/.debug/src/<set>/task/<name>.py:<line>` — workspace
    comes from pdb via `p __import__('os').getcwd()` (cached after
    first call). Task is tried first; the fixture's pdb confirms the
    breakpoint id so library isn't attempted."""
    from pyopsctl.session.daemon import _op_break_set
    d, pdb, _ = _make_daemon(
        initial_banner="(Pdb) ",
        pdb_responses=[
            "'/jobs/foo/JOB'\n",  # cwd lookup
            "Breakpoint 1 at /jobs/foo/JOB/.debug/src/foo/task/scr.py:3\n",
        ],
    )
    result = _op_break_set(d, {
        "friendly": "foo::scr", "line": 3, "condition": None,
    })
    assert pdb.sent == [
        "p __import__('os').getcwd()",
        "b /jobs/foo/JOB/.debug/src/foo/task/scr.py:3",
    ]
    assert result["id"] == 1
    assert result["friendly"] == "foo::scr"
    assert result["line"] == 3
    assert result["verified"] is True
    assert result["path"] == "/jobs/foo/JOB/.debug/src/foo/task/scr.py"


def test_op_break_set_falls_back_to_library_when_task_refused():
    """If pdb refuses the task variant (file not present in linecache),
    the daemon retries with the library layout."""
    from pyopsctl.session.daemon import _op_break_set
    d, pdb, _ = _make_daemon(
        initial_banner="(Pdb) ",
        pdb_responses=[
            "'/jobs/foo/JOB'\n",  # cwd lookup
            "*** End of file\n",   # task variant refused (raises PdbError, caught)
            "Breakpoint 1 at /jobs/foo/JOB/.debug/src/foo/library/util.py:7\n",
        ],
    )
    result = _op_break_set(d, {
        "friendly": "foo::util", "line": 7, "condition": None,
    })
    assert pdb.sent == [
        "p __import__('os').getcwd()",
        "b /jobs/foo/JOB/.debug/src/foo/task/util.py:7",
        "b /jobs/foo/JOB/.debug/src/foo/library/util.py:7",
    ]
    assert result["verified"] is True
    assert "library" in result["path"]


def test_op_break_set_appends_condition_when_given():
    from pyopsctl.session.daemon import _op_break_set
    d, pdb, _ = _make_daemon(
        initial_banner="(Pdb) ",
        pdb_responses=[
            "'/jobs/foo/JOB'\n",
            "Breakpoint 2 at /jobs/foo/JOB/.debug/src/foo/task/scr.py:3\n",
        ],
    )
    _op_break_set(d, {
        "friendly": "foo::scr", "line": 3, "condition": "x > 0",
    })
    assert pdb.sent == [
        "p __import__('os').getcwd()",
        "b /jobs/foo/JOB/.debug/src/foo/task/scr.py:3, x > 0",
    ]


def test_op_break_set_invalid_args():
    from pyopsctl.session.daemon import _op_break_set
    from pyopsctl.pdb.client import PdbError
    d, _, _ = _make_daemon()
    with pytest.raises(PdbError):
        _op_break_set(d, {"friendly": "", "line": 5})
    with pytest.raises(PdbError):
        _op_break_set(d, {"friendly": "foo::scr", "line": 0})


def test_op_break_set_raises_when_both_layouts_refused():
    """If pdb refuses the task AND library variants, surface a clear
    error rather than letting an `id=None` shape leak through."""
    from pyopsctl.session.daemon import _op_break_set
    from pyopsctl.pdb.client import PdbError
    d, pdb, _ = _make_daemon(
        initial_banner="(Pdb) ",
        pdb_responses=[
            "'/jobs/foo/JOB'\n",
            "*** End of file\n",  # task: refused
            "*** End of file\n",  # library: refused
        ],
    )
    with pytest.raises(PdbError) as exc:
        _op_break_set(d, {
            "friendly": "foo::nonexistent", "line": 1, "condition": None,
        })
    assert "task and library" in str(exc.value).lower() or "End of file" in str(exc.value)


# ----------------------------------------------------------------------------
# _op_break_remove
# ----------------------------------------------------------------------------


def test_op_break_remove_single_sends_cl_with_id():
    from pyopsctl.session.daemon import _op_break_remove
    d, pdb, _ = _make_daemon(pdb_responses=["Deleted breakpoint 3\n"])
    result = _op_break_remove(d, {"id": "3"})
    assert pdb.sent == ["cl 3"]
    assert result["removed"] == ["3"]


def test_op_break_remove_all_handles_confirmation_prompt():
    from pyopsctl.session.daemon import _op_break_remove
    # Pdb's `cl` with no args responds with the confirmation question,
    # then expects `yes` / `no`; on yes it deletes and returns.
    d, pdb, _ = _make_daemon(pdb_responses=[
        "Clear all breaks?",
        "Deleted breakpoint 1\nDeleted breakpoint 2\n",
    ])
    result = _op_break_remove(d, {"all": True})
    assert pdb.sent == ["cl", "yes"]
    assert result["removed"] == "all"


def test_op_break_remove_requires_id_or_all():
    from pyopsctl.session.daemon import _op_break_remove
    from pyopsctl.pdb.client import PdbError
    d, _, _ = _make_daemon()
    with pytest.raises(PdbError):
        _op_break_remove(d, {})


# ----------------------------------------------------------------------------
# _step_then_snapshot via _op_continue
# ----------------------------------------------------------------------------


def test_op_continue_sends_c_and_captures_new_stop():
    from pyopsctl.session.daemon import _op_continue
    initial = (
        "> /jobs/foo/JOB/.debug/src/foo/task/scr.py(1)<module>()\n"
        "-> pass\n"
    )
    # After `c`, pdb hits the breakpoint and prints the new location.
    # _stop_snapshot then issues `p locals()`, `p globals()`, `w`.
    continue_output = (
        "> /jobs/foo/JOB/.debug/src/foo/task/scr.py(3)<module>()\n"
        "-> counter = counter + 1\n"
    )
    d, pdb, _http = _make_daemon(initial_banner=initial, pdb_responses=[
        continue_output,
        "{'counter': 1}\n",
        "{'__name__': '__main__'}\n",
        # `w` (stack) — single frame at the new cursor
        "> /jobs/foo/JOB/.debug/src/foo/task/scr.py(3)<module>()\n"
        "-> counter = counter + 1\n",
    ])
    result = _op_continue(d, {})
    assert pdb.sent[0] == "c"
    assert result["state"] == "stopped"
    assert result["frame"]["line"] == 3
    # locals were captured from `p locals()`
    assert any(v["name"] == "counter" and v["value"] == "1"
               for v in result["locals"])


def test_op_continue_detects_program_termination():
    from pyopsctl.session.daemon import _op_continue
    initial = "> /a/b.py(1)<module>()\n-> pass\n"
    d, pdb, _ = _make_daemon(initial_banner=initial, pdb_responses=[
        "The program finished and will be restarted\n"
    ])
    result = _op_continue(d, {})
    assert pdb.sent == ["c"]
    assert result["state"] == "terminated"


# ----------------------------------------------------------------------------
# _op_source
# ----------------------------------------------------------------------------


def test_op_source_returns_lines_from_http_inventory():
    from pyopsctl.session.daemon import _op_source
    d, _pdb, http = _make_daemon(
        http_code={"foo::scr": "line 1\nline 2\nline 3\n"},
    )
    result = _op_source(d, {"friendly": "foo::scr"})
    assert result["set"] == "foo"
    assert result["name"] == "scr"
    assert [l["text"] for l in result["lines"]] == ["line 1", "line 2", "line 3"]
    assert http.calls == [("/scriptlet/inventory/code",
                           {"set": "foo", "name": "scr"})]


def test_op_source_window_via_lines_param():
    from pyopsctl.session.daemon import _op_source
    d, _pdb, _ = _make_daemon(
        http_code={"foo::scr": "\n".join(f"line {i}" for i in range(1, 11))},
    )
    result = _op_source(d, {"friendly": "foo::scr",
                            "start_line": 3, "end_line": 5})
    nums = [l["n"] for l in result["lines"]]
    assert nums == [3, 4, 5]


def test_op_source_raises_when_inventory_returns_nothing():
    from pyopsctl.session.daemon import _op_source
    from pyopsctl.pdb.client import PdbError
    d, _pdb, _ = _make_daemon(http_code={})  # KeyError on lookup
    with pytest.raises(PdbError):
        _op_source(d, {"friendly": "foo::scr"})


# ----------------------------------------------------------------------------
# _op_break_list
# ----------------------------------------------------------------------------


def test_op_break_list_parses_table_and_resolves_friendly():
    from pyopsctl.session.daemon import _op_break_list
    table = (
        "Num Type         Disp Enb   Where\n"
        "1   breakpoint   keep yes   "
        "at /jobs/foo/JOB/.debug/src/foo/task/scr.py:3\n"
        "2   breakpoint   keep yes   "
        "at /jobs/foo/JOB/.debug/src/foo/library/util.py:7\n"
        "        stop only if x > 0\n"
    )
    d, _pdb, _ = _make_daemon(pdb_responses=[table])
    result = _op_break_list(d, {})
    bps = result["breakpoints"]
    assert len(bps) == 2
    assert bps[0]["friendly"] == "foo::scr"
    assert bps[0]["line"] == 3
    assert bps[0]["condition"] == ""
    assert bps[1]["friendly"] == "foo::util"
    assert bps[1]["condition"] == "x > 0"
