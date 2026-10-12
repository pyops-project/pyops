import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.web import app

@pytest.fixture
def client():
    return TestClient(app)

def test_controller_proxy_path_backward_compat(client, mock_git_ops, mock_db):
    # Test backward compatibility: /q calls send path="/q"
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"success": True})

        response = client.get("/controller/testset/testname/q?cmd=foo", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/q"
        assert params["type"] == "GET"
        assert params["cmd"] == "foo"

def test_controller_proxy_path_subpath(client, mock_git_ops, mock_db):
    # Test subpath forwarding
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"received": "path"})

        # POST with JSON body
        json_data = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        response = client.post("/controller/testset/testname/v1/chat/completions", json=json_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/v1/chat/completions"
        assert params["type"] == "POST"
        assert params["data"] == json_data
        assert params["data_type"] == "json"

def test_controller_proxy_path_query_params(client, mock_git_ops, mock_db):
    # Test query params with subpath
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"ok": True})

        response = client.get("/controller/testset/testname/api/models?limit=10&after=gpt", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/api/models"
        assert params["type"] == "GET"
        assert params["limit"] == "10"
        assert params["after"] == "gpt"

def test_controller_proxy_path_empty_rest(client, mock_git_ops, mock_db):
    # Test empty rest (trailing /)
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"path": "/"})

        response = client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/"
        assert params["type"] == "GET"

