"""
Cron-schedule workflow E2E tests — driven through the Scheduler and Monitor UI.

Workflow under test:
  1. Create a task scriptlet via API (fast, reliable).
  2. Create a cron schedule through the Scheduler page UI.
  3. Manually trigger the schedule three times via the UI run button.
  4. Navigate to the Monitor and verify all three jobs completed.
  5. Disable the schedule through the Scheduler UI.
  6. Verify the scheduler reflects the disabled state and no new jobs are triggered.
  7. Delete the schedule through the Scheduler UI.
  8. Verify the schedule row is gone.
"""

import time

import pytest

from tests.e2e.conftest import expand_set


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRON_SET = "cron_e2e"
CRON_TASK = "cron_task"
CRON_EXPR = "* * * * *"  # every minute — used to test scheduling, not wall-clock timing

TASK_CODE = 'print("cron task executed")\n'


# ---------------------------------------------------------------------------
# UI navigation helpers (module-level so they work without self)
# ---------------------------------------------------------------------------


def _nav_scheduler(e2e_page):
    """Click Scheduler in the sidebar and return the content frame locator."""
    e2e_page.locator('[data-testid="menu-link-scriptlet_scheduler"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="scheduler-navbar"]').wait_for(state="visible", timeout=10_000)
    return frame


def _nav_monitor(e2e_page):
    """Click Monitor in the sidebar and return the content frame locator."""
    e2e_page.locator('[data-testid="menu-link-scriptlet_monitor"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="monitor-navbar"]').wait_for(state="visible", timeout=10_000)
    return frame


def _create_cron_schedule_via_ui(frame, e2e_page, set_name, task_name, cron_expr):
    """Open the scheduler Create modal, fill the form and submit.

    Returns:
        str: The schedule row's data-id (schedule ID as string).
    """
    frame.locator('[data-testid="scheduler-create"]').click()
    frame.locator('[data-testid="scheduler-create-modal"]').wait_for(state="visible", timeout=5_000)

    frame.locator('[data-testid="scheduler-create-set"]').fill(set_name)
    frame.locator('[data-testid="scheduler-create-name"]').fill(task_name)
    frame.locator('[data-testid="scheduler-create-schedule"]').fill(cron_expr)

    frame.locator('[data-testid="scheduler-create-submit"]').click()
    frame.locator('[data-testid="scheduler-create-modal"]').wait_for(state="hidden", timeout=5_000)

    # Per-set tables are collapsed by default — expand this set, then wait
    # for the new row to appear and read its ID.
    expand_set(frame, "scheduler", set_name)
    row = frame.locator('[data-testid^="schedule-row-"]').first
    row.wait_for(state="visible", timeout=5_000)
    return row.get_attribute("data-id")


def _run_schedule_n_times(frame, e2e_page, sched_id, n):
    """Click the run button n times, dismissing the alert after each click."""

    def _accept(dialog):
        dialog.accept()

    e2e_page.on("dialog", _accept)
    try:
        for _ in range(n):
            frame.locator(f'[data-testid="schedule-run-{sched_id}"]').click()
            time.sleep(0.4)  # small gap so alerts don't collide
    finally:
        e2e_page.remove_listener("dialog", _accept)


