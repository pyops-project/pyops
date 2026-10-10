"""Scheduler UI E2E tests.

Covers the UI workflows not exercised by test_schedule_api.py:
  - Edit-existing-schedule modal (scheduler-edit-*)
  - Delete schedule (confirm dialog + row removal)
  - Create modal with every field populated (param, max-retries, drop-done, keep-n, max-fail-n)
  - Create modal Cancel button
  - keep-n input enabled only with drop-done and a cron schedule (create and edit modal); max-fail-n always enabled
  - 422 validation errors shown as their messages
"""

import time

import pytest

from tests.e2e.conftest import expand_set, set_ace_value


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
    # Set + name are dropdowns (must already exist in inventory); schedule
    # is still a free-text input; param is an Ace editor (3.3.3).
    if fields.get("set") is not None:
        frame.locator('[data-testid="scheduler-create-set"]').select_option(fields["set"])
    if fields.get("name") is not None:
        frame.locator('[data-testid="scheduler-create-name"]').select_option(fields["name"])
    if fields.get("schedule") is not None:
        frame.locator('[data-testid="scheduler-create-schedule"]').fill(fields["schedule"])
    if fields.get("param") is not None:
        set_ace_value(frame, "scheduler-create-param", fields["param"])
    if fields.get("max_retries") is not None:
        frame.locator('[data-testid="scheduler-create-max-retries"]').fill(str(fields["max_retries"]))
    if fields.get("drop_done"):
        frame.locator('[data-testid="scheduler-create-drop-done"]').check()
    if fields.get("keep_n") is not None:
        frame.locator('[data-testid="scheduler-create-keep-n"]').fill(str(fields["keep_n"]))
    if fields.get("max_fail_n") is not None:
        frame.locator('[data-testid="scheduler-create-max-fail-n"]').fill(str(fields["max_fail_n"]))
    frame.locator('[data-testid="scheduler-create-submit"]').click()


def _first_schedule_row(frame):
    row = frame.locator('[data-testid^="schedule-row-"]').first
    row.wait_for(state="visible", timeout=5_000)
    return row


def _error_text(locator, timeout=5):
    """The text of a modal's error line once the failed request has filled it ('' if it never does)."""
    deadline = time.time() + timeout
    while time.time() < deadline and not locator.inner_text().strip():
        time.sleep(0.1)
    return locator.inner_text()