def test_controller_proxy_path_put_delete(client, mock_git_ops, mock_db):
    # Test PUT and DELETE with subpath
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"method": "PUT"})

        # PUT
        response = client.put("/controller/testset/testname/items/123", json={"name": "test"}, headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/items/123"
        assert params["type"] == "PUT"
        assert params["data"] == {"name": "test"}
        assert params["data_type"] == "json"

        # DELETE
        mock_instance.request.reset_mock()
        response = client.delete("/controller/testset/testname/items/123", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/items/123"
        assert params["type"] == "DELETE"

def test_controller_proxy_path_error_no_service(client, mock_git_ops, mock_db):
    """When no running service backs the controller, proxy returns 503
    (transient — client should retry while the service spins up) with a
    structured {"error": ...} payload. Previously this raised and surfaced
    as a 200 with an error dict; the 503 prevents warmup-poll traffic
    from dumping a traceback into the server log on every miss."""
    response = client.get("/controller/testset/testname/v1/models", headers={"X-User": "testuser"})
    assert response.status_code == 503
    data = response.json()
    assert "error" in data
    assert "no running" in data["error"].lower()

def test_controller_proxy_path_exec_fail(client, mock_git_ops, mock_db):
    # Test exec failure in controller code
    with patch("builtins.exec", side_effect=SyntaxError("controller syntax error")):
        response = client.get("/controller/testset/testname/v1/models", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "controller syntax error" in data["error"]

def test_controller_proxy_service_error_status(client, mock_git_ops, mock_db):
    # A non-200 status from the service is the response's status
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (404, "resource not found")

        response = client.get("/controller/testset/testname/missing", headers={"X-User": "testuser"})
        assert response.status_code == 404

def test_controller_proxy_service_error_status_422(client, mock_git_ops, mock_db):
    # Test that 422 from service is propagated
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (422, "validation failed")

        response = client.post("/controller/testset/testname/submit", json={}, headers={"X-User": "testuser"})
        assert response.status_code == 422

def test_controller_proxy_preserves_query_path(client, mock_git_ops, mock_db):
    # Test backward compatibility: preserves ?path= if present
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"path_preserved": True})

        response = client.get("/controller/testset/testname/?path=/custom&cmd=foo", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        params = mock_instance.request.call_args[0][0]
        assert params["path"] == "/custom"
        assert params["cmd"] == "foo"

@pytest.mark.parametrize("web_conf, recv_timeout_ms", [
    ({}, 300_000),
    ({"timeout": 0}, 300_000),
    ({"timeout": 7}, 7_000),
], ids=["timeout_unset", "timeout_0", "timeout_7"])
def test_controller_proxy_service_client_args(client, mock_git_ops, mock_db, web_conf, recv_timeout_ms):
    # Test that ServiceClient is called with correct arguments from controller globals
    import zmq
    with patch("app.lib.runner.find", return_value=["mock_job_id"]) as mock_find, \
         patch("app.lib.files.conf_web", return_value=web_conf), \
         patch("zmq.Context") as mock_zmq_context, \
         patch("msgpack.unpackb", return_value=[200, {"test": "response"}]) as mock_unpackb:

        mock_socket = MagicMock()
        mock_context = MagicMock()
        mock_context.socket.return_value = mock_socket
        mock_zmq_context.return_value = mock_context

        response = client.get("/controller/testset/testname/api/test", headers={"X-User": "testuser"})
        assert response.status_code == 200

        # Verify ServiceClient was instantiated with correct args: (user, glob["set"], glob["service"], debug)
        # The mock controller code sets: set="testset", service="testservice", debug=False (default)
        # For services (not debug), owner is None to allow access by all users
        mock_find.assert_called_once_with(None, "testset", "testservice", "service", ["running"])
        # The recv is bounded by the web worker's request timeout (web.timeout s, 300 s when unset or 0).
        mock_socket.setsockopt.assert_any_call(zmq.RCVTIMEO, recv_timeout_ms)

def test_controller_html_output(client, mock_git_ops, mock_db):
    # Test that controller endpoint returns HTML with sticky navbar
    with patch("builtins.exec") as mock_exec, \
         patch("app.web.get_view_content") as mock_get_view:

        # Mock controller globals
        mock_glob = {
            "set": "testset",
            "service": "testservice",
            "name": "Test Controller",
            "head": "head_view",
            "menu": "menu_view",
            "body": "body_view",
            "debug": False
        }
        def exec_side_effect(code, glob):
            glob.update(mock_glob)
        mock_exec.side_effect = exec_side_effect

        # Mock view content
        mock_get_view.side_effect = lambda set, name, debug=False: f"<div>{name} content</div>"

        response = client.get("/controller/testset/testname", headers={"X-User": "testuser"})
        assert response.status_code == 200
        html = response.text

        # Check HTML structure
        assert "<html>" in html
        assert "<head>" in html
        assert "<body class=\"bg-light\">" in html
        assert "sticky-top" in html  # The navbar should have sticky-top class
        assert "navbar navbar-expand-md navbar-dark bg-dark text-light pt-3 sticky-top" in html
        assert 'class="navbar-toggler"' in html
        assert 'id="controllerNav"' in html
        assert "testset::Test Controller" in html
        assert "<div>head_view content</div>" in html
        assert "<div>menu_view content</div>" in html
        assert "<div>body_view content</div>" in html


def test_controller_proxy_closes_service_client_on_success(client, mock_git_ops, mock_db):
    """Regression (ZMQ leak): the per-request ServiceClient must be closed so
    its context + ZMQbg thread pair is reclaimed, not leaked for the worker
    lifetime."""
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (200, {"ok": True})

        response = client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 200
        mock_instance.request.assert_called_once()
        mock_instance.close.assert_called_once()


def test_controller_proxy_closes_service_client_on_error_status(client, mock_git_ops, mock_db):
    """close() also runs when the service returns a non-200 — the client
    is not leaked on the error status."""
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.return_value = (500, "boom")

        response = client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 500
        mock_instance.close.assert_called_once()


def test_controller_proxy_closes_service_client_on_request_error(client, mock_git_ops, mock_db):
    """close() runs via finally when request() raises — a recv that timed out
    on a slow or dead service does not leak the client, and the proxy
    answers 504 Gateway Timeout."""
    from app.lib.errors import ServiceTimeout
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_instance = MagicMock()
        mock_service.return_value = mock_instance
        mock_instance.request.side_effect = ServiceTimeout("Service job mock_job_id did not answer within 1s")

        response = client.get("/controller/testset/testname/", headers={"X-User": "testuser"})
        assert response.status_code == 504
        assert response.json() == {"error": "Service job mock_job_id did not answer within 1s"}
        mock_instance.close.assert_called_once()


@pytest.mark.parametrize("status, body", [
    (201, {"id": 5}),
    (400, {"error": "Invalid set name 'a.b'"}),
    (404, {"error": "set 'x' not found"}),
    (409, {"error": "Set 's1' already exists."}),
    (503, {"error": "pyops shutdown in progress"}),
    (200, ["a", "b"]),
    (200, "plain text"),
])
def test_controller_proxy_passes_service_answer_through(client, mock_git_ops, mock_db, status, body):
    """The service's status and body are the response, unchanged — no
    {"detail": ...} wrapping of error bodies, no mangled 2xx bodies."""
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_service.return_value.request.return_value = (status, body)
        response = client.post("/controller/testset/testname/x", json={}, headers={"X-User": "testuser"})
    assert response.status_code == status
    assert response.json() == body


def test_controller_proxy_passes_bodyless_status_through(client, mock_git_ops, mock_db):
    with patch("app.lib.runner.find", return_value=["mock_job_id"]), \
         patch("app.lib.pyops.ServiceClient") as mock_service:
        mock_service.return_value.request.return_value = (204, None)
        response = client.delete("/controller/testset/testname/item/1", headers={"X-User": "testuser"})
    assert response.status_code == 204
    assert response.content == b""


def test_controller_proxy_times_out_against_a_silent_service(client, mock_git_ops, mock_db, tmp_path):
    """A running service row whose socket never answers (hung, or not yet
    bound): after web.timeout the proxy answers 504 with a WARNING — not a
    200 error body with an ERROR traceback."""
    import logging
    import zmq
    sock_dir = tmp_path / "pyops"
    sock_dir.mkdir()
    ctx = zmq.Context()
    silent = ctx.socket(zmq.ROUTER)
    silent.bind(f"ipc://{sock_dir}/silent_job.sock")
    try:
        with patch("app.lib.runner.find", return_value=["silent_job"]), \
             patch("app.lib.files.conf_web", return_value={"timeout": 1}), \
             patch("tempfile.gettempdir", return_value=str(tmp_path)):
            response = client.get("/controller/testset/testname/x", headers={"X-User": "testuser"})
    finally:
        silent.close(linger=0)
        ctx.term()
    assert response.status_code == 504
    assert response.json() == {"error": "Service job silent_job did not answer within 1s"}


def _controller_code(debug=False, body="body"):
    return (
        "set = 's1'\nservice = 'svc'\nname = 'My Panel'\n"
        f"debug = {debug}\nhead = 'head'\nmenu = 'menu'\nbody = {body!r}\n"
    )


_VIEWS = {"head.html": "<title>t</title>", "menu.html": "<li>m</li>", "body.html": "<p>committed</p>"}


class TestControllerRealSet:
    """/controller and /menu against a real git set repo (no git mocks)."""

    def test_task_is_not_a_controller_404(self, client, set_repo):
        """No controller of that name: 404, for the page and the proxy alike;
        the proxy's own errors carry the message in `error`."""
        set_repo("s1", committed={"job.task.py": "pass\n"})
        response = client.get("/controller/s1/job")
        assert response.status_code == 404
        assert response.json() == {"detail": "Scriptlet s1::job is not a controller"}
        response = client.get("/controller/s1/job/x")
        assert response.status_code == 404
        assert response.json() == {"error": "Scriptlet s1::job is not a controller"}

    def test_controller_beside_a_prefix_clash(self, client, set_repo):
        """`panel.x.task.py` (another, invalid name) never answers for the
        controller `panel`, whatever the directory order."""
        set_repo("s1", committed={"panel.x.task.py": "pass\n", "panel.controller.py": _controller_code(), **_VIEWS})
        with patch("os.listdir", return_value=["panel.x.task.py", "panel.controller.py"]):
            response = client.get("/controller/s1/panel")
        assert response.status_code == 200
        assert "<p>committed</p>" in response.text

    def test_controller_with_missing_view_404(self, client, set_repo):
        set_repo("s1", committed={"panel.controller.py": _controller_code(body="nobody"), **_VIEWS})
        response = client.get("/controller/s1/panel")
        assert response.status_code == 404
        assert response.json()["detail"] == "Scriptlet s1::nobody not existing"

    @pytest.mark.parametrize("debug, shown, hidden", [
        (True, "<p>draft</p>", "<p>committed</p>"),
        (False, "<p>committed</p>", "<p>draft</p>"),
    ], ids=["debug", "production"])
    def test_controller_view_source_follows_debug_flag(self, client, set_repo, debug, shown, hidden):
        """A debug (preview) controller renders its views' uncommitted drafts;
        a production controller renders the committed views."""
        set_repo(
            "s1",
            committed={"panel.controller.py": _controller_code(debug=debug), **_VIEWS},
            drafts={"body.html": "<p>draft</p>"},
        )
        response = client.get("/controller/s1/panel")
        assert response.status_code == 200
        assert shown in response.text
        assert hidden not in response.text

    def test_menu_lists_controller_under_its_display_name(self, client, set_repo):
        set_repo("s1", committed={"panel.controller.py": _controller_code(), **_VIEWS})
        response = client.get("/menu")
        assert response.status_code == 200
        body = response.json()
        section = next(m for m in body["menu"] if m["id"] == "s1")
        assert section["items"] == [
            {"id": "s1_panel", "name": "My Panel", "path": "/controller/s1/panel", "default": False},
        ]
        assert body["failures"] == []

    def test_unknown_set_page_404(self, client, set_repo):
        """The controller page for an unknown set answers 404 with the
        controller error page."""
        set_repo("s1", committed={"panel.controller.py": _controller_code(), **_VIEWS})
        response = client.get("/controller/nope/panel")
        assert response.status_code == 404
        assert "Controller Draft Error" in response.text
        assert "Set 'nope' does not exist" in response.text

    def test_error_page_escapes_request_values(self, client, set_repo):
        """The error page shows the set and name from the URL, and the error
        text (which quotes the set), as text: markup in them is escaped, not
        rendered."""
        set_repo("s1")
        response = client.get("/controller/<img src=x onerror=alert(1)>/<svg onload=alert(2)>")
        assert response.status_code == 400
        assert "<img" not in response.text
        assert "<svg" not in response.text
        assert "for &lt;img src=x onerror=alert(1)&gt;::&lt;svg onload=alert(2)&gt; failed" in response.text
        assert "Invalid set name '&lt;img src=x onerror=alert(1)&gt;'" in response.text

    def test_unknown_set_proxy_404(self, client, set_repo):
        set_repo("s1", committed={"panel.controller.py": _controller_code(), **_VIEWS})
        response = client.get("/controller/nope/panel/items")
        assert response.status_code == 404
        assert response.json() == {"error": "Controller execution failed: Set 'nope' does not exist"}
