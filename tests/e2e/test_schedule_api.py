"""
Schedule REST API E2E tests — direct endpoint calls (not UI-driven).

Covers:
  - PUT  /scriptlet/schedule/item       create + update
  - GET  /scriptlet/schedule/list       listing
  - GET  /scriptlet/schedule/item       fetch by id + 404
  - GET  /scriptlet/schedule/run        manual trigger + 404
  - GET  /scriptlet/schedule/activate   + 404
  - GET  /scriptlet/schedule/deactivate + 404
  - DELETE /scriptlet/schedule/item     delete + 404

Cron workflow through the UI is covered in test_cron_workflow.py. This file
locks down the JSON contract so the VSIX and other clients have a stable API.
"""

import time

import pytest


SET = "schedule_api_e2e"
TASK = "scheduled_task"
CRON_EXPR = "*/5 * * * *"


def _setup_task(api):
    api.post("/scriptlet/sets/create", json={"name": SET})
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET, "name": TASK, "type": "task"},
    )
    assert r.status_code == 200
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": SET, "name": TASK, "code": 'print("scheduled")\n'},
    )
    assert r.status_code == 200


def _create_schedule(api, cron=CRON_EXPR, name=TASK, set_name=SET):
    r = api.put(
        "/scriptlet/schedule/item",
        json={
            "set": set_name,
            "name": name,
            "drop_done": False,
            "max_retries": 0,
            "schedule": cron,
            "param": "",
        },
    )
    assert r.status_code == 200, f"create schedule failed: {r.text}"
    return r.json()["schedule"]


class TestScheduleCreate:
    """PUT /scriptlet/schedule/item — create and update."""

    def test_create_schedule_returns_id(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)
        assert isinstance(sched_id, int) and sched_id > 0
        pyops_server.assert_no_unexpected_errors()

    def test_create_schedule_for_missing_task_404(self, api, pyops_server):
        """Creating a schedule for a non-existent task returns 404."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_miss"})
        r = api.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET + "_miss",
                "name": "no_such_task",
                "drop_done": False,
                "max_retries": 0,
                "schedule": CRON_EXPR,
                "param": "",
            },
        )
        assert r.status_code == 404
        # git logs 'fatal: path ... does not exist in HEAD' for the missing task,
        # which is the normal signal when the scriptlet was never committed.
        pyops_server.assert_no_unexpected_errors(allow=["does not exist in 'HEAD'"])

    def test_update_schedule_changes_fields(self, api, pyops_server):
        """PUT with id updates an existing schedule's fields."""
        _setup_task(api)
        sched_id = _create_schedule(api)

        r = api.put(
            "/scriptlet/schedule/item",
            json={
                "id": sched_id,
                "set": SET,
                "name": TASK,
                "drop_done": True,
                "max_retries": 3,
                "schedule": "0 0 * * *",
                "param": "updated=1",
            },
        )
        assert r.status_code == 200
        assert r.json()["schedule"] == sched_id

        # Verify via GET item.
        r = api.get(f"/scriptlet/schedule/item?id={sched_id}")
        assert r.status_code == 200
        item = r.json()
        assert item["schedule"] == "0 0 * * *"
        assert item["drop_done"] is True
        assert item["max_retries"] == 3
        assert item["param"] == "updated=1"
        pyops_server.assert_no_unexpected_errors()

    def test_update_unknown_schedule_returns_404(self, api, pyops_server):
        _setup_task(api)
        r = api.put(
            "/scriptlet/schedule/item",
            json={
                "id": 999999,
                "set": SET,
                "name": TASK,
                "drop_done": False,
                "max_retries": 0,
                "schedule": CRON_EXPR,
                "param": "",
            },
        )
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()


