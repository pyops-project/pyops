"""Vertical state-dump renderer for debug commands."""


def test_separator_is_60_hyphens():
    from pyopsctl import debug_format
    assert debug_format.SEPARATOR == "-" * 60


def test_render_variables_with_values():
    from pyopsctl.debug_format import render_variables
    out = render_variables("locals", [
        {"name": "x", "type": "int", "value": "1"},
        {"name": "s", "type": "str", "value": "'hi'"},
    ])
    assert out.splitlines()[0] == "locals:"
    assert "- x: 1  # int" in out
    assert "- s: 'hi'  # str" in out


def test_render_variables_truncates_long_value():
    from pyopsctl.debug_format import render_variables
    long_value = "a" * 200
    out = render_variables("locals", [{"name": "big", "value": long_value}])
    line = next(l for l in out.splitlines() if l.startswith("- big"))
    assert len(line) <= 130  # `- big: ` + 120-ish + ellipsis
    assert line.endswith("…")


def test_render_variables_empty():
    from pyopsctl.debug_format import render_variables
    assert render_variables("globals", []).splitlines() == [
        "globals:", "- (empty)"
    ]


def test_render_code_marks_current_with_arrow():
    from pyopsctl.debug_format import render_code, ARROW
    code = {
        "set": "test", "name": "scr", "current_line": 6,
        "start_line": 5, "end_line": 7,
        "lines": [
            {"n": 5, "text": "def f():"},
            {"n": 6, "text": "    x = 1", "current": True},
            {"n": 7, "text": "    return x"},
        ],
    }
    out = render_code(code)
    lines = out.splitlines()
    assert lines[0] == "code (test::scr):"
    # exactly one line starts with the arrow prefix
    arrow_lines = [l for l in lines if l.lstrip().startswith(ARROW)]
    assert len(arrow_lines) == 1
    assert "x = 1" in arrow_lines[0]


def test_render_code_right_aligns_line_numbers():
    from pyopsctl.debug_format import render_code
    code = {
        "set": "test", "name": "scr", "current_line": 99,
        "start_line": 8, "end_line": 100,
        "lines": [{"n": i, "text": ""} for i in range(8, 101)],
    }
    out = render_code(code)
    # The widest line number is "100" (3 chars); all numbers right-aligned to 3.
    payload = out.splitlines()[1:]  # skip header
    for line, n in zip(payload, range(8, 101)):
        # Format: "   N  text" or " → N  text". The 3-char number sits
        # immediately before the two-space `  ` separator.
        assert f"  {str(n).rjust(3)}  " in line


def test_render_callstack_runner_shim_label():
    from pyopsctl.debug_format import render_callstack
    out = render_callstack([
        {"name": "my_func", "line": 6, "source": {"set": "test", "name": "scr"}},
        {"name": "<module>", "line": 2, "source": {"is_shim": True, "path": "/srv/runner.py"}},
    ])
    lines = out.splitlines()
    assert lines[0] == "callstack:"
    assert "test::scr:6 in my_func" in lines[1]
    assert "<runner shim>:2 in <module>" in lines[2]


def test_render_state_dump_has_four_sections_separated_by_60_hyphens():
    from pyopsctl.debug_format import render_state_dump, SEPARATOR
    snapshot = {
        "state": "stopped", "reason": "step",
        "globals": [{"name": "G", "value": "1"}],
        "locals": [{"name": "x", "value": "1"}],
        "code": {
            "set": "t", "name": "s", "current_line": 1,
            "start_line": 1, "end_line": 1,
            "lines": [{"n": 1, "text": "pass", "current": True}],
        },
        "stack": [{"name": "<module>", "line": 1,
                   "source": {"set": "t", "name": "s"}}],
    }
    out = render_state_dump(snapshot)
    parts = out.split(f"\n{SEPARATOR}\n")
    assert len(parts) == 4
    assert parts[0].startswith("globals:")
    assert parts[1].startswith("locals:")
    assert parts[2].startswith("code (t::s):")
    assert parts[3].startswith("callstack:")


def test_render_state_dump_terminated_one_line():
    from pyopsctl.debug_format import render_state_dump
    assert render_state_dump({"state": "terminated"}) == "terminated"


def test_render_evaluate_one_liner():
    from pyopsctl.debug_format import render_evaluate
    out = render_evaluate({"expression": "x + 1", "value": "2", "type": "int"})
    assert out == "x + 1  =  2  # int"


def test_render_breakpoint_verified_vs_pending():
    from pyopsctl.debug_format import render_breakpoint
    verified = render_breakpoint({
        "id": "bp1", "verified": True, "friendly": "test::scr", "line": 5,
    })
    pending = render_breakpoint({
        "id": "bp2", "pending": True, "friendly": "test::lazy_lib", "line": 3,
    })
    assert "[verified]" in verified and "test::scr:5" in verified
    assert "[pending]" in pending and "test::lazy_lib:3" in pending


def test_render_breakpoint_with_condition():
    from pyopsctl.debug_format import render_breakpoint
    out = render_breakpoint({
        "id": "bp1", "verified": True, "friendly": "test::scr", "line": 5,
        "condition": "x > 0",
    })
    assert "if x > 0" in out


