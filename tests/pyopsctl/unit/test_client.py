"""Auth header construction + error → exit-code mapping.

The two auth pathways:
  - `api_key` present → `Authorization: Basic base64(user:api_key)` and
    no `X-User` (nginx will inject it from `$remote_user`)
  - `api_key` absent  → no `Authorization`, `X-User` falls through to
    `profile.user` or "pyopsctl"
"""
import base64
from unittest.mock import patch, MagicMock

import pytest


def _make_profile(api_key="", user=""):
    from pyopsctl.config import Profile
    return Profile(name="t", url="http://server.example", user=user, api_key=api_key)


def test_basic_auth_header_when_api_key_present():
    from pyopsctl.client import Client

    p = _make_profile(api_key="topsecret", user="ralph")
    with Client(p, timeout=1.0) as c:
        expected = "Basic " + base64.b64encode(b"ralph:topsecret").decode("ascii")
        assert c.session.headers.get("Authorization") == expected
        # When using Basic Auth, X-User is injected by the proxy — we don't send it.
        assert "X-User" not in c.session.headers


def test_x_user_header_when_no_api_key():
    from pyopsctl.client import Client

    p = _make_profile(api_key="", user="ralph")
    with Client(p, timeout=1.0) as c:
        assert "Authorization" not in c.session.headers
        assert c.session.headers.get("X-User") == "ralph"


def test_x_user_falls_back_to_pyopsctl_when_user_empty():
    from pyopsctl.client import Client

    p = _make_profile(api_key="", user="")
    with Client(p, timeout=1.0) as c:
        assert c.session.headers.get("X-User") == "pyopsctl"


def test_http_4xx_raises_httperror_with_status_4xx():
    from pyopsctl.client import Client, HTTPError

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        fake = MagicMock()
        fake.ok = False
        fake.status_code = 404
        fake.json.return_value = {"detail": "not found"}
        fake.text = ""
        fake.headers = {}
        with patch.object(c.session, "request", return_value=fake):
            with pytest.raises(HTTPError) as exc:
                c.get("/some/path")
            assert exc.value.status == 404
            assert "not found" in exc.value.message


def test_http_422_prints_each_validation_message():
    """A validation error's detail is a list: printed as '<field>: <msg>'."""
    from pyopsctl.client import Client, HTTPError

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        fake = MagicMock()
        fake.ok = False
        fake.status_code = 422
        fake.json.return_value = {"detail": [
            {"type": "greater_than_equal", "loc": ["body", "max_retries"],
             "msg": "Input should be greater than or equal to 0", "input": -1, "ctx": {"ge": 0}},
            {"type": "missing", "loc": ["body"], "msg": "Field required", "input": None},
        ]}
        fake.text = ""
        fake.headers = {}
        with patch.object(c.session, "request", return_value=fake):
            with pytest.raises(HTTPError) as exc:
                c.put("/scriptlet/job/item", json_body={})
            assert exc.value.status == 422
            assert exc.value.message == ("PUT /scriptlet/job/item: max_retries: Input should be greater "
                                         "than or equal to 0; body: Field required")


def test_http_5xx_raises_httperror_with_status_5xx():
    from pyopsctl.client import Client, HTTPError

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        fake = MagicMock()
        fake.ok = False
        fake.status_code = 503
        fake.json.side_effect = ValueError
        fake.text = "service degraded"
        fake.headers = {}
        with patch.object(c.session, "request", return_value=fake):
            with pytest.raises(HTTPError) as exc:
                c.get("/health")
            assert exc.value.status == 503


def test_timeout_raises_httperror_with_status_0():
    """Network timeouts are surfaced as HTTPError(status=0) — they're
    not HTTP responses but the caller still wants a single exception
    type. __main__ maps status=0 to exit code 6 (timeout)."""
    from pyopsctl.client import Client, HTTPError
    import requests as req

    p = _make_profile()
    with Client(p, timeout=0.001) as c:
        with patch.object(c.session, "request", side_effect=req.exceptions.Timeout):
            with pytest.raises(HTTPError) as exc:
                c.get("/whatever")
            assert exc.value.status == 0
            assert "timeout" in exc.value.message.lower()


def test_unreachable_server_raises_httperror_with_status_0():
    from pyopsctl.client import Client, HTTPError
    import requests as req

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        with patch.object(c.session, "request",
                          side_effect=req.exceptions.ConnectionError("nope")):
            with pytest.raises(HTTPError) as exc:
                c.get("/whatever")
            assert exc.value.status == 0
            assert "cannot reach" in exc.value.message


def test_json_response_decoded_to_python_object():
    from pyopsctl.client import Client

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        fake = MagicMock()
        fake.ok = True
        fake.status_code = 200
        fake.content = b'{"x": 1}'
        fake.headers = {"Content-Type": "application/json"}
        fake.json.return_value = {"x": 1}
        with patch.object(c.session, "request", return_value=fake):
            data = c.get("/api")
            assert data == {"x": 1}


def test_text_response_passes_through_as_string():
    from pyopsctl.client import Client

    p = _make_profile()
    with Client(p, timeout=1.0) as c:
        fake = MagicMock()
        fake.ok = True
        fake.status_code = 200
        fake.content = b"# HELP pyops_open_fds Open FDs\nfoo 1\n"
        fake.text = "# HELP pyops_open_fds Open FDs\nfoo 1\n"
        fake.headers = {"Content-Type": "text/plain"}
        with patch.object(c.session, "request", return_value=fake):
            text = c.get("/metrics")
            assert text.startswith("# HELP")
