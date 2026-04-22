"""
Smoke E2E tests: verify server startup, page loading, and a full task workflow.

Each test runs against a fresh PyOps instance with isolated data.
"""

import time

import pytest


class TestServerStartup:
    """Verify that the server starts cleanly and serves basic pages."""

    def test_root_page_loads(self, e2e_page, pyops_server):
        """Root page loads and contains the PyOps sidebar."""
        assert e2e_page.locator('[data-testid="app-title"]').is_visible()
        assert e2e_page.locator('[data-testid="sidebar-menu"]').is_visible()
        assert e2e_page.locator('[data-testid="content-frame"]').is_visible()
        pyops_server.assert_no_unexpected_errors()

    def test_menu_api_returns_default_sections(self, api, pyops_server):
        """The /menu endpoint returns the default Scriptlet menu section."""
        resp = api.get("/menu")
        assert resp.status_code == 200
        data = resp.json()
        assert "menu" in data
        menu = data["menu"]
        assert any(s["id"] == "scriptlet" for s in menu)
        scriptlet_section = next(s for s in menu if s["id"] == "scriptlet")
        item_ids = [i["id"] for i in scriptlet_section["items"]]
        assert "scriptlet_manager" in item_ids
        assert "scriptlet_monitor" in item_ids
        assert "scriptlet_scheduler" in item_ids
        assert "scriptlet_sets" in item_ids
        pyops_server.assert_no_unexpected_errors()

    def test_empty_inventory(self, api, pyops_server):
        """Fresh instance has no scriptlets."""
        resp = api.get("/scriptlet/inventory/list")
        assert resp.status_code == 200
        assert resp.json() == []
        pyops_server.assert_no_unexpected_errors()

    def test_empty_job_list(self, api, pyops_server):
        """Fresh instance has no jobs."""
        resp = api.get("/scriptlet/job/list")
        assert resp.status_code == 200
        assert resp.json() == []
        pyops_server.assert_no_unexpected_errors()

    def test_no_unexpected_errors_on_startup(self, pyops_server):
        """Server starts without any unexpected errors in the log."""
        pyops_server.assert_no_unexpected_errors()


