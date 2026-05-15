"""
Complex E2E workflow tests: service + controller + views full-stack integration.

All test workflows are driven through the browser (Playwright).
Scriptlet creation uses api_anon for fast, reliable setup (same user as browser).
Every test cleans up all resources it created via the UI and verifies removal.

Covers:
  - Scriptlet visibility in Manager UI and sidebar menu
  - Service lifecycle via Scheduler and Monitor UI
  - Controller page rendering in the browser
  - Controller proxy via fetch() from the browser
  - Cleanup verification: job deletion, schedule deletion, scriptlet deletion, set removal
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET_NAME = "svc_e2e"
SVC_NAME = "counter_svc"
CTRL_NAME = "dashboard"
HEAD_VIEW = "e2e_head"
MENU_VIEW = "e2e_menu"
BODY_VIEW = "e2e_body"

ALL_SCRIPTLETS = [SVC_NAME, CTRL_NAME, HEAD_VIEW, MENU_VIEW, BODY_VIEW]

BASE_URL = "http://127.0.0.1:18321"

# ---------------------------------------------------------------------------
# Scriptlet content
# ---------------------------------------------------------------------------

SERVICE_CODE = """\
import time

_state = {"count": 0, "log": []}

while not listener.kill_now:
    msg = listener.poll()
    if msg is None:
        time.sleep(0.05)
        continue
    if msg == "TERM":
        break

    path = msg.get("path", "/")
    method = msg.get("type", "GET")
    data = msg.get("data")
    user = msg.get("user", "anonymous")

    if path == "/ping" and method == "GET":
        listener.response({"pong": True})
    elif path == "/count" and method == "GET":
        _state["count"] += 1
        listener.response({"count": _state["count"]})
    elif path == "/echo" and method == "POST":
        _state["log"].append(data)
        listener.response({"echo": data, "user": user})
    elif path == "/log" and method == "GET":
        listener.response({"log": _state["log"]})
    else:
        listener.response({"error": "not found"}, 404)
