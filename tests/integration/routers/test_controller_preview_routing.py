"""Regression tests for `/controller/{set}/{name}/{rest:path}` (controller
proxy) preview-mode routing.

The controller code can carry `debug=True` to flip the proxy into
"preview" mode: the routing should try a `debug` job (debugger-attached,
e.g. started from VSIX) first, then fall back to a `draft` job (unstaged
code running standalone, e.g. started from the Manager). `debug=False`
(or unset) routes to the committed `service` as before.

The bug this guards against: previously the proxy only looked at
`kind="debug"` when `debug=True`, so an operator who stopped the
committed service and started the same scriptlet as a draft saw the
proxy return 503 instead of binding to the draft.
"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.web import app


@pytest.fixture
def client():
    return TestClient(app)


def _ctrl_code(debug: bool, set_name: str = "myset", service: str = "mysvc") -> str:
    return (
        f"set = '{set_name}'\n"
        f"service = '{service}'\n"
        f"name = 'X'\n"
        f"debug = {debug}\n"
        "head = 'h'\nmenu = 'm'\nbody = 'b'\n"
    )


def _stub_proxy(client, mock_git_ops, mock_db, controller_debug: bool, find_results: dict):
    """Drive a request through the proxy. `find_results` maps `kind` →
    list-of-job-ids that `runner.find` should return for that kind. Any
    kind not in the dict returns `[]`. Returns the FastAPI response and a
    list of `runner.find` calls in order."""
    code = _ctrl_code(controller_debug)

    def fake_find(owner, set_, name_, kind, states):
        return list(find_results.get(kind, []))

    with patch("app.web.get_controller_code", return_value=code), \
         patch("app.lib.runner.find", side_effect=fake_find) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_svc:
        mock_svc.return_value.request.return_value = (200, {"ok": True})
        r = client.get("/controller/myset/anycontroller/ping", headers={"X-User": "alice"})
        return r, mock_find.call_args_list


def _find_kinds_used(call_list):
    """Return the `kind` positional arg from each runner.find call."""
    return [c.args[3] for c in call_list]


class TestPreviewRouting:
    def test_production_routes_to_service_kind(self, client, mock_git_ops, mock_db):
        """Controller without debug=True: proxy looks up the committed
        service. Owner filter is `None` (any user can reach the production
        service)."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=False,
                                find_results={"service": ["jobA"]})
        assert r.status_code == 200
        assert _find_kinds_used(calls) == ["service"]
        assert calls[0].args[0] is None  # owner filter

    def test_debug_prefers_debug_kind_over_draft(self, client, mock_git_ops, mock_db):
        """debug=True controller, BOTH a debug and a draft job running:
        debug wins (debugger-attached preview gets priority)."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=True,
                                find_results={"debug": ["jobDbg"], "draft": ["jobDft"]})
        assert r.status_code == 200
        kinds = _find_kinds_used(calls)
        # First call must be debug; if it returned a hit the proxy stops there
        assert kinds[0] == "debug"
        assert "draft" not in kinds

    def test_debug_falls_back_to_draft_kind(self, client, mock_git_ops, mock_db):
        """debug=True controller, only a draft running (no debug job):
        the proxy queries debug first, gets nothing, falls back to draft."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=True,
                                find_results={"draft": ["jobDft"]})
        assert r.status_code == 200
        kinds = _find_kinds_used(calls)
        assert kinds == ["debug", "draft"]

    def test_debug_503_when_neither_debug_nor_draft_running(self, client, mock_git_ops, mock_db):
        """debug=True controller, nothing running: proxy queries both
        debug and draft, both empty, returns 503 with a `preview` label
        in the error message so operators see at a glance which mode was
        being attempted."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=True,
                                find_results={})
        assert r.status_code == 503
        body = r.json()
        assert "error" in body
        assert "preview" in body["error"].lower()
        assert _find_kinds_used(calls) == ["debug", "draft"]

    def test_debug_preview_scoped_to_requesting_user(self, client, mock_git_ops, mock_db):
        """Preview routing is per-user: each proxy lookup passes the
        X-User header through as the owner filter, so one operator's
        draft does not bleed into another operator's preview session."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=True,
                                find_results={"draft": ["jobDft"]})
        assert r.status_code == 200
        for c in calls:
            assert c.args[0] == "alice"  # owner = X-User header

    def test_production_lookup_does_not_filter_by_owner(self, client, mock_git_ops, mock_db):
        """Production-mode service lookup passes `None` for owner so any
        user can reach a service started by anyone — services are shared."""
        r, calls = _stub_proxy(client, mock_git_ops, mock_db,
                                controller_debug=False,
                                find_results={"service": ["jobA"]})
        assert r.status_code == 200
        assert calls[0].args[0] is None
