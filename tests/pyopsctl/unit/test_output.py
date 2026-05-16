"""Output formatting in human / --json / --quiet modes, plus
exit-code-routed error emission to stderr."""
import json
from types import SimpleNamespace

import pytest


def _args(json_mode=False, quiet=False):
    return SimpleNamespace(json_mode=json_mode, quiet=quiet)


def test_human_mode_prints_human_string(capsys):
    from pyopsctl.output import emit
    emit(_args(), {"x": 1}, human="hello")
    assert capsys.readouterr().out == "hello\n"


def test_json_mode_passes_server_payload_verbatim(capsys):
    from pyopsctl.output import emit
    payload = [{"id": "a"}, {"id": "b"}]
    emit(_args(json_mode=True), payload, id_field="id", human="ignored")
    line = capsys.readouterr().out.strip()
    assert json.loads(line) == payload


def test_quiet_mode_prints_ids_one_per_line_for_list(capsys):
    from pyopsctl.output import emit
    payload = [{"id": "a", "name": "x"}, {"id": "b", "name": "y"}, {"id": "c", "name": "z"}]
    emit(_args(quiet=True), payload, id_field="id")
    assert capsys.readouterr().out == "a\nb\nc\n"


def test_quiet_mode_prints_single_id_for_dict(capsys):
    from pyopsctl.output import emit
    emit(_args(quiet=True), {"id": "abc123"}, id_field="id")
    assert capsys.readouterr().out == "abc123\n"


def test_quiet_falls_through_when_no_id_field():
    """Without id_field, --quiet has nothing to print and emit returns
    silently — caller's contract is to only set id_field when there's
    something pipe-friendly to emit."""
    from pyopsctl.output import emit
    # No exception; just nothing printed.
    emit(_args(quiet=True), {"x": 1}, human="ignored")


def test_emit_error_writes_to_stderr_in_human_mode(capsys):
    from pyopsctl.output import emit_error
    emit_error(_args(), "boom", status=404)
    out, err = capsys.readouterr()
    assert out == ""
    assert "boom" in err and "404" in err


def test_emit_error_writes_structured_json_to_stderr_in_json_mode(capsys):
    from pyopsctl.output import emit_error
    emit_error(_args(json_mode=True), "boom", status=503)
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err.strip()) == {"error": "boom", "status": 503}


def test_emit_composite_wraps_in_data_and_meta_in_json_mode(capsys):
    from pyopsctl.output import emit_composite
    emit_composite(_args(json_mode=True), {"port": 45678, "job_id": "j1"},
                   meta={"server": "https://x", "profile": "aiops"},
                   human="ignored")
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["data"] == {"port": 45678, "job_id": "j1"}
    assert payload["meta"]["server"] == "https://x"


def test_format_table_aligns_columns():
    from pyopsctl.output import format_table
    out = format_table(["id", "state"], [["a", "ok"], ["bb", "fail"]])
    lines = out.split("\n")
    # 3 lines: header, separator, two body rows = 4 total
    assert len(lines) == 4
    # Columns aligned to widest cell
    assert lines[0].startswith("id ")
    assert "state" in lines[0]
