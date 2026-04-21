"""
Debug mode E2E tests.

Covers:
  1. GET /debug/port — returns the configured debug port for the requesting
     user (from conf_users()) or 400 if not configured.
  2. Override key validation — PUT /scriptlet/job/item with kind="debug"
     and malformed override keys returns HTTP 400.
  3. Override bypasses existence check — when the main scriptlet key is
     present in overrides, the job is accepted even if no committed
     scriptlet file exists yet.
  4. Override code runs instead of committed code — a debug job with an
     override executes the override's code, not the on-disk scriptlet.

The e2e server config provides debug_port=15679 for the "anonymous" user
(no X-User header → owner "anonymous").  The "e2e_test_user" identity
(the default `api` fixture) has no debug_port entry, so it gets 400.
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET = "debug_e2e"
ANON_DEBUG_PORT = 15679   # matches e2e_env config: users.anonymous.debug_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_job(api, job_id, timeout=30):
    terminal = {"done", "failed", "cancelled", "killed"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = api.get(f"/scriptlet/job/item?id={job_id}")
        assert resp.status_code == 200
        job = resp.json()
        if job["state"] in terminal:
            return job
        time.sleep(0.5)
    pytest.fail(
        f"Job {job_id} did not reach terminal state within {timeout}s "
        f"(last: {job['state']})"
    )


def _create(api, set_name, name, type_, code):
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "type": type_},
    )
    assert r.status_code == 200, f"create {name} failed: {r.text}"
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "code": code},
    )
    assert r.status_code == 200, f"save {name} failed: {r.text}"


# ---------------------------------------------------------------------------
# Tests: /debug/port endpoint
# ---------------------------------------------------------------------------


class TestDebugPort:
    """GET /debug/port returns port from config or 400 if not configured."""

    def test_configured_user_gets_port(self, api_anon, pyops_server):
        """Anonymous user (debug_port=15679 in config) receives the correct port."""
        resp = api_anon.get("/debug/port")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert data["port"] == ANON_DEBUG_PORT
        pyops_server.assert_no_unexpected_errors()

    def test_unconfigured_user_gets_400(self, api, pyops_server):
        """User with no debug_port entry in config receives HTTP 400."""
        # The default `api` fixture sends X-User: e2e_test_user, which has
        # no entry in the e2e_env users config.
        resp = api.get("/debug/port")
        assert resp.status_code == 400
        assert "debug_port" in resp.json().get("detail", "")
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: debug job enqueue — API contract
# ---------------------------------------------------------------------------


class TestDebugJobEnqueue:
    """PUT /scriptlet/job/item with kind='debug' — API-level validation."""

    def test_invalid_override_key_format_returns_400(self, api_anon, pyops_server):
        """Override keys must match set::name format; a bare name returns 400."""
        api_anon.post("/scriptlet/sets/create", json={"name": SET})
        _create(api_anon, SET, "task_a", "task", 'print("committed")\n')

        resp = api_anon.put(
            "/scriptlet/job/item",
            json={
                "set": SET,
                "name": "task_a",
                "drop_done": False,
                "max_retries": 0,
                "param": "",
                "kind": "debug",
                "overrides": {"bad_key_no_double_colon": "print('x')"},
            },
        )
        assert resp.status_code == 400
        assert "set::name" in resp.json().get("detail", "")
        pyops_server.assert_no_unexpected_errors()

    def test_override_covering_main_key_bypasses_existence_check(
        self, api_anon, pyops_server
    ):
        """When overrides contains the main set::name key, no committed scriptlet is required.

        This is the VSIX local-only debug workflow: the extension provides
        the code via overrides and the scriptlet may not yet be committed.
        """
        api_anon.post("/scriptlet/sets/create", json={"name": SET + "_bypass"})
        # Deliberately do NOT create a committed scriptlet.

        resp = api_anon.put(
            "/scriptlet/job/item",
            json={
                "set": SET + "_bypass",
                "name": "uncommitted_task",
                "drop_done": False,
                "max_retries": 0,
                "param": "",
                "kind": "debug",
                "overrides": {
                    f"{SET}_bypass::uncommitted_task": 'print("from_override")',
                },
            },
        )
        # Should be accepted (HTTP 200) — override covers the missing scriptlet.
        assert resp.status_code == 200, (
            f"Expected 200 when override covers main key, got {resp.status_code}: {resp.text}"
        )
        pyops_server.assert_no_unexpected_errors(
            allow=["debugpy", "debug_port", "adapter"]
        )

    def test_debug_without_override_requires_committed_scriptlet(
        self, api_anon, pyops_server
    ):
        """kind=debug without an override for the main key needs the committed scriptlet."""
        api_anon.post("/scriptlet/sets/create", json={"name": SET + "_nocomm"})
        # No scriptlet created → should get 404.

        resp = api_anon.put(
            "/scriptlet/job/item",
            json={
                "set": SET + "_nocomm",
                "name": "missing_task",
                "drop_done": False,
                "max_retries": 0,
                "param": "",
                "kind": "debug",
                "overrides": {
                    # Override for a *library*, not the main task — doesn't bypass check.
                    f"{SET}_nocomm::some_lib": "def helper(): pass",
                },
            },
        )
        assert resp.status_code == 404
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: debug override execution
# ---------------------------------------------------------------------------


class TestDebugOverrideExecution:
    """Debug job with overrides runs the override code, not the committed code."""

    def test_override_code_replaces_committed_code(self, api_anon, pyops_server):
        """A debug job's override for the main scriptlet executes instead of disk code.

        The committed scriptlet prints "committed_output". The override prints
        "override_output". The job's stdout must contain "override_output".

        NOTE: This test installs debugpy into the set venv (first debug job in
        a fresh environment) and therefore allows a 120-second timeout.
        Port 15679 must be free on the test host.
        """
        api_anon.post("/scriptlet/sets/create", json={"name": SET + "_exec"})
        _create(
            api_anon,
            SET + "_exec",
            "target_task",
            "task",
            'print("committed_output")\n',
        )

        resp = api_anon.put(
            "/scriptlet/job/item",
            json={
                "set": SET + "_exec",
                "name": "target_task",
                "drop_done": False,
                "max_retries": 0,
                "param": "",   # no #>AWAIT_DEBUG → debugpy listens but doesn't block
                "kind": "debug",
                "overrides": {
                    f"{SET}_exec::target_task": 'print("override_output")',
                },
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job"]

        job = _wait_for_job(api_anon, job_id, timeout=120)
        assert job["state"] == "done", (
            f"Debug job expected done, got {job['state']}: {job.get('detail', '')}"
        )
        assert "override_output" in job["stdout"], (
            "Override code should have run"
        )
        assert "committed_output" not in job["stdout"], (
            "Committed code should NOT have run"
        )
        pyops_server.assert_no_unexpected_errors(
            allow=["debugpy", "adapter", "listen"]
        )