"""

CONTROLLER_CODE = f"""\
name = "E2E Dashboard"
set = "{SET_NAME}"
service = "{SVC_NAME}"
head = "{HEAD_VIEW}"
menu = "{MENU_VIEW}"
body = "{BODY_VIEW}"
debug = False
"""

HEAD_CONTENT = '<title>{{SET}} E2E Dashboard</title><meta charset="utf-8">'
MENU_CONTENT = (
    '<li class="nav-item">'
    '<span class="nav-link text-light">E2E Menu for {{SET}}::{{NAME}}</span>'
    "</li>"
)
BODY_CONTENT = (
    '<div class="container mt-4">'
    '<h1 id="e2e-heading">{{SET}} E2E Body</h1>'
    "<p>E2E test dashboard content.</p>"
    "</div>"
)

# ---------------------------------------------------------------------------
# Setup helper (API — fast scriptlet creation, anonymous user)
# ---------------------------------------------------------------------------


def _setup_full_stack(api, set_factory):
    """Create set, service, controller, and three view scriptlets via API.

    Uses the anonymous-user session so resources match the browser's owner.
    """
    set_factory(SET_NAME)

    for name, type_, code in [
        (SVC_NAME, "task", SERVICE_CODE),
        (CTRL_NAME, "controller", CONTROLLER_CODE),
        (HEAD_VIEW, "view", HEAD_CONTENT),
        (MENU_VIEW, "view", MENU_CONTENT),
        (BODY_VIEW, "view", BODY_CONTENT),
    ]:
        resp = api.put(
            "/scriptlet/inventory/item",
            json={"set": SET_NAME, "name": name, "type": type_},
        )
        assert resp.status_code == 200, f"Create {name} ({type_}) failed: {resp.text}"
        resp = api.post(
            "/scriptlet/inventory/item",
            json={"set": SET_NAME, "name": name, "code": code},
        )
        assert resp.status_code == 200, f"Save {name} failed: {resp.text}"


# ---------------------------------------------------------------------------
# UI navigation helpers
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


def _nav_manager(e2e_page):
    """Click Manager in the sidebar and return the content frame locator."""
    e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="manager-navbar"]').wait_for(state="visible", timeout=10_000)
    return frame


def _nav_sets(e2e_page):
    """Click Sets in the sidebar and return the content frame locator."""
    e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="sets-create"]').wait_for(state="visible", timeout=10_000)
    return frame


# ---------------------------------------------------------------------------
# Scheduler / Monitor UI helpers
# ---------------------------------------------------------------------------


def _create_service_schedule_via_ui(e2e_page):
    """Open the Scheduler, create a schedule with empty cron (→ service kind).

    Returns:
        tuple[str, FrameLocator]: (schedule_id, scheduler_frame)
    """
    frame = _nav_scheduler(e2e_page)
    frame.locator('[data-testid="scheduler-create"]').click()
    frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
        state="visible", timeout=5_000
    )

    frame.locator('[data-testid="scheduler-create-set"]').fill(SET_NAME)
    frame.locator('[data-testid="scheduler-create-name"]').fill(SVC_NAME)
    # Leave schedule empty → service kind at run time.

    frame.locator('[data-testid="scheduler-create-submit"]').click()
    frame.locator('[data-testid="scheduler-create-modal"]').wait_for(
        state="hidden", timeout=5_000
    )

    row = frame.locator('[data-testid^="schedule-row-"]').first
    row.wait_for(state="visible", timeout=5_000)
    sched_id = row.get_attribute("data-id")
    return sched_id, frame


def _run_schedule_once_via_ui(frame, e2e_page, sched_id):
    """Click the run button, accepting the alert dialog."""

    def _accept(dialog):
        dialog.accept()

    e2e_page.on("dialog", _accept)
    try:
        frame.locator(f'[data-testid="schedule-run-{sched_id}"]').click()
        time.sleep(0.5)
    finally:
        e2e_page.remove_listener("dialog", _accept)


def _wait_for_running_job_in_monitor(frame, timeout=30):
    """Poll the monitor until a USER job with 'running' state appears.

    Skips the always-running `internal::data` essential service (its
    job-id starts with "system_internal_data_") — every fresh pyops
    instance has one, and grabbing it would misroute the test's
    assertions to a job it didn't enqueue.

    Returns:
        str: The job's data-id.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(1)
        rows = frame.locator('[data-testid^="job-row-"]')
        for i in range(rows.count()):
            row = rows.nth(i)
            job_id = row.get_attribute("data-id") or ""
            if job_id.startswith("system_internal_data_"):
                continue
            if "running" in row.inner_text().lower():
                return job_id
    pytest.fail(f"No running USER job found in monitor within {timeout}s")


