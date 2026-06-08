"""Integration tests for the pyops-managed set bundle.

Verifies that app/pyops/ is reachable through the existing pyops
mechanisms (scriptlet enumeration, controller, jslib endpoint) without
relying on a running runner / data service. The service-driven sub-task
flow is covered by E2E in tests/e2e/.
"""
import os
import py_compile

import pytest
from fastapi.testclient import TestClient
from app.web import app
from app.lib import files


PYOPS_SET_DIR = files.bundled_set_path
client = TestClient(app)


def _path(name):
    return os.path.join(PYOPS_SET_DIR, name)


class TestPyopsSetBundlePresent:
    """Sanity: the files we ship under app/pyops/ exist and parse."""

    @pytest.mark.parametrize("filename", [
        "sets.controller.py",
        "data.task.py",
        "set.library.py",
        "set_clone.task.py",
        "set_create.task.py",
        "set_pull.task.py",
        "set_switch.task.py",
        "set_remove.task.py",
        "set_update_deps.task.py",
        "set_commit_drafts.task.py",
    ])
    def test_python_scriptlet_parses(self, filename, tmp_path):
        path = _path(filename)
        assert os.path.exists(path), f"missing pyops-set scriptlet: {path}"
        # Compile to a tmp file so pyops's source tree stays clean
        # (avoids littering app/pyops/ with .pyc artefacts that
        # later test runs would mis-parse).
        py_compile.compile(path, cfile=str(tmp_path / "out.pyc"), doraise=True)

    @pytest.mark.parametrize("filename", [
        "sets_head.html",
        "sets_menu.html",
        "sets_body.html",
    ])
    def test_view_present(self, filename):
        assert os.path.exists(_path(filename))

    def test_jslibrary_present(self):
        assert os.path.exists(_path("sets_job.js"))


class TestPyopsControllerRoute:
    """GET /controller/pyops/sets executes the controller and stitches
    head/menu/body view content into the wrapper HTML."""

    def test_controller_renders_sets_page(self, mock_conf):
        r = client.get("/controller/pyops/sets")
        assert r.status_code == 200
        html = r.text
        # Body content carried over from the body view
        assert 'id="sets"' in html
        assert "Clone Set" in html
        assert "Create Set" in html
        assert "Switch Branch" in html
        # Menu buttons
        assert 'data-testid="sets-clone"' in html
        assert 'data-testid="sets-create"' in html
        # Head view loads the jslib via the standard pyops endpoint
        assert "/scriptlet/jslib/pyops/sets_job" in html
        # {{SET}} / {{NAME}} substitution applied in apiCall + button handlers
        assert "/controller/pyops/sets/" in html
        assert "{{SET}}" not in html
        assert "{{NAME}}" not in html


class TestPyopsJslibRoute:
    """The sets_job.js library is served via the same jslib endpoint
    user-set JS libraries use — set='pyops', name='sets_job'."""

    def test_jslibrary_served(self):
        r = client.get(
            "/scriptlet/jslib/pyops/sets_job",
            params={"committed": False},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/javascript")
        # Exposes the runSetsJob global
        assert "runSetsJob" in r.text


class TestPyopsControllerProxyNeedsRunningService:
    """The controller-proxy path /controller/pyops/sets/<rest> requires
    a running `pyops::data` service. Without it, the proxy returns an
    error payload — exercising the routing wiring without booting a real
    runner."""

    def test_proxy_returns_503_when_service_not_running(self, mock_db):
        r = client.get("/controller/pyops/sets/list")
        # controller_proxy returns 503 with a structured {"error": "..."}
        # payload when the bound service isn't running yet (avoids a
        # traceback in the server log for every warmup poll).
        assert r.status_code == 503
        body = r.json()
        assert "error" in body
        assert "service" in body["error"].lower()
