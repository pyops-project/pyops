import pytest


def _pct(s):
    """Percent-encodes every byte of `s`, so ".." reaches a route's path
    parameter as ".." (the client does not resolve it) and NUL as NUL."""
    return "".join(f"%{b:02X}" for b in s.encode())


# Invalid set names: ".." resolves to the git base's parent (data/); before
# validation, get_repo_path git-init'ed and committed in there. A NUL byte
# cannot be part of a path at all.
_INVALID = [pytest.param("..", id="dotdot"), pytest.param("nul\x00byte", id="nul")]

# Every route that resolves a client-supplied set name, as
# (method, url, request kwargs) built for a given set name.
_ROUTES = [
    pytest.param(lambda s: ("PUT", "/scriptlet/job/item", {"json": {"set": s, "name": "job", "drop_done": True, "param": ""}}), id="job put"),
    pytest.param(lambda s: ("PUT", "/scriptlet/schedule/item", {"json": {"set": s, "name": "job", "drop_done": True, "schedule": "", "param": ""}}), id="schedule put"),
    pytest.param(lambda s: ("PUT", "/scriptlet/inventory/item", {"json": {"set": s, "name": "job", "type": "task"}}), id="inventory item put"),
    pytest.param(lambda s: ("POST", "/scriptlet/inventory/draft", {"json": {"set": s, "name": "job", "code": "pass"}}), id="inventory draft post"),
    pytest.param(lambda s: ("DELETE", "/scriptlet/inventory/item", {"params": {"set": s, "name": "job"}}), id="inventory item delete"),
    pytest.param(lambda s: ("DELETE", "/scriptlet/inventory/draft", {"params": {"set": s, "name": "job"}}), id="inventory draft delete"),
    pytest.param(lambda s: ("GET", "/scriptlet/inventory/code", {"params": {"set": s, "name": "job"}}), id="inventory code"),
    pytest.param(lambda s: ("GET", "/scriptlet/inventory/status", {"params": {"set": s, "name": "job"}}), id="inventory status"),
    pytest.param(lambda s: ("GET", "/repo/status", {"params": {"set": s}}), id="repo status"),
    pytest.param(lambda s: ("GET", f"/scriptlet/jslib/{_pct(s)}/lib", {}), id="jslib"),
]


def _git_dirs(tmp_path):
    """`.git` dirs in the git base and in its parent."""
    data = tmp_path / "data"
    return [p for p in (data / ".git", data / "git" / ".git") if p.exists()]


@pytest.mark.parametrize("set_name", _INVALID)
@pytest.mark.parametrize("route", _ROUTES)
def test_route_rejects_invalid_set_name_with_400(mock_client, tmp_path, route, set_name):
    method, url, kwargs = route(set_name)
    response = mock_client.request(method, url, **kwargs)
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid set name")
    assert _git_dirs(tmp_path) == []


def test_job_put_rejects_empty_set_name_with_400(mock_client, tmp_path):
    """"" resolves to the git base itself."""
    response = mock_client.put("/scriptlet/job/item", json={"set": "", "name": "job", "drop_done": True, "param": ""})
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid set name")
    assert _git_dirs(tmp_path) == []


@pytest.mark.parametrize("set_name", _INVALID)
def test_controller_page_rejects_invalid_set_name_with_400(mock_client, tmp_path, set_name):
    response = mock_client.get(f"/controller/{_pct(set_name)}/panel")
    assert response.status_code == 400
    assert "Controller Draft Error" in response.text
    assert "Invalid set name" in response.text
    assert _git_dirs(tmp_path) == []


@pytest.mark.parametrize("set_name", _INVALID)
def test_controller_proxy_rejects_invalid_set_name_with_400(mock_client, tmp_path, set_name):
    response = mock_client.get(f"/controller/{_pct(set_name)}/panel/items")
    assert response.status_code == 400
    assert response.json()["error"].startswith("Controller execution failed: Invalid set name")
    assert _git_dirs(tmp_path) == []
