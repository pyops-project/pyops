"""
Code-completion endpoint E2E tests.

POST /scriptlet/completion wraps jedi.Script.complete() and returns
{name, value, meta, score} entries for the editor's autocomplete popup.
"""

import pytest


class TestCompletion:
    """POST /scriptlet/completion returns jedi suggestions for the given position."""

    def test_completion_returns_builtins(self, api, pyops_server):
        """Completing 'pri' at the cursor yields 'print' among the suggestions."""
        r = api.post(
            "/scriptlet/completion",
            json={"code": "pri", "line": 1, "column": 3},
        )
        assert r.status_code == 200
        items = r.json()
        names = {item["name"] for item in items}
        assert "print" in names, f"no 'print' in {names}"
        # Score is present and an int.
        for it in items[:5]:
            assert isinstance(it["score"], int)
            assert "value" in it
        pyops_server.assert_no_unexpected_errors()

    def test_completion_on_module_attributes(self, api, pyops_server):
        """After 'import os; os.' the endpoint returns os attributes."""
        r = api.post(
            "/scriptlet/completion",
            json={"code": "import os\nos.", "line": 2, "column": 3},
        )
        assert r.status_code == 200
        names = {item["name"] for item in r.json()}
        # 'path' is a well-known attribute of the os module.
        assert "path" in names, f"no 'path' in {sorted(names)[:20]}"
        pyops_server.assert_no_unexpected_errors()

    def test_completion_on_empty_buffer_is_safe(self, api, pyops_server):
        """An empty buffer must not 500 — jedi returns a (possibly empty) list."""
        r = api.post(
            "/scriptlet/completion",
            json={"code": "", "line": 1, "column": 0},
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        pyops_server.assert_no_unexpected_errors()
