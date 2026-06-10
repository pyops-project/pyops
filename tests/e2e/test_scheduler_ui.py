"""Scheduler UI E2E tests.

Covers the UI workflows not exercised by test_schedule_api.py:
  - Edit-existing-schedule modal (scheduler-edit-*)
  - Delete schedule (confirm dialog + row removal)
  - Create modal with every field populated (param, max-retries, drop-done)
  - Create modal Cancel button
"""

import time

import pytest

from tests.e2e.conftest import expand_set


SET_NAME = "schedui_e2e"
TASK_NAME = "dummy"
TASK_CODE = 'print("hi")\n'


def _prepare_set_and_task(api, set_factory, commit_set):
    set_factory(SET_NAME)
    resp = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET_NAME, "name": TASK_NAME, "type": "task"},
    )
    assert resp.status_code == 200, resp.text
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": SET_NAME, "name": TASK_NAME, "code": TASK_CODE},
    )
    assert resp.status_code == 200, resp.text
    commit_set(SET_NAME, f"add {TASK_NAME}")


def _nav_scheduler(e2e_page):
    e2e_page.locator('[data-testid="menu-link-scriptlet_scheduler"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="scheduler-navbar"]').wait_for(
        state="visible", timeout=10_000
    )
    return frame


def _open_create_modal(frame):
    frame.locator('[data-testid="scheduler-create"]').click()
    frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
        state="visible", timeout=5_000
    )


def _submit_create(frame, **fields):
    if fields.get("set") is not None:
        frame.locator('[data-testid="scheduler-create-set"]').fill(fields["set"])
    if fields.get("name") is not None:
        frame.locator('[data-testid="scheduler-create-name"]').fill(fields["name"])
    if fields.get("schedule") is not None:
        frame.locator('[data-testid="scheduler-create-schedule"]').fill(fields["schedule"])
    if fields.get("param") is not None:
        frame.locator('[data-testid="scheduler-create-param"]').fill(fields["param"])
    if fields.get("max_retries") is not None:
        frame.locator('[data-testid="scheduler-create-max-retries"]').fill(str(fields["max_retries"]))
    if fields.get("drop_done"):
        frame.locator('[data-testid="scheduler-create-drop-done"]').check()
    frame.locator('[data-testid="scheduler-create-submit"]').click()


def _first_schedule_row(frame):
    row = frame.locator('[data-testid^="schedule-row-"]').first
    row.wait_for(state="visible", timeout=5_000)
    return row