class TestSchedulerUI:
    def test_create_modal_cancel_closes_without_creating(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Cancel on the create modal closes it and creates nothing."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        frame = _nav_scheduler(e2e_page)

        _open_create_modal(frame)
        # Set/name are dropdowns now — no need to pick anything to prove Cancel
        # closes the modal without creating a schedule. Wait for the modal's
        # `shown` handler to populate the set dropdown first: that confirms
        # the open transition finished, so Bootstrap won't swallow the
        # dismiss as a mid-transition no-op.
        frame.locator('[data-testid="scheduler-create-set"] option[value="' + SET_NAME + '"]').wait_for(
            state="attached", timeout=5_000
        )
        frame.locator('[data-testid="scheduler-create-cancel"]').click()

        frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
            state="hidden", timeout=5_000
        )
        resp = api_anon.get("/scriptlet/schedule/list")
        assert resp.status_code == 200
        assert resp.json() == []
        pyops_server.assert_no_unexpected_errors()

    def test_create_with_all_fields_persists_values(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Every field on the create modal (param, max_retries, drop_done, keep_n, max_fail_n) round-trips."""
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
            keep_n=4,
            max_fail_n=7,
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
        assert s["keep_n"] == 4
        assert s["max_fail_n"] == 7

        # Deactivate so the scheduler doesn't start firing it.
        api_anon.get(f"/scriptlet/schedule/deactivate?id={s['id']}")
        pyops_server.assert_no_unexpected_errors()

    def test_edit_modal_updates_existing_schedule(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Opening Edit, changing fields, and submitting updates the schedule; a 422 shows its message first."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        # Create via API — the create flow is already covered above.
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": False,
                "max_retries": 0,
                "keep_n": 2,
                "max_fail_n": 5,
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

        # The schedule's keep_n is shown, disabled while drop_done is off.
        keep_n = frame.locator('[data-testid="scheduler-edit-keep-n"]')
        assert keep_n.input_value() == "2"
        assert keep_n.is_disabled()
        max_fail_n = frame.locator('[data-testid="scheduler-edit-max-fail-n"]')
        assert max_fail_n.input_value() == "5"

        # Modify schedule + param + max_retries. (param is an Ace editor)
        frame.locator('[data-testid="scheduler-edit-schedule"]').fill("30 2 * * *")
        set_ace_value(frame, "scheduler-edit-param", "updated = True")
        frame.locator('[data-testid="scheduler-edit-max-retries"]').fill("5")
        frame.locator('[data-testid="scheduler-edit-drop-done"]').check()
        keep_n.fill("6")
        # Out of range: the 422 shows its message and the modal stays open for the correction.
        max_fail_n.fill("101")
        frame.locator('[data-testid="scheduler-edit-submit"]').click()
        assert "less than or equal to 100" in _error_text(frame.locator('[data-testid="scheduler-edit-error"]'))
        max_fail_n.fill("9")
        frame.locator('[data-testid="scheduler-edit-submit"]').click()

        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        s = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert s["schedule"] == "30 2 * * *"
        assert s["param"] == "updated = True"
        assert s["max_retries"] == 5
        assert s["drop_done"] is True
        assert s["keep_n"] == 6
        assert s["max_fail_n"] == 9
        pyops_server.assert_no_unexpected_errors()

    def test_create_keep_n_and_max_fail_n_enablement(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """The keep-n input follows drop-done and the schedule field live; max-fail-n (default 3) is enabled for cron and service."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        frame = _nav_scheduler(e2e_page)
        _open_create_modal(frame)

        keep_n = frame.locator('[data-testid="scheduler-create-keep-n"]')
        max_fail_n = frame.locator('[data-testid="scheduler-create-max-fail-n"]')
        drop_done = frame.locator('[data-testid="scheduler-create-drop-done"]')
        schedule = frame.locator('[data-testid="scheduler-create-schedule"]')
        assert keep_n.is_disabled()
        assert max_fail_n.input_value() == "3"

        schedule.fill("0 0 * * *")
        assert keep_n.is_disabled()  # cron, but no drop_done
        assert max_fail_n.is_enabled()
        drop_done.check()
        assert keep_n.is_enabled()
        schedule.fill("")
        assert keep_n.is_disabled()  # drop_done, but a service
        assert max_fail_n.is_enabled()
        frame.locator('[data-testid="scheduler-create-schedule-preset"]').select_option("*/5 * * * *")
        assert keep_n.is_enabled()
        drop_done.uncheck()
        assert keep_n.is_disabled()

        frame.locator('[data-testid="scheduler-create-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_create_validation_error_shows_message(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """A 422 (keep_n 101; the input's max is not enforced on a scripted submit) shows its message, not "[object Object]"."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        frame = _nav_scheduler(e2e_page)
        _open_create_modal(frame)
        _submit_create(frame, set=SET_NAME, name=TASK_NAME, schedule="0 0 * * *", drop_done=True, keep_n=101)

        err = _error_text(frame.locator('[data-testid="scheduler-create-error"]'))
        assert "less than or equal to 100" in err and "[object Object]" not in err, err
        assert frame.locator('[data-testid="scheduler-create-modal"]').is_visible()
        assert api_anon.get("/scriptlet/schedule/list").json() == []

        frame.locator('[data-testid="scheduler-create-cancel"]').click()
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

    def test_create_name_dropdown_excludes_unknown_scriptlet(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """The create modal's name dropdown only offers scriptlets that
        exist in the chosen set, so an unknown name can't be scheduled
        from the UI at all (replaces the old "unknown scriptlet → server
        error" path, which the dropdown makes unreachable). A set with no
        task scriptlets yields an empty, disabled name dropdown."""
        # Set exists, but has no task scriptlets.
        set_factory(SET_NAME)

        frame = _nav_scheduler(e2e_page)
        _open_create_modal(frame)
        # Pick the set (waits for populateSets to fill the option).
        frame.locator('[data-testid="scheduler-create-set"]').select_option(SET_NAME)

        name_select = frame.locator('[data-testid="scheduler-create-name"]')
        # No option for a non-existent scriptlet exists.
        assert name_select.locator('option[value="does_not_exist"]').count() == 0
        # With no task scriptlets in the set, the dropdown is disabled and
        # carries only the placeholder.
        assert name_select.locator('option[value=""]').count() == 1
        assert name_select.is_disabled()

        frame.locator('[data-testid="scheduler-create-cancel"]').click()
        # Nothing was (or could be) created.
        assert api_anon.get("/scriptlet/schedule/list").json() == []
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
        err = _error_text(err_locator)
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

    def test_drops_checkmark_and_edit_cron_preset(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """The Drops column renders a checkmark (not true/false), and the
        edit modal's cron-preset selector fills the schedule field; changing
        the schedule (preset or typed) re-checks the keep-n enablement."""
        _prepare_set_and_task(api_anon, set_factory, commit_set)
        resp = api_anon.put(
            "/scriptlet/schedule/item",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "drop_done": True,
                "max_retries": 0,
                "schedule": "",
                "param": "",
                "active": False,
            },
        )
        sched_id = resp.json()["schedule"]

        frame = _nav_scheduler(e2e_page)
        frame.locator('[data-testid="scheduler-refresh"]').click()
        expand_set(frame, "scheduler", SET_NAME)

        # Drops cell shows a checkmark for drop_done=True (no "true"/"false").
        drops = frame.locator(f'[data-testid="schedule-drops-{sched_id}"]')
        drops.wait_for(state="visible", timeout=5_000)
        assert drops.get_attribute("data-drops") == "true"
        assert "check" in drops.inner_text()

        # Edit: pick a cron preset and confirm it fills the schedule input,
        # then persists on Update.
        frame.locator(f'[data-testid="schedule-edit-{sched_id}"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="visible", timeout=5_000
        )
        keep_n = frame.locator('[data-testid="scheduler-edit-keep-n"]')
        schedule = frame.locator('[data-testid="scheduler-edit-schedule"]')
        assert keep_n.is_disabled()  # drop_done, but a service
        frame.locator('[data-testid="scheduler-edit-schedule-preset"]').select_option("0 0 * * *")
        assert schedule.input_value() == "0 0 * * *"
        assert keep_n.is_enabled()
        schedule.fill("")
        assert keep_n.is_disabled()
        schedule.fill("0 0 * * *")
        assert keep_n.is_enabled()
        frame.locator('[data-testid="scheduler-edit-submit"]').click()
        frame.locator('[data-testid="scheduler-edit-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        s = api_anon.get(f"/scriptlet/schedule/item?id={sched_id}").json()
        assert s["schedule"] == "0 0 * * *"
        assert s["drop_done"] is True
        pyops_server.assert_no_unexpected_errors()