class TestSetManagement:
    """Workflow: create a set, verify it appears, remove it."""

    def test_create_and_list_set(self, api, pyops_server):
        resp = api.post("/scriptlet/sets/create", json={"name": "e2e_test"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = api.get("/scriptlet/sets/list")
        assert resp.status_code == 200
        sets = resp.json()["sets"]
        assert any(s["name"] == "e2e_test" for s in sets)
        pyops_server.assert_no_unexpected_errors()

    def test_create_and_remove_set(self, api, pyops_server):
        api.post("/scriptlet/sets/create", json={"name": "to_remove"})

        resp = api.delete("/scriptlet/sets/remove/to_remove")
        assert resp.status_code == 200

        resp = api.get("/scriptlet/sets/list")
        sets = resp.json()["sets"]
        assert not any(s["name"] == "to_remove" for s in sets)
        pyops_server.assert_no_unexpected_errors()


class TestTaskWorkflow:
    """Full workflow: create set -> create task -> save code -> run -> verify completion."""

    def _create_set_and_task(self, api, set_name="wf_test", task_name="hello"):
        """Helper: create a set and a task scriptlet in it."""
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = api.put(
            "/scriptlet/inventory/item",
            json={"set": set_name, "name": task_name, "type": "task"},
        )
        assert resp.status_code == 200
        return resp.json()

    def _save_task_code(self, api, set_name, task_name, code):
        resp = api.post(
            "/scriptlet/inventory/item",
            json={"set": set_name, "name": task_name, "code": code},
        )
        assert resp.status_code == 200

    def _enqueue_task(self, api, set_name, task_name, param=""):
        resp = api.put(
            "/scriptlet/job/item",
            json={
                "set": set_name,
                "name": task_name,
                "drop_done": False,
                "max_retries": 0,
                "param": param,
                "kind": "task",
            },
        )
        assert resp.status_code == 200
        # enqueue returns {"job": "<job_id_string>"}
        job_id = resp.json()["job"]
        return job_id

    def _wait_for_job(self, api, job_id, terminal_states=None, timeout=30):
        """Poll job until it reaches a terminal state."""
        if terminal_states is None:
            terminal_states = {"done", "failed", "cancelled", "killed"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = api.get(f"/scriptlet/job/item?id={job_id}")
            assert resp.status_code == 200
            job = resp.json()
            if job["state"] in terminal_states:
                return job
            time.sleep(0.5)
        pytest.fail(f"Job {job_id} did not reach terminal state within {timeout}s (last state: {job['state']})")

    def test_create_and_run_simple_task(self, api, pyops_server):
        """Create a task that prints output, run it, verify it completes with stdout."""
        self._create_set_and_task(api, "wf_test", "hello")
        self._save_task_code(api, "wf_test", "hello", 'print("Hello from E2E test!")\n')

        job_id = self._enqueue_task(api, "wf_test", "hello")

        job = self._wait_for_job(api, job_id)
        assert job["state"] == "done", f"Expected done, got {job['state']}: {job.get('detail', '')}"
        assert job["stdout"] is not None
        assert "Hello from E2E test!" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_failing_task(self, api, pyops_server):
        """A task that raises an exception should end in 'failed' state."""
        self._create_set_and_task(api, "wf_fail", "bad_task")
        self._save_task_code(api, "wf_fail", "bad_task", 'raise RuntimeError("intentional failure")\n')

        job_id = self._enqueue_task(api, "wf_fail", "bad_task")

        job = self._wait_for_job(api, job_id)
        assert job["state"] == "failed"
        assert job["stderr"] is not None
        assert "intentional failure" in job["stderr"]
        # The intentional failure traceback is expected in server logs
        pyops_server.assert_no_unexpected_errors(allow=["intentional failure"])

    def test_task_with_param(self, api, pyops_server):
        """Task receives param (executed as code before main) and uses it."""
        self._create_set_and_task(api, "wf_param", "echo")
        code = 'print(f"greeting={msg}")\n'
        self._save_task_code(api, "wf_param", "echo", code)

        # param is exec'd as Python code before the main task code
        job_id = self._enqueue_task(api, "wf_param", "echo", param='msg = "hello_e2e"')
        job = self._wait_for_job(api, job_id)
        assert job["state"] == "done"
        assert "greeting=hello_e2e" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_task_lifecycle_in_job_list(self, api, pyops_server):
        """After running a task, it appears in the job list with correct metadata."""
        self._create_set_and_task(api, "wf_list", "listed")
        self._save_task_code(api, "wf_list", "listed", 'print("listed")\n')
        job_id = self._enqueue_task(api, "wf_list", "listed")

        self._wait_for_job(api, job_id)

        resp = api.get("/scriptlet/job/list")
        assert resp.status_code == 200
        jobs = resp.json()
        matching = [j for j in jobs if j["id"] == job_id]
        assert len(matching) == 1
        assert matching[0]["state"] == "done"
        pyops_server.assert_no_unexpected_errors()

    def test_delete_completed_job(self, api, pyops_server):
        """A completed job can be deleted."""
        self._create_set_and_task(api, "wf_del", "deleteme")
        self._save_task_code(api, "wf_del", "deleteme", 'print("bye")\n')
        job_id = self._enqueue_task(api, "wf_del", "deleteme")
        self._wait_for_job(api, job_id)

        resp = api.delete(f"/scriptlet/job/item?id={job_id}")
        assert resp.status_code == 200

        # Job should eventually disappear from list (state becomes "remove"/"removed")
        resp = api.get("/scriptlet/job/list")
        jobs = resp.json()
        visible = [j for j in jobs if j["id"] == job_id]
        assert len(visible) == 0
        pyops_server.assert_no_unexpected_errors()


class TestScriptletCRUD:
    """Verify scriptlet create, read, update, delete via API."""

    def test_create_read_update_delete(self, api, pyops_server):
        api.post("/scriptlet/sets/create", json={"name": "crud_test"})

        # Create
        resp = api.put(
            "/scriptlet/inventory/item",
            json={"set": "crud_test", "name": "my_task", "type": "task"},
        )
        assert resp.status_code == 200

        # Read (initial code)
        resp = api.get("/scriptlet/inventory/code?set=crud_test&name=my_task")
        assert resp.status_code == 200
        assert "code" in resp.json()

        # Update
        resp = api.post(
            "/scriptlet/inventory/item",
            json={"set": "crud_test", "name": "my_task", "code": 'print("updated")\n'},
        )
        assert resp.status_code == 200

        # Read updated
        resp = api.get("/scriptlet/inventory/code?set=crud_test&name=my_task")
        assert resp.status_code == 200
        assert "updated" in resp.json()["code"]

        # Appears in inventory
        resp = api.get("/scriptlet/inventory/list")
        items = resp.json()
        assert any(i["set"] == "crud_test" and i["name"] == "my_task" for i in items)

        # Delete
        resp = api.delete("/scriptlet/inventory/item?set=crud_test&name=my_task")
        assert resp.status_code == 200

        # Gone from inventory
        resp = api.get("/scriptlet/inventory/list")
        items = resp.json()
        assert not any(i["set"] == "crud_test" and i["name"] == "my_task" for i in items)

        pyops_server.assert_no_unexpected_errors()


class TestWebUIWorkflow:
    """Browser-based workflow tests using Playwright."""

    def test_navigate_to_manager_via_menu(self, e2e_page, pyops_server):
        """Click the Manager menu item and verify the manager page loads in the iframe."""
        menu_link = e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]')
        menu_link.click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator('[data-testid="manager-navbar"]').wait_for(state="visible", timeout=10000)
        assert frame.locator('[data-testid="manager-create"]').is_visible()
        pyops_server.assert_no_unexpected_errors()

    def test_navigate_to_monitor(self, e2e_page, pyops_server):
        """Click the Monitor menu item and verify the monitor page loads."""
        menu_link = e2e_page.locator('[data-testid="menu-link-scriptlet_monitor"]')
        menu_link.click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator('[data-testid="monitor-navbar"]').wait_for(state="visible", timeout=10000)
        assert frame.locator('[data-testid="monitor-table"]').is_visible()
        pyops_server.assert_no_unexpected_errors()

    def test_navigate_to_sets(self, e2e_page, pyops_server):
        """Click the Sets menu item and verify the sets page loads."""
        menu_link = e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]')
        menu_link.click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator('[data-testid="sets-navbar"]').wait_for(state="visible", timeout=10000)
        assert frame.locator('[data-testid="sets-create"]').is_visible()
        pyops_server.assert_no_unexpected_errors()

    def test_create_set_via_ui(self, e2e_page, api, pyops_server):
        """Create a set through the Sets UI and verify it appears."""
        e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]').click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator('[data-testid="sets-navbar"]').wait_for(state="visible", timeout=10000)

        # Open create modal
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5000)

        # Fill in name and submit
        frame.locator('[data-testid="sets-create-name"]').fill("ui_test_set")
        frame.locator('[data-testid="sets-create-submit"]').click()

        # Wait for modal to close and table to update
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="hidden", timeout=10000)

        # Verify via API
        resp = api.get("/scriptlet/sets/list")
        sets = resp.json()["sets"]
        assert any(s["name"] == "ui_test_set" for s in sets)
        pyops_server.assert_no_unexpected_errors()

    def test_create_scriptlet_via_manager_ui(self, e2e_page, api, pyops_server):
        """Create a scriptlet through the Manager create modal and verify it appears."""
        # Set must exist before the modal can select it.
        api.post("/scriptlet/sets/create", json={"name": "ui_create_set"})

        e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator('[data-testid="manager-navbar"]').wait_for(state="visible", timeout=10_000)

        # Open create modal.
        frame.locator('[data-testid="manager-create"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(state="visible", timeout=5_000)

        # Wait for the set dropdown to be populated (populateSets fetch).
        frame.locator(
            '[data-testid="manager-create-set"] option[value="ui_create_set"]'
        ).wait_for(state="attached", timeout=5_000)
        frame.locator('[data-testid="manager-create-set"]').select_option(value="ui_create_set")

        frame.locator('[data-testid="manager-create-name"]').fill("new_task")
        # Type defaults to "task" — no change needed.

        frame.locator('[data-testid="manager-create-submit"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(state="hidden", timeout=10_000)

        # Verify the new scriptlet row appears in the Manager table.
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator(
            '[data-testid="scriptlet-row-ui_create_set-new_task"]'
        ).wait_for(state="visible", timeout=10_000)

        pyops_server.assert_no_unexpected_errors()
