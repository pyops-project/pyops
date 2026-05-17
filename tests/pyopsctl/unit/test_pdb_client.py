"""Unit coverage for ctl/pyopsctl/pdb/client.py.

Drives a `PdbClient` against a stub TCP server that scripts the
responses pdb would write. Verifies:
  - the prompt reader stops at exactly `(Pdb) ` and keeps the buffer
    around for the next command
  - command() returns the text emitted between the previous prompt
    and the next one, with the prompts stripped
  - `*** <Err>: ...` lines raise PdbError but leave the connection
    usable for the next command (this is critical: pdb stays at a
    prompt even on evaluation errors)
  - parse_stop_info / parse_stack / parse_breakpoints / parse_list
    extract the expected shapes from realistic pdb output
"""
import socket
import threading

import pytest


def _serve_script(script: list[bytes]) -> tuple[int, threading.Thread]:
    """Spin up a one-shot TCP listener that, on accept, writes each
    item of `script` immediately and then waits for the client to send
    bytes (a pdb command + newline). After each client send, advance to
    the next script item. Returns (port, thread).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _run():
        conn, _ = srv.accept()
        try:
            for chunk in script:
                conn.sendall(chunk)
                # Drain one command line from the client between chunks.
                if chunk is not script[-1]:
                    buf = b""
                    while not buf.endswith(b"\n"):
                        bit = conn.recv(4096)
                        if not bit:
                            return
                        buf += bit
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            srv.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return port, t


def test_connect_consumes_initial_prompt_and_captures_banner():
    from pyopsctl.pdb.client import PdbClient

    port, _t = _serve_script([
        b"> /tmp/.debug/src/foo/task/scr.py(1)<module>()\n"
        b"-> print('hi')\n"
        b"(Pdb) ",
    ])
    c = PdbClient.connect("127.0.0.1", port, timeout=5.0)
    assert "scr.py(1)" in c.initial_banner
    assert "-> print('hi')" in c.initial_banner
    c.close()


def test_command_returns_response_between_prompts():
    from pyopsctl.pdb.client import PdbClient

    port, _t = _serve_script([
        b"(Pdb) ",  # initial prompt
        b"42\n(Pdb) ",  # response to `p 6*7`
    ])
    c = PdbClient.connect("127.0.0.1", port, timeout=5.0)
    out = c.command("p 6*7")
    assert out.strip() == "42"
    c.close()


def test_command_raises_on_pdb_error_but_keeps_connection_usable():
    from pyopsctl.pdb.client import PdbClient, PdbError

    port, _t = _serve_script([
        b"(Pdb) ",
        b"*** NameError: name 'undef' is not defined\n(Pdb) ",
        b"42\n(Pdb) ",
    ])
    c = PdbClient.connect("127.0.0.1", port, timeout=5.0)
    with pytest.raises(PdbError) as exc:
        c.command("p undef")
    assert "NameError" in str(exc.value)

    # Next command still works — pdb is at a prompt after errors.
    out = c.command("p 6*7")
    assert out.strip() == "42"
    c.close()


def test_parse_stop_info_extracts_path_line_func_and_arrow():
    from pyopsctl.pdb.client import PdbClient

    text = (
        "> /tmp/jobs/foo/JOBID/.debug/src/foo/task/scr.py(3)my_func()\n"
        "-> counter = counter + 1\n"
    )
    info = PdbClient.parse_stop_info(text)
    assert info is not None
    assert info.path == "/tmp/jobs/foo/JOBID/.debug/src/foo/task/scr.py"
    assert info.line == 3
    assert info.func == "my_func()"
    assert info.arrow_line == "counter = counter + 1"


def test_parse_stop_info_picks_last_when_multiple():
    """`n` (next) inside a generator can emit several `>` lines; the
    final one is the cursor we care about."""
    from pyopsctl.pdb.client import PdbClient

    text = (
        "> /a/b.py(10)<module>()\n"
        "-> x = 1\n"
        "> /a/b.py(11)<module>()\n"
        "-> x = 2\n"
    )
    info = PdbClient.parse_stop_info(text)
    assert info.line == 11
    assert info.arrow_line == "x = 2"


def test_parse_stack_returns_frames_outermost_first():
    from pyopsctl.pdb.client import PdbClient

    text = (
        "  /a/main.py(5)<module>()\n"
        "-> main()\n"
        "  /a/main.py(2)main()\n"
        "-> helper()\n"
        "> /a/main.py(8)helper()\n"
        "-> raise RuntimeError\n"
    )
    stack = PdbClient.parse_stack(text)
    assert [f.line for f in stack] == [5, 2, 8]
    assert stack[-1].func == "helper()"


def test_parse_breakpoints_handles_unconditional_and_conditional():
    from pyopsctl.pdb.client import PdbClient

    text = (
        "Num Type         Disp Enb   Where\n"
        "1   breakpoint   keep yes   at /a/b.py:42\n"
        "2   breakpoint   keep yes   at /a/b.py:50\n"
        "        stop only if x > 0\n"
    )
    bps = PdbClient.parse_breakpoints(text)
    assert len(bps) == 2
    assert bps[0].line == 42 and bps[0].condition == ""
    assert bps[1].line == 50 and bps[1].condition == "x > 0"


def test_parse_list_marks_current_line_with_arrow():
    from pyopsctl.pdb.client import PdbClient

    text = (
        "  1   counter = 0\n"
        "  2   while True:\n"
        "  3 ->     counter = counter + 1\n"
        "  4       if counter > 10**9:\n"
    )
    lines = PdbClient.parse_list(text)
    assert lines == [
        (1, "counter = 0", False),
        (2, "while True:", False),
        (3, "counter = counter + 1", True),
        (4, "if counter > 10**9:", False),
    ]