def _wait_for_terminal_job_in_monitor(frame, job_id, timeout=30):
    """Poll the monitor until the job's delete button appears (terminal state)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(1)
        if frame.locator(f'[data-testid="job-delete-{job_id}"]').is_visible():
            return
    pytest.fail(f"Job {job_id} did not reach terminal state within {timeout}s")


def _start_service_via_ui(e2e_page):
    """Create schedule → deactivate (prevent auto-start race) → run → wait.

    The schedule is deactivated immediately after creation so the scheduler
    loop cannot auto-start a second job while we manually trigger one.
    Manual run works on deactivated schedules.

    Returns:
        tuple[str, str]: (schedule_id, job_id)
    """
    sched_id, sched_frame = _create_service_schedule_via_ui(e2e_page)

    # Deactivate immediately to prevent auto-start from creating a second job.
    sched_frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]').click()
    sched_frame.locator(
        f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
    ).wait_for(state="visible", timeout=5_000)

    _run_schedule_once_via_ui(sched_frame, e2e_page, sched_id)
    monitor_frame = _nav_monitor(e2e_page)
    job_id = _wait_for_running_job_in_monitor(monitor_frame)
    return sched_id, job_id


def _start_service_via_api_then_runnow(e2e_page, api_anon):
    """Race-free counterpart to `_start_service_via_ui` for tests that need
    *exactly one* service instance (e.g. stateful service assertions).

    Creates the schedule via API with `active=False` so the scheduler loop
    cannot auto-enqueue a parallel service in the gap between create and the
    UI-driven deactivate. Then triggers Run Now via UI and waits for the
    monitor to show the running job.

    Returns:
        tuple[str, str]: (schedule_id, job_id)
    """
    resp = api_anon.put(
        "/scriptlet/schedule/item",
        json={
            "set": SET_NAME,
            "name": SVC_NAME,
            "drop_done": False,
            "max_retries": 0,
            "schedule": "",
            "param": "",
            "active": False,
        },
    )
    assert resp.status_code == 200, f"Create schedule failed: {resp.text}"
    sched_id = str(resp.json()["schedule"])
    sched_frame = _nav_scheduler(e2e_page)
    sched_frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
        state="visible", timeout=5_000
    )
    _run_schedule_once_via_ui(sched_frame, e2e_page, sched_id)
    monitor_frame = _nav_monitor(e2e_page)
    job_id = _wait_for_running_job_in_monitor(monitor_frame)
    return sched_id, job_id


# ---------------------------------------------------------------------------
# Proxy helpers (fetch from browser context)
# ---------------------------------------------------------------------------


def _proxy_get(e2e_page, path):
    """GET fetch to the controller proxy from the browser."""
    url = f"/controller/{SET_NAME}/{CTRL_NAME}/{path}"
    return e2e_page.evaluate(
        """async (url) => {
            const resp = await fetch(url);
            const data = await resp.json();
            return { status: resp.status, data: data };
        }""",
        url,
    )


def _proxy_post(e2e_page, path, payload):
    """POST fetch with JSON body to the controller proxy from the browser."""
    url = f"/controller/{SET_NAME}/{CTRL_NAME}/{path}"
    return e2e_page.evaluate(
        """async ([url, payload]) => {
            const resp = await fetch(url, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            return { status: resp.status, data: data };
        }""",
        [url, payload],
    )


# ---------------------------------------------------------------------------
# Cleanup helpers — each verifies removal through the UI
# ---------------------------------------------------------------------------


def _cleanup_job(e2e_page, job_id):
    """Cancel a job if still running, wait for terminal, delete it, verify gone."""
    monitor = _nav_monitor(e2e_page)
    monitor.locator('[data-testid="monitor-refresh"]').click()
    time.sleep(0.5)

    cancel_btn = monitor.locator(f'[data-testid="job-cancel-{job_id}"]')
    if cancel_btn.count() > 0 and cancel_btn.is_visible():
        cancel_btn.click()

    _wait_for_terminal_job_in_monitor(monitor, job_id, timeout=30)

    # Delete the terminal job (no confirm dialog).
    monitor.locator(f'[data-testid="job-delete-{job_id}"]').click()
    time.sleep(0.5)
    monitor.locator('[data-testid="monitor-refresh"]').click()
    monitor.locator(f'[data-testid="job-row-{job_id}"]').wait_for(
        state="hidden", timeout=5_000
    )


def _cleanup_schedule(e2e_page, sched_id):
    """Deactivate (if active), delete the schedule, verify row gone."""
    frame = _nav_scheduler(e2e_page)

    toggle = frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]')
    if "btn-danger" in (toggle.get_attribute("class") or ""):
        toggle.click()
        frame.locator(
            f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
        ).wait_for(state="visible", timeout=5_000)

    # Delete (uses window.confirm).
    e2e_page.once("dialog", lambda d: d.accept())
    frame.locator(f'[data-testid="schedule-delete-{sched_id}"]').click()
    frame.locator(f'[data-testid="schedule-row-{sched_id}"]').wait_for(
        state="hidden", timeout=5_000
    )
    assert frame.locator('[data-testid^="schedule-row-"]').count() == 0


def _cleanup_scriptlets(e2e_page, names=None):
    """Delete listed scriptlets (or all known) from Manager, verify rows gone."""
    if names is None:
        names = list(ALL_SCRIPTLETS)
    frame = _nav_manager(e2e_page)
    frame.locator('[data-testid="manager-refresh"]').click()
    time.sleep(0.5)

    for name in names:
        row = frame.locator(f'[data-testid="scriptlet-row-{SET_NAME}-{name}"]')
        if row.count() > 0 and row.is_visible():
            e2e_page.once("dialog", lambda d: d.accept())
            frame.locator(
                f'[data-testid="scriptlet-delete-{SET_NAME}-{name}"]'
            ).click()
            row.wait_for(state="hidden", timeout=5_000)

    # Verify none of the test scriptlets remain.
    for name in names:
        assert frame.locator(
            f'[data-testid="scriptlet-row-{SET_NAME}-{name}"]'
        ).count() == 0


def _cleanup_set(e2e_page):
    """Remove the test set from Sets page and verify row gone."""
    frame = _nav_sets(e2e_page)
    frame.locator('[data-testid="sets-refresh"]').click()
    time.sleep(0.5)

    row = frame.locator(f'[data-testid="set-row-{SET_NAME}"]')
    if row.count() > 0 and row.is_visible():
        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(f'[data-testid="set-remove-{SET_NAME}"]').click()
        # The set_remove task blocks on the bootstrap lock if a per-set venv
        # pip install is still running (runner's per-job-spawn refresh). Cold
        # devpi cache + aarch64 wheels can push that to ~30s.
        row.wait_for(state="hidden", timeout=60_000)

    assert frame.locator(f'[data-testid="set-row-{SET_NAME}"]').count() == 0


def _full_cleanup(e2e_page, job_id=None, sched_id=None, scriptlets=None):
    """Clean up all test resources via UI in the correct order, verifying each step.

    Order: deactivate schedule → cancel+delete job → delete schedule →
           delete scriptlets → remove set.
    """
    # Deactivate schedule first to prevent auto-restart during job cancel.
    if sched_id:
        frame = _nav_scheduler(e2e_page)
        toggle = frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]')
        if toggle.count() > 0 and "btn-danger" in (toggle.get_attribute("class") or ""):
            toggle.click()
            frame.locator(
                f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
            ).wait_for(state="visible", timeout=5_000)

    if job_id:
        _cleanup_job(e2e_page, job_id)
    if sched_id:
        _cleanup_schedule(e2e_page, sched_id)
    _cleanup_scriptlets(e2e_page, names=scriptlets)
    _cleanup_set(e2e_page)


# ---------------------------------------------------------------------------
# Tests: scriptlet creation and Manager UI visibility
# ---------------------------------------------------------------------------


class TestServiceControllerCreation:
    """Verify that created scriptlets are visible in the Manager UI and sidebar."""

    def test_full_stack_scriptlets_in_manager_ui(self, e2e_page, api_anon, pyops_server, set_factory):
        """All scriptlets (service, controller, views) appear as rows in the Manager."""
        _setup_full_stack(api_anon, set_factory)

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        for name in ALL_SCRIPTLETS:
            frame.locator(
                f'[data-testid="scriptlet-row-{SET_NAME}-{name}"]'
            ).wait_for(state="visible", timeout=10_000)

        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_controller_appears_in_sidebar_menu(self, e2e_page, api_anon, pyops_server, set_factory):
        """Controller scriptlet creates an entry in the sidebar menu.

        User-set sections collapse by default (see `web.expanded_sets`
        config); the link is rendered into the DOM but hidden inside the
        collapse target until the operator clicks the section header.
        We check structural presence here (state='attached') — the
        sibling click-through test exercises the expand-then-click path."""
        _setup_full_stack(api_anon, set_factory)

        e2e_page.reload()
        e2e_page.wait_for_load_state("networkidle")

        menu_link = e2e_page.locator(
            f'[data-testid="menu-link-{SET_NAME}_{CTRL_NAME}"]'
        )
        menu_link.wait_for(state="attached", timeout=10_000)
        assert "E2E Dashboard" in menu_link.inner_text()

        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_delete_scriptlet_via_manager_ui(self, e2e_page, api_anon, pyops_server, set_factory):
        """Deleting a scriptlet through the Manager UI removes its row."""
        _setup_full_stack(api_anon, set_factory)

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator(
            f'[data-testid="scriptlet-row-{SET_NAME}-{SVC_NAME}"]'
        ).wait_for(state="visible", timeout=10_000)

        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(
            f'[data-testid="scriptlet-delete-{SET_NAME}-{SVC_NAME}"]'
        ).click()

        frame.locator(
            f'[data-testid="scriptlet-row-{SET_NAME}-{SVC_NAME}"]'
        ).wait_for(state="hidden", timeout=10_000)

        # Clean up remaining scriptlets and set.
        remaining = [n for n in ALL_SCRIPTLETS if n != SVC_NAME]
        _cleanup_scriptlets(e2e_page, names=remaining)
        _cleanup_set(e2e_page)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: service lifecycle via Scheduler + Monitor UI
# ---------------------------------------------------------------------------


class TestServiceLifecycle:
    """Verify service start, running state, cancel, and schedule guards through the UI."""

    def test_service_reaches_running_via_scheduler_ui(self, e2e_page, api_anon, pyops_server, set_factory):
        """Service started via the Scheduler UI reaches 'running' in the Monitor."""
        _setup_full_stack(api_anon, set_factory)
        # _start_service_via_ui waits until the monitor shows a running job.
        sched_id, job_id = _start_service_via_ui(e2e_page)
        assert job_id, "No running job found after starting service via UI"

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_service_appears_in_monitor(self, e2e_page, api_anon, pyops_server, set_factory):
        """Running service job has a visible row in the Monitor table."""
        _setup_full_stack(api_anon, set_factory)
        sched_id, job_id = _start_service_via_ui(e2e_page)

        monitor_frame = _nav_monitor(e2e_page)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        assert monitor_frame.locator(f'[data-testid="job-row-{job_id}"]').is_visible()

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_cancel_service_via_monitor_ui(self, e2e_page, api_anon, pyops_server, set_factory):
        """Clicking Cancel in the Monitor stops the service."""
        _setup_full_stack(api_anon, set_factory)
        sched_id, job_id = _start_service_via_ui(e2e_page)
        # Schedule is already deactivated by _start_service_via_ui.

        # Cancel via Monitor.
        monitor_frame = _nav_monitor(e2e_page)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        monitor_frame.locator(f'[data-testid="job-cancel-{job_id}"]').click()
        _wait_for_terminal_job_in_monitor(monitor_frame, job_id, timeout=30)

        row_text = monitor_frame.locator(
            f'[data-testid="job-row-{job_id}"]'
        ).inner_text().lower()
        assert "cancelled" in row_text or "done" in row_text, (
            f"Expected cancelled or done, got: {row_text}"
        )

        # Delete the terminal job and verify it disappears.
        monitor_frame.locator(f'[data-testid="job-delete-{job_id}"]').click()
        time.sleep(0.5)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        monitor_frame.locator(f'[data-testid="job-row-{job_id}"]').wait_for(
            state="hidden", timeout=5_000
        )

        _cleanup_schedule(e2e_page, sched_id)
        _cleanup_scriptlets(e2e_page)
        _cleanup_set(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_schedule_auto_starts_service_in_monitor(self, e2e_page, api_anon, pyops_server, set_factory):
        """Active schedule with no cron auto-starts the service (no manual Run click)."""
        _setup_full_stack(api_anon, set_factory)

        sched_id, _sched_frame = _create_service_schedule_via_ui(e2e_page)

        monitor_frame = _nav_monitor(e2e_page)
        job_id = _wait_for_running_job_in_monitor(monitor_frame, timeout=15)

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_deactivated_schedule_does_not_restart(self, e2e_page, api_anon, pyops_server, set_factory):
        """After deactivation, the schedule does not restart a cancelled service."""
        _setup_full_stack(api_anon, set_factory)

        sched_id, _sched_frame = _create_service_schedule_via_ui(e2e_page)
        monitor_frame = _nav_monitor(e2e_page)
        job_id = _wait_for_running_job_in_monitor(monitor_frame, timeout=15)

        # Deactivate.
        sched_frame = _nav_scheduler(e2e_page)
        sched_frame.locator(f'[data-testid="schedule-toggle-{sched_id}"]').click()
        sched_frame.locator(
            f'[data-testid="schedule-toggle-{sched_id}"].btn-success'
        ).wait_for(state="visible", timeout=5_000)

        time.sleep(1)

        # Cancel.
        monitor_frame = _nav_monitor(e2e_page)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        monitor_frame.locator(f'[data-testid="job-cancel-{job_id}"]').click()
        _wait_for_terminal_job_in_monitor(monitor_frame, job_id, timeout=30)

        # Snapshot current job IDs.
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        known_rows = monitor_frame.locator('[data-testid^="job-row-"]')
        known_ids = set()
        for i in range(known_rows.count()):
            known_ids.add(known_rows.nth(i).get_attribute("data-id"))

        # Wait and verify no new running job.
        time.sleep(4)
        monitor_frame.locator('[data-testid="monitor-refresh"]').click()
        time.sleep(0.5)
        rows = monitor_frame.locator('[data-testid^="job-row-"]')
        for i in range(rows.count()):
            row = rows.nth(i)
            if row.get_attribute("data-id") in known_ids:
                continue
            text = row.inner_text().lower()
            assert "running" not in text and "loading" not in text, (
                f"Service restarted despite deactivated schedule: {text}"
            )

        # Clean up job, schedule, scriptlets, set.
        _cleanup_job(e2e_page, job_id)
        _cleanup_schedule(e2e_page, sched_id)
        _cleanup_scriptlets(e2e_page)
        _cleanup_set(e2e_page)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: controller page rendering in the browser
# ---------------------------------------------------------------------------


class TestControllerRendering:
    """Navigate the browser to the controller page and verify DOM content."""

    def _go_to_controller(self, e2e_page):
        """Navigate the browser directly to the controller URL."""
        e2e_page.goto(f"{BASE_URL}/controller/{SET_NAME}/{CTRL_NAME}")
        e2e_page.wait_for_load_state("networkidle")

    def _go_back_to_app(self, e2e_page):
        """Return to the main app page (needed after direct controller navigation)."""
        e2e_page.goto(BASE_URL)
        e2e_page.wait_for_load_state("networkidle")

    def test_controller_page_loads_in_browser(self, e2e_page, api_anon, pyops_server, set_factory):
        """Controller page renders valid HTML with the expected heading."""
        _setup_full_stack(api_anon, set_factory)
        self._go_to_controller(e2e_page)

        assert e2e_page.locator("#e2e-heading").is_visible()
        html = e2e_page.content()
        assert "<html" in html.lower()
        assert "<body" in html.lower()

        self._go_back_to_app(e2e_page)
        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_controller_embeds_view_content(self, e2e_page, api_anon, pyops_server, set_factory):
        """All three views (head, menu, body) are rendered in the controller page."""
        _setup_full_stack(api_anon, set_factory)
        self._go_to_controller(e2e_page)

        html = e2e_page.content()
        assert "E2E Dashboard" in html
        assert "E2E Body" in html
        assert "E2E Menu" in html

        self._go_back_to_app(e2e_page)
        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_controller_template_vars_substituted(self, e2e_page, api_anon, pyops_server, set_factory):
        """{{SET}} and {{NAME}} placeholders are replaced with actual values."""
        _setup_full_stack(api_anon, set_factory)
        self._go_to_controller(e2e_page)

        html = e2e_page.content()
        assert "{{SET}}" not in html
        assert "{{NAME}}" not in html
        assert SET_NAME in html

        self._go_back_to_app(e2e_page)
        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_controller_display_name_in_navbar(self, e2e_page, api_anon, pyops_server, set_factory):
        """Controller navbar shows the display name from the controller code."""
        _setup_full_stack(api_anon, set_factory)
        self._go_to_controller(e2e_page)

        navbar = e2e_page.locator("nav.navbar")
        assert "E2E Dashboard" in navbar.inner_text()

        self._go_back_to_app(e2e_page)
        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_controller_via_sidebar_menu_click(self, e2e_page, api_anon, pyops_server, set_factory):
        """Clicking the controller entry in the sidebar loads the page in the iframe."""
        _setup_full_stack(api_anon, set_factory)

        e2e_page.reload()
        e2e_page.wait_for_load_state("networkidle")

        # User-set sections start collapsed (see `web.expanded_sets` config) —
        # click the section toggle so its child links are reachable.
        e2e_page.locator(f'[data-testid="menu-toggle-{SET_NAME}"]').click()

        menu_link = e2e_page.locator(
            f'[data-testid="menu-link-{SET_NAME}_{CTRL_NAME}"]'
        )
        menu_link.wait_for(state="visible", timeout=10_000)
        menu_link.click()

        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        frame.locator("#e2e-heading").wait_for(state="visible", timeout=10_000)
        assert "E2E Body" in frame.locator("#e2e-heading").inner_text()

        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: controller proxy via fetch() from the browser
# ---------------------------------------------------------------------------


class TestControllerProxy:
    """HTTP→ZMQ→service forwarding tested via browser fetch() calls."""

    def _setup_running_service(self, e2e_page, api_anon, set_factory):
        """Create scriptlets (API) and start service.

        Uses the race-free API-then-runnow path because the proxy assertions
        require *exactly one* service instance — the UI-only flow has a
        narrow but real window (between schedule create-with-active=True
        and the subsequent UI deactivate) in which the scheduler can
        auto-enqueue a second service, leading to non-deterministic
        find-by-id behavior across requests.
        """
        _setup_full_stack(api_anon, set_factory)
        return _start_service_via_api_then_runnow(e2e_page, api_anon)

    def test_proxy_get_ping(self, e2e_page, api_anon, pyops_server, set_factory):
        """GET proxy to /ping returns {pong: true} from the service."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        result = _proxy_get(e2e_page, "ping")
        assert result["status"] == 200
        assert result["data"]["pong"] is True

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_post_echo(self, e2e_page, api_anon, pyops_server, set_factory):
        """POST proxy to /echo echoes the JSON body."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        payload = {"message": "hello e2e", "value": 42}
        result = _proxy_post(e2e_page, "echo", payload)
        assert result["status"] == 200
        assert result["data"]["echo"] == payload
        assert "user" in result["data"]

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_stateful_counter(self, e2e_page, api_anon, pyops_server, set_factory):
        """Service maintains state: /count increments on each request."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        counts = []
        for _ in range(5):
            result = _proxy_get(e2e_page, "count")
            assert result["status"] == 200
            counts.append(result["data"]["count"])

        assert counts == [1, 2, 3, 4, 5], f"Counter not incrementing: {counts}"

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_post_log_then_get_log(self, e2e_page, api_anon, pyops_server, set_factory):
        """POSTed messages are stored in service state and retrievable via GET."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        messages = [{"idx": i, "text": f"msg{i}"} for i in range(3)]
        for msg in messages:
            _proxy_post(e2e_page, "echo", msg)

        result = _proxy_get(e2e_page, "log")
        assert result["status"] == 200
        log = result["data"]["log"]
        assert len(log) == 3
        assert log[0] == messages[0]
        assert log[2] == messages[2]

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_unknown_path_returns_error(self, e2e_page, api_anon, pyops_server, set_factory):
        """Requests to unmapped paths return 404 from the service."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        result = _proxy_get(e2e_page, "nonexistent")
        assert result["status"] == 404
        detail = result["data"].get("detail", {})
        assert detail == {"error": "not found"}

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_without_running_service_returns_error(self, e2e_page, api_anon, pyops_server, set_factory):
        """Proxy request with no running service returns HTTP 503 + error dict.

        503 is the right status for "service not yet running" (transient,
        client should retry) — the proxy returns it without dumping a
        traceback, since this is expected during warmup."""
        _setup_full_stack(api_anon, set_factory)

        result = _proxy_get(e2e_page, "ping")
        assert result["status"] == 503
        assert "error" in result["data"]

        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_with_debug_controller_routes_to_draft_job(self, e2e_page, api_anon, pyops_server, set_factory):
        """Regression: a controller flagged `debug = True` (saved as a
        draft) plus the same scriptlet started as `kind="draft"` from the
        Manager preview flow → the proxy must bind to the draft job.

        Before the preview-routing fix the proxy only looked at
        `kind="debug"` jobs in debug mode, so a draft started for preview
        was never reachable.
        """
        _setup_full_stack(api_anon, set_factory)

        # Save a controller DRAFT that flips debug=True. The committed
        # controller still has debug=False; the proxy reads draft code on
        # every request via get_controller_code(debug=True), so the flip
        # takes effect without a commit.
        controller_draft = CONTROLLER_CODE.replace("debug = False", "debug = True")
        r = api_anon.post(
            "/scriptlet/inventory/draft",
            json={"set": SET_NAME, "name": CTRL_NAME, "code": controller_draft},
        )
        assert r.status_code == 200, f"draft controller failed: {r.text}"

        # Save a service DRAFT whose /ping response differs from the
        # committed code so we can prove which one the proxy hit.
        service_draft = SERVICE_CODE.replace('"pong": True', '"pong": "from-draft"')
        r = api_anon.post(
            "/scriptlet/inventory/draft",
            json={"set": SET_NAME, "name": SVC_NAME, "code": service_draft},
        )
        assert r.status_code == 200, f"draft service failed: {r.text}"

        # Enqueue the service as a draft job — kind="draft" runs the
        # unstaged code via run_draft_job (which gives it a ServiceListener
        # like a real service does).
        r = api_anon.put(
            "/scriptlet/job/item",
            json={"set": SET_NAME, "name": SVC_NAME, "kind": "draft",
                  "drop_done": False, "max_retries": 0, "param": ""},
        )
        assert r.status_code == 200, f"enqueue draft job failed: {r.text}"
        job_id = r.json()["job"]

        # Wait for the draft job to reach 'running'.
        deadline = time.time() + 30
        while time.time() < deadline:
            j = api_anon.get(f"/scriptlet/job/item?id={job_id}").json()
            if j.get("state") == "running":
                break
            time.sleep(0.3)
        else:
            pytest.fail(f"draft job {job_id} did not reach running in time")

        # Proxy through the browser. The controller draft (debug=True)
        # tells the proxy to seek a debug/draft preview job, so we land
        # on the draft service we just started, not the (absent) committed one.
        result = _proxy_get(e2e_page, "ping")
        assert result["status"] == 200
        assert result["data"]["pong"] == "from-draft", (
            f"proxy did not reach the draft service: {result}"
        )

        # Cancel the draft job. (UI cancel path is tested elsewhere; here
        # we go via API to keep the test focused on the routing assertion.)
        api_anon.get(f"/scriptlet/job/cancel?id={job_id}")
        deadline = time.time() + 15
        while time.time() < deadline:
            j = api_anon.get(f"/scriptlet/job/item?id={job_id}").json()
            if j.get("state") in {"cancelled", "removed", None}:
                break
            time.sleep(0.3)

        _full_cleanup(e2e_page)
        pyops_server.assert_no_unexpected_errors()

    def test_proxy_after_cancel_returns_error(self, e2e_page, api_anon, pyops_server, set_factory):
        """After cancelling the service via Monitor UI, proxy returns an error."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)
        # Schedule is already deactivated by _start_service_via_ui.

        result = _proxy_get(e2e_page, "ping")
        assert result["data"]["pong"] is True

        # Cancel every service job that is still live in the monitor —
        # the scheduler may have auto-enqueued a second instance during the
        # active→inactive transition in _start_service_via_ui. Skip the
        # always-running pyops-internal data service — cancelling it just
        # gets re-enqueued by the essential-services iteration, making this
        # loop never terminate.
        monitor_frame = _nav_monitor(e2e_page)
        deadline = time.time() + 30
        while time.time() < deadline:
            monitor_frame.locator('[data-testid="monitor-refresh"]').click()
            time.sleep(0.5)
            cancel_btns = monitor_frame.locator('[data-testid^="job-cancel-"]')
            user_cancel_idx = []
            for i in range(cancel_btns.count()):
                tid = cancel_btns.nth(i).get_attribute("data-testid") or ""
                if tid.startswith("job-cancel-system_internal_data_"):
                    continue
                user_cancel_idx.append(i)
            if not user_cancel_idx:
                break
            for i in user_cancel_idx:
                cancel_btns.nth(i).click()
            time.sleep(1)
        else:
            pytest.fail("Service jobs never reached terminal state within 30s")

        result = _proxy_get(e2e_page, "ping")
        assert "error" in result["data"], f"Expected error, got: {result}"

        # Delete job, schedule, scriptlets, set.
        _cleanup_job(e2e_page, job_id)
        _cleanup_schedule(e2e_page, sched_id)
        _cleanup_scriptlets(e2e_page)
        _cleanup_set(e2e_page)
        pyops_server.assert_no_unexpected_errors(allow=["No running service"])

    def test_multiple_sequential_proxy_requests(self, e2e_page, api_anon, pyops_server, set_factory):
        """Ten sequential proxy requests all succeed without errors."""
        sched_id, job_id = self._setup_running_service(e2e_page, api_anon, set_factory)

        for i in range(10):
            result = _proxy_get(e2e_page, "ping")
            assert result["status"] == 200
            assert result["data"]["pong"] is True, f"Request {i} failed: {result}"

        _full_cleanup(e2e_page, job_id=job_id, sched_id=sched_id)
        pyops_server.assert_no_unexpected_errors()
