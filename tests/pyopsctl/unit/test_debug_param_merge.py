"""The merge of `--await` and `--param` is the load-bearing piece of
the debug surface — VSIX can't do this. Truth table tests below.

The key invariant: param is a multi-line Python script that may carry
other `#`-prefixed directives. With `--await` we prepend
`#>AWAIT_DEBUG\n`; the user's body stays verbatim on subsequent lines
so any embedded directives (`#!pip foo`, comments, blank lines) survive.
"""


def test_await_only_emits_bare_directive():
    from pyopsctl.commands.debug import _merge_debug_param

    assert _merge_debug_param(await_flag=True, user_param=None) == "#>AWAIT_DEBUG"
    assert _merge_debug_param(await_flag=True, user_param="") == "#>AWAIT_DEBUG"


def test_param_only_passes_verbatim():
    from pyopsctl.commands.debug import _merge_debug_param

    assert _merge_debug_param(await_flag=False, user_param="x = 1") == "x = 1"
    multi = "import foo\n\nx = 1\nprint(x)\n"
    assert _merge_debug_param(await_flag=False, user_param=multi) == multi


def test_neither_flag_empty():
    from pyopsctl.commands.debug import _merge_debug_param

    assert _merge_debug_param(await_flag=False, user_param=None) == ""
    assert _merge_debug_param(await_flag=False, user_param="") == ""


def test_await_plus_single_line_prepends_directive_with_newline():
    from pyopsctl.commands.debug import _merge_debug_param

    result = _merge_debug_param(await_flag=True, user_param="x = 1")
    assert result == "#>AWAIT_DEBUG\nx = 1"
    assert result.startswith("#>AWAIT_DEBUG\n")
    # Substring check matching pyops runner.py's `"#>AWAIT_DEBUG" in job.param`
    assert "#>AWAIT_DEBUG" in result


def test_await_plus_multiline_script_keeps_user_body_intact():
    from pyopsctl.commands.debug import _merge_debug_param

    user = "import foo\n\nx = 1\nprint(x)\n"
    result = _merge_debug_param(await_flag=True, user_param=user)
    assert result == "#>AWAIT_DEBUG\n" + user
    # The user's body lives on its own lines after the directive — no splicing.
    assert result.split("\n", 1)[1] == user


def test_await_plus_param_with_embedded_directive_preserves_it():
    """If the user param itself starts with a `#`-prefixed directive
    (`#!pip foo` for install hint, another `#>FLAG`), the merge must
    keep that directive on its own line, untouched. This is why the
    separator is `\\n` and the directive isn't spliced into the first
    line.
    """
    from pyopsctl.commands.debug import _merge_debug_param

    user = "#!pip somepkg\n# tracer comment\nx = 1\n"
    result = _merge_debug_param(await_flag=True, user_param=user)
    assert result == "#>AWAIT_DEBUG\n" + user

    lines = result.split("\n")
    assert lines[0] == "#>AWAIT_DEBUG"
    assert lines[1] == "#!pip somepkg"  # operator's embedded directive survives
    assert lines[2] == "# tracer comment"
    assert lines[3] == "x = 1"
