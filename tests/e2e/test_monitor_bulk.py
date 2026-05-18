"""Monitor bulk-action E2E tests.

Covers the navbar buttons that operate on every matching job at once:
  - monitor-cancel-all: cancel all in-flight jobs (not yet terminal)
  - monitor-delete-done: delete every done/cancelled job
  - monitor-delete-failed: delete every failed/killed job

Each test drives the workflow through the Playwright page and verifies the
result through the REST API (fast, deterministic — avoids UI race on list
refresh timing).
"""

import time

import pytest


SET_NAME = "monbulk_e2e"
TASK_DONE = "quick_task"
TASK_FAIL = "bad_task"
TASK_LONG = "long_task"

QUICK_CODE = 'print("ok")\n'
FAIL_CODE = 'raise RuntimeError("planned failure")\n'
LONG_CODE = "import time\nfor _ in range(60):\n    time.sleep(0.5)\n"


def _create_scriptlet(api, name, code, set_factory, commit_set):
    set_factory(SET_NAME)
    resp = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET_NAME, "name": name, "type": "task"},
    )
    assert resp.status_code == 200, resp.text
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": SET_NAME, "name": name, "code": code},
    )
    assert resp.status_code == 200, resp.text
    # kind=task reads committed content; bake the draft into HEAD so the
    # bulk-monitor tests get a runnable scriptlet without going through
    # the set_commit_drafts queue.
    commit_set(SET_NAME, f"add {name}")


def _enqueue(api, name, param=""):
    resp = api.put(
        "/scriptlet/job/item",
        json={
            "set": SET_NAME,
            "name": name,
            "drop_done": False,
            "max_retries": 0,
            "param": param,
            "kind": "task",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["job"]


def _wait_for_state(api, job_id, states, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = api.get(f"/scriptlet/job/item?id={job_id}")
        if resp.status_code == 404:
            return {"state": "removed"}
        assert resp.status_code == 200, resp.text
        job = resp.json()
        if job["state"] in states:
            return job
        time.sleep(0.3)
    pytest.fail(f"Job {job_id} never reached {states} (last: {job.get('state')})")


def _nav_monitor(e2e_page):
    e2e_page.locator('[data-testid="menu-link-scriptlet_monitor"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="monitor-navbar"]').wait_for(
        state="visible", timeout=10_000
    )
    return frame


def _wait_until_row_gone(api, job_id, timeout=15):
    """Poll the job list until the job is absent (deleted)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = api.get("/scriptlet/job/list")
        assert resp.status_code == 200
        if not any(j["id"] == job_id for j in resp.json()):
            return
        time.sleep(0.3)
    pytest.fail(f"Job {job_id} still present after {timeout}s")


class TestMonitorBulkOps:
    def test_cancel_all_cancels_every_running_job(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """monitor-cancel-all should cancel every in-flight job simultaneously."""
        _create_scriptlet(api_anon, TASK_LONG, LONG_CODE, set_factory, commit_set)

        job_ids = [_enqueue(api_anon, TASK_LONG) for _ in range(3)]
        # Ensure at least one is actually running before bulk-cancelling.
        _wait_for_state(api_anon, job_ids[0], {"running"}, timeout=30)

        frame = _nav_monitor(e2e_page)
        frame.locator('[data-testid="monitor-refresh"]').click()
        frame.locator('[data-testid="monitor-cancel-all"]').click()

        for job_id in job_ids:
            job = _wait_for_state(
                api_anon, job_id, {"cancelled", "done", "failed", "killed"}, timeout=30
            )
            assert job["state"] in {"cancelled", "killed"}, (
                f"Job {job_id} ended {job['state']}, expected cancelled/killed"
            )

        pyops_server.assert_no_unexpected_errors()

    def test_delete_done_removes_only_done_jobs(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """monitor-delete-done deletes done/cancelled jobs, leaves failed ones alone."""
        _create_scriptlet(api_anon, TASK_DONE, QUICK_CODE, set_factory, commit_set)
        _create_scriptlet(api_anon, TASK_FAIL, FAIL_CODE, set_factory, commit_set)

        done_id = _enqueue(api_anon, TASK_DONE)
        fail_id = _enqueue(api_anon, TASK_FAIL)
        _wait_for_state(api_anon, done_id, {"done"}, timeout=30)
        _wait_for_state(api_anon, fail_id, {"failed"}, timeout=30)

        frame = _nav_monitor(e2e_page)
        frame.locator('[data-testid="monitor-refresh"]').click()
        frame.locator('[data-testid="monitor-delete-done"]').click()

        _wait_until_row_gone(api_anon, done_id)
        # Failed job must still be around — this button only targets done/cancelled.
        resp = api_anon.get(f"/scriptlet/job/item?id={fail_id}")
        assert resp.status_code == 200
        assert resp.json()["state"] == "failed"

        pyops_server.assert_no_unexpected_errors(allow=["planned failure"])

    def test_delete_failed_removes_only_failed_jobs(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """monitor-delete-failed deletes failed/killed jobs, leaves done ones alone."""
        _create_scriptlet(api_anon, TASK_DONE, QUICK_CODE, set_factory, commit_set)
        _create_scriptlet(api_anon, TASK_FAIL, FAIL_CODE, set_factory, commit_set)

        done_id = _enqueue(api_anon, TASK_DONE)
        fail_id = _enqueue(api_anon, TASK_FAIL)
        _wait_for_state(api_anon, done_id, {"done"}, timeout=30)
        _wait_for_state(api_anon, fail_id, {"failed"}, timeout=30)

        frame = _nav_monitor(e2e_page)
        frame.locator('[data-testid="monitor-refresh"]').click()
        frame.locator('[data-testid="monitor-delete-failed"]').click()

        _wait_until_row_gone(api_anon, fail_id)
        # Done job must still be around.
        resp = api_anon.get(f"/scriptlet/job/item?id={done_id}")
        assert resp.status_code == 200
        assert resp.json()["state"] == "done"

        pyops_server.assert_no_unexpected_errors(allow=["planned failure"])