class TestScheduleListAndGet:
    """GET /scriptlet/schedule/list and GET /scriptlet/schedule/item."""

    def test_list_returns_created_schedules(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)

        r = api.get("/scriptlet/schedule/list")
        assert r.status_code == 200
        schedules = r.json()
        assert any(s["id"] == sched_id for s in schedules)
        match = next(s for s in schedules if s["id"] == sched_id)
        assert match["set"] == SET
        assert match["name"] == TASK
        assert match["active"] is True
        assert match["schedule"] == CRON_EXPR
        pyops_server.assert_no_unexpected_errors()

    def test_empty_list_on_fresh_server(self, api, pyops_server):
        r = api.get("/scriptlet/schedule/list")
        assert r.status_code == 200
        assert r.json() == []
        pyops_server.assert_no_unexpected_errors()

    def test_get_schedule_by_id(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)
        r = api.get(f"/scriptlet/schedule/item?id={sched_id}")
        assert r.status_code == 200
        item = r.json()
        assert item["id"] == sched_id
        assert item["set"] == SET
        pyops_server.assert_no_unexpected_errors()

    def test_get_unknown_schedule_returns_404(self, api, pyops_server):
        r = api.get("/scriptlet/schedule/item?id=999999")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()


class TestScheduleRun:
    """GET /scriptlet/schedule/run manually triggers a schedule as a job."""

    def test_run_enqueues_job_with_matching_set_name(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)

        r = api.get(f"/scriptlet/schedule/run?id={sched_id}")
        assert r.status_code == 200
        job_id = r.json()["job"]
        assert job_id

        # Wait for terminal state.
        terminal = {"done", "failed", "cancelled", "killed"}
        deadline = time.time() + 30
        last = None
        while time.time() < deadline:
            resp = api.get(f"/scriptlet/job/item?id={job_id}")
            assert resp.status_code == 200
            last = resp.json()
            if last["state"] in terminal:
                break
            time.sleep(0.3)

        assert last and last["state"] == "done", f"expected done, got {last}"
        assert "scheduled" in (last.get("stdout") or "")
        pyops_server.assert_no_unexpected_errors()

    def test_run_unknown_schedule_returns_404(self, api, pyops_server):
        r = api.get("/scriptlet/schedule/run?id=999999")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_run_updates_last_timestamp(self, api, pyops_server):
        """After manual run, schedule.last should advance."""
        _setup_task(api)
        sched_id = _create_schedule(api)

        before = api.get(f"/scriptlet/schedule/item?id={sched_id}").json()["last"]
        time.sleep(1.1)
        r = api.get(f"/scriptlet/schedule/run?id={sched_id}")
        assert r.status_code == 200
        after = api.get(f"/scriptlet/schedule/item?id={sched_id}").json()["last"]

        assert after != before, f"last timestamp should advance: {before} -> {after}"
        pyops_server.assert_no_unexpected_errors()


class TestScheduleActivateDeactivate:
    """Activate/deactivate toggles and 404 handling."""

    def test_deactivate_then_activate(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)

        r = api.get(f"/scriptlet/schedule/deactivate?id={sched_id}")
        assert r.status_code == 200
        item = api.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert item["active"] is False

        r = api.get(f"/scriptlet/schedule/activate?id={sched_id}")
        assert r.status_code == 200
        item = api.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert item["active"] is True
        pyops_server.assert_no_unexpected_errors()

    def test_activate_unknown_404(self, api, pyops_server):
        r = api.get("/scriptlet/schedule/activate?id=999999")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_deactivate_unknown_404(self, api, pyops_server):
        r = api.get("/scriptlet/schedule/deactivate?id=999999")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()


class TestScheduleDelete:
    """DELETE /scriptlet/schedule/item."""

    def test_delete_removes_from_list(self, api, pyops_server):
        _setup_task(api)
        sched_id = _create_schedule(api)

        r = api.delete(f"/scriptlet/schedule/item?id={sched_id}")
        assert r.status_code == 200

        r = api.get("/scriptlet/schedule/list")
        assert not any(s["id"] == sched_id for s in r.json())

        r = api.get(f"/scriptlet/schedule/item?id={sched_id}")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_delete_unknown_returns_404(self, api, pyops_server):
        r = api.delete("/scriptlet/schedule/item?id=999999")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()
