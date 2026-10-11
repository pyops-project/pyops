"""HTTP client for pyopsctl.

Thin wrapper around `requests.Session` that:
  - applies the auth pathway resolved by `config.load()`:
      * `api_key` present → `Authorization: Basic base64(user:api_key)`
      * `api_key` absent  → `X-User: <profile.user or "pyopsctl">`
  - normalizes 4xx/5xx into HTTPError so command modules don't need to
    repeat status-code handling
  - keeps one Session alive for the duration of a single CLI invocation
    so multi-call commands (e.g. `job view --follow`, `debug start
    --wait-running`) re-use the underlying connection.
"""
import base64
import urllib.parse
from typing import Any

import requests

from pyopsctl.config import Profile, DEFAULT_X_USER


class HTTPError(Exception):
    """Server returned a non-2xx response. status maps to CLI exit code
    in __main__.main: 4xx → 4, 5xx → 5."""

    def __init__(self, status: int, message: str, *, body: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body


# Re-export so __main__ can `from pyopsctl.client import HTTPError, ConfigError`
from pyopsctl.config import ConfigError  # noqa: E402,F401


class Client:
    """Connection-reusing HTTP client bound to a resolved Profile.

    Use as a context manager from command handlers:

        with Client(profile, timeout=args.timeout) as c:
            data = c.get("/scriptlet/job/list")
    """

    def __init__(self, profile: Profile, *, timeout: float = 30.0):
        self.profile = profile
        self.timeout = timeout
        self.session = requests.Session()
        if profile.api_key:
            token = f"{profile.user}:{profile.api_key}".encode("utf-8")
            self.session.headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
        else:
            self.session.headers["X-User"] = profile.user or DEFAULT_X_USER

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.session.close()

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return self.profile.url + (path if path.startswith("/") else "/" + path)

    def request(self, method: str, path: str, *,
                params: dict | None = None,
                json_body: Any = None,
                data: Any = None,
                stream: bool = False) -> requests.Response:
        try:
            resp = self.session.request(
                method, self._url(path),
                params=params, json=json_body, data=data,
                timeout=self.timeout, stream=stream,
            )
        except requests.exceptions.Timeout as ex:
            raise HTTPError(0, f"timeout after {self.timeout}s talking to {self.profile.url}") from ex
        except requests.exceptions.ConnectionError as ex:
            raise HTTPError(0, f"cannot reach {self.profile.url}: {ex}") from ex

        if not resp.ok:
            body: Any = None
            try:
                body = resp.json()
                message = body.get("detail") or body.get("error") or resp.text
            except ValueError:
                message = resp.text or resp.reason
            raise HTTPError(resp.status_code, f"{method} {path}: {message}", body=body)
        return resp

    def get(self, path: str, **kw) -> Any:
        resp = self.request("GET", path, **kw)
        return _decode(resp)

    def post(self, path: str, **kw) -> Any:
        resp = self.request("POST", path, **kw)
        return _decode(resp)

    def put(self, path: str, **kw) -> Any:
        resp = self.request("PUT", path, **kw)
        return _decode(resp)

    def delete(self, path: str, **kw) -> Any:
        resp = self.request("DELETE", path, **kw)
        return _decode(resp)

    def cancel_job(self, job_id: str) -> bool:
        """`GET /scriptlet/job/cancel` for `job_id`. True when the job is
        cancelled (or was being cancelled already); False when it had
        already finished or is being deleted — the server's 409, which
        leaves the job unchanged and is a no-op for a caller that only
        wants the job to stop. Any other error raises HTTPError."""
        try:
            self.get("/scriptlet/job/cancel", params={"id": job_id})
        except HTTPError as ex:
            if ex.status == 409:
                return False
            raise
        return True

    def get_bytes(self, path: str, **kw) -> bytes:
        resp = self.request("GET", path, stream=True, **kw)
        return resp.content

    def get_text(self, path: str, **kw) -> str:
        resp = self.request("GET", path, **kw)
        return resp.text


def _decode(resp: requests.Response) -> Any:
    """Decode a response. JSON when content-type says so; otherwise plain
    text. 204 No Content collapses to None."""
    if resp.status_code == 204 or not resp.content:
        return None
    ct = resp.headers.get("Content-Type", "")
    if ct.startswith("application/json"):
        return resp.json()
    return resp.text


def quote_path_segment(s: str) -> str:
    """URL-encode a single path segment, keeping it safe for paths like
    `/scriptlet/sets/remove/{name}` where `name` might contain reserved
    chars."""
    return urllib.parse.quote(s, safe="")