class TestSchedulerUI:
    def test_create_modal_cancel_closes_without_creating(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Cancel on the create modal closes it and creates nothing."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        frame = _nav_scheduler(e2e_page)

        _open_create_modal(frame)
        frame.locator('[data-testid="scheduler-create-set"]').fill(SET_NAME)
        frame.locator('[data-testid="scheduler-create-name"]').fill(TASK_NAME)
        frame.locator('[data-testid="scheduler-create-cancel"]').click()

        frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
            state="hidden", timeout=5_000
        )
        resp = api_anon.get("/scriptlet/schedule/list")
        assert resp.status_code == 200
        assert resp.json() == []
        pyops_server.assert_no_unexpected_errors()

    def test_create_with_all_fields_persists_values(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Every field on the create modal (param, max_retries, drop_done) round-trips."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        frame = _nav_scheduler(e2e_page)

        _open_create_modal(frame)
        _submit_create(
            frame,
            set=SET_NAME,
            name=TASK_NAME,
            schedule="*/5 * * * *",
            param="x = 1",
            max_retries=3,
            drop_done=True,
        )
        frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        schedules = api_anon.get("/scriptlet/schedule/list").json()
        assert len(schedules) == 1
        s = schedules[0]
        assert s["set"] == SET_NAME
        assert s["name"] == TASK_NAME
        assert s["schedule"] == "*/5 * * * *"
        assert s["param"] == "x = 1"
        assert s["max_retries"] == 3
        assert s["drop_done"] is True

        # Deactivate so the scheduler doesn't start firing it.
        api_anon.get(f"/scriptlet/schedule/deactivate?id={s['id']}")
        pyops_server.assert_no_unexpected_errors()

    def test_edit_modal_updates_existing_schedule(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Opening Edit, changing fields, and submitting updates the schedule."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        # Create via API — the create flow is already covered above.
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": False,
                "max_retries": 0,
                "schedule": "0 0 * * *",
                "param": "",
            },
        )
        assert resp.status_code == 200
        sched_id = resp.json()["schedule"]
        api_anon.get(f"/scriptlet/schedule/deactivate?id={sched_id}")

        frame = _nav_scheduler(e2e_page)
        frame.locator('[data-testid="scheduler-refresh"]').click()
        expand_set(frame, "scheduler", SET_NAME)
        frame.locator(f'[data-testid="schedule-edit-{sched_id}"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="visible", timeout=5_000
        )

        # Modify schedule + param + max_retries.
        frame.locator('[data-testid="scheduler-edit-schedule"]').fill("30 2 * * *")
        frame.locator('[data-testid="scheduler-edit-param"]').fill("updated = True")
        frame.locator('[data-testid="scheduler-edit-max-retries"]').fill("5")
        frame.locator('[data-testid="scheduler-edit-drop-done"]').check()
        frame.locator('[data-testid="scheduler-edit-submit"]').click()

        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        s = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert s["schedule"] == "30 2 * * *"
        assert s["param"] == "updated = True"
        assert s["max_retries"] == 5
        assert s["drop_done"] is True
        pyops_server.assert_no_unexpected_errors()

    def test_edit_modal_cancel_preserves_original_values(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Cancel on the edit modal must not alter the schedule."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": False,
                "max_retries": 1,
                "schedule": "0 1 * * *",
                "param": "original",
            },
        )
        sched_id = resp.json()["schedule"]
        api_anon.get(f"/scriptlet/schedule/deactivate?id={sched_id}")

        frame = _nav_scheduler(e2e_page)
        frame.locator('[data-testid="scheduler-refresh"]').click()
        expand_set(frame, "scheduler", SET_NAME)
        frame.locator(f'[data-testid="schedule-edit-{sched_id}"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="visible", timeout=5_000
        )

        frame.locator('[data-testid="scheduler-edit-schedule"]').fill("99 99 * * *")
        frame.locator('[data-testid="scheduler-edit-cancel"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        s = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert s["schedule"] == "0 1 * * *"
        assert s["param"] == "original"
        assert s["max_retries"] == 1
        pyops_server.assert_no_unexpected_errors()

    def test_create_with_unknown_scriptlet_shows_error(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Submitting create with a non-existent scriptlet surfaces the server error inline."""
        # Set exists, scriptlet does not.
        set_factory(SET_NAME)

        frame = _nav_scheduler(e2e_page)
        _open_create_modal(frame)
        _submit_create(
            frame,
            set=SET_NAME,
            name="does_not_exist",
            schedule="0 0 * * *",
        )
        # Modal stays open and error text shows the server detail.
        err_locator = frame.locator('[data-testid="scheduler-create-error"]')
        err_locator.wait_for(state="visible", timeout=5_000)
        # Poll until the text is populated (fetch resolves asynchronously).
        deadline = time.time() + 5
        while time.time() < deadline and not err_locator.inner_text().strip():
            time.sleep(0.1)
        err = err_locator.inner_text()
        assert err.strip(), "scheduler-create-error was never populated"
        assert frame.locator('[data-testid="scheduler-create-modal"]').is_visible()

        # No schedule persisted.
        assert api_anon.get("/scriptlet/schedule/list").json() == []

        frame.locator('[data-testid="scheduler-create-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_edit_with_invalid_cron_shows_error(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Invalid cron in the edit modal surfaces an error and keeps the modal open."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": False,
                "max_retries": 0,
                "schedule": "0 0 * * *",
                "param": "",
            },
        )
        sched_id = resp.json()["schedule"]
        api_anon.get(f"/scriptlet/schedule/deactivate?id={sched_id}")

        frame = _nav_scheduler(e2e_page)
        frame.locator('[data-testid="scheduler-refresh"]').click()
        expand_set(frame, "scheduler", SET_NAME)
        frame.locator(f'[data-testid="schedule-edit-{sched_id}"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="visible", timeout=5_000
        )

        frame.locator('[data-testid="scheduler-edit-schedule"]').fill("not a cron")
        frame.locator('[data-testid="scheduler-edit-submit"]').click()

        err_locator = frame.locator('[data-testid="scheduler-edit-error"]')
        err_locator.wait_for(state="visible", timeout=5_000)
        deadline = time.time() + 5
        while time.time() < deadline and not err_locator.inner_text().strip():
            time.sleep(0.1)
        err = err_locator.inner_text()
        assert err.strip(), "scheduler-edit-error was never populated"
        assert frame.locator('[data-testid="scheduler-edit-modal"]').is_visible()

        # Original cron is unchanged on the server.
        s = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert s["schedule"] == "0 0 * * *"

        frame.locator('[data-testid="scheduler-edit-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_delete_schedule_removes_row(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Delete button plus confirm dialog removes the schedule."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": False,
                "max_retries": 0,
                "schedule": "0 0 * * *",
                "param": "",
            },
        )
        sched_id = resp.json()["schedule"]
        api_anon.get(f"/scriptlet/schedule/deactivate?id={sched_id}")

        frame = _nav_scheduler(e2e_page)
        frame.locator('[data-testid="scheduler-refresh"]').click()
        expand_set(frame, "scheduler", SET_NAME)
        frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
            state="visible", timeout=5_000
        )

        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(f'[data-testid="schedule-delete-{sched_id}"]').click()
        frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
            state="hidden", timeout=5_000
        )

        resp = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}")
        assert resp.status_code == 404
        pyops_server.assert_no_unexpected_errors()