def _wait_for_n_terminal_jobs(frame, n, timeout=60):
    """Poll the monitor until at least n jobs show a delete button (terminal state)."""
    deadline = time.time() + timeout
    count = 0
    while time.time() < deadline:
        frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(1)
        count = frame.locator('[data-testid^="job-delete-"]').count()
        if count >= n:
            return count
    pytest.fail(f"Expected {n} terminal jobs in monitor within {timeout}s, got {count}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCronScheduleWorkflowUI:
    """Full lifecycle of a cron-scheduled task, exercised through the browser UI."""

    def _setup_task(self, api_anon, set_factory, commit_set):
        """Create set and task scriptlet via API (anonymous user)."""
        set_factory(CRON_SET)
        resp = api_anon.put(
            "/scriptlet/inventory/item",
            json={"set": CRON_SET, "name": CRON_TASK, "type": "task"},
        )
        assert resp.status_code == 200
        resp = api_anon.post(
            "/scriptlet/inventory/draft",
            json={"set": CRON_SET, "name": CRON_TASK, "code": TASK_CODE},
        )
        assert resp.status_code == 200
        commit_set(CRON_SET, f"add {CRON_TASK}")

    def test_create_schedule_via_scheduler_ui(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Schedule created via the Scheduler UI appears as a row in the table."""
        self._setup_task(api_anon, set_factory, commit_set)

        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        assert sched_id, "Schedule row did not appear or has no data-id"

        # The set is now the collapsible group header (its own column was
        # dropped); the task name + cron live in the row.
        header_text = frame.locator(
            f'[data-testid="scheduler-set-toggle-{CRON_SET}"]'
        ).inner_text()
        assert CRON_SET in header_text
        row = frame.locator(f'[data-testid="schedule-row-{sched_id}"]')
        row_text = row.inner_text()
        assert CRON_TASK in row_text
        assert CRON_EXPR in row_text

        pyops_server.assert_no_unexpected_errors()

    def test_run_three_times_and_verify_in_monitor(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Run three times produces three completed jobs in the Monitor."""
        self._setup_task(api_anon, set_factory, commit_set)

        # Create schedule via UI (browser → owner = anonymous).
        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        # Manually trigger the task three times.
        _run_schedule_n_times(frame, e2e_page, sched_id, 3)

        # Switch to the monitor and wait for all three jobs to finish.
        monitor_frame = _nav_monitor(e2e_page)
        done_count = _wait_for_n_terminal_jobs(monitor_frame, 3, timeout=60)
        assert done_count >= 3, f"Expected ≥3 completed jobs, got {done_count}"

        pyops_server.assert_no_unexpected_errors()

    def test_disable_schedule_via_ui_changes_toggle_state(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking the toggle button on an active schedule marks it inactive (btn-success)."""
        self._setup_task(api_anon, set_factory, commit_set)

        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        toggle = frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]')

        # Active state: toggle button has btn-danger class.
        assert "btn-danger" in (toggle.get_attribute("class") or ""), (
            "Expected active schedule to have btn-danger on toggle"
        )

        # Click to deactivate.
        toggle.click()

        # Inactive state: toggle button should change to btn-success.
        frame.locator(
            f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
        ).wait_for(state="visible", timeout=5_000)

        pyops_server.assert_no_unexpected_errors()

    def test_disabled_schedule_does_not_trigger_new_jobs(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """After disabling the schedule, the scheduler loop does not enqueue new jobs."""
        self._setup_task(api_anon, set_factory, commit_set)

        # Create schedule via UI and then immediately disable it.
        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]').click()
        frame.locator(
            f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
        ).wait_for(state="visible", timeout=5_000)

        # Navigate to the monitor and take a baseline job count.
        monitor_frame = _nav_monitor(e2e_page)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(1)
        baseline = monitor_frame.locator('[data-testid^="job-row-"]').count()

        # Wait several scheduler loop cycles (loop runs every ~0.5 s).
        time.sleep(4)

        # Verify the job count is unchanged — no new jobs enqueued.
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        final = monitor_frame.locator('[data-testid^="job-row-"]').count()

        assert final == baseline, (
            f"Job count changed from {baseline} to {final} despite disabled schedule"
        )

        pyops_server.assert_no_unexpected_errors()

    def test_delete_schedule_via_ui_removes_row(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Deleting a schedule via the UI removes its row from the scheduler table."""
        self._setup_task(api_anon, set_factory, commit_set)

        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        # The delete function calls window.confirm — accept it.
        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(f'[data-testid="schedule-delete-{sched_id}"]').click()

        # Row should disappear after deletion + refresh.
        frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
            state="hidden", timeout=5_000
        )

        # Verify the schedules table is now empty.
        assert frame.locator('[data-testid^="schedule-row-"]').count() == 0

        pyops_server.assert_no_unexpected_errors()

    def test_full_cron_lifecycle(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """End-to-end: create → run 3× → verify monitor → disable → no new jobs → delete."""
        self._setup_task(api_anon, set_factory, commit_set)

        # 1. Create schedule.
        frame = _nav_scheduler(e2e_page)
        sched_id = _create_cron_schedule_via_ui(frame, e2e_page, CRON_SET, CRON_TASK, CRON_EXPR)

        # 2. Run three times.
        _run_schedule_n_times(frame, e2e_page, sched_id, 3)

        # 3. Verify three completed jobs in monitor.
        monitor_frame = _nav_monitor(e2e_page)
        _wait_for_n_terminal_jobs(monitor_frame, 3, timeout=60)

        # 4. Disable schedule.
        frame = _nav_scheduler(e2e_page)
        expand_set(frame, "scheduler", CRON_SET)
        frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]').click()
        frame.locator(
            f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
        ).wait_for(state="visible", timeout=5_000)

        # 5. Verify no new jobs appear.
        monitor_frame = _nav_monitor(e2e_page)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(1)
        count_before = monitor_frame.locator('[data-testid^="job-row-"]').count()

        time.sleep(4)  # Several scheduler loop iterations.

        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        count_after = monitor_frame.locator('[data-testid^="job-row-"]').count()
        assert count_after == count_before, (
            f"New jobs appeared despite disabled schedule ({count_before} → {count_after})"
        )

        # 6. Delete schedule.
        frame = _nav_scheduler(e2e_page)
        expand_set(frame, "scheduler", CRON_SET)
        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(f'[data-testid="schedule-delete-{sched_id}"]').click()
        frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
            state="hidden", timeout=5_000
        )
        assert frame.locator('[data-testid^="schedule-row-"]').count() == 0

        pyops_server.assert_no_unexpected_errors()
