"""Browser pages show free text from the server as text.

The Monitor (job ids — they carry the X-User owner —, details, params),
the Scheduler (schedule params) and the menu's draft-error list put that
text into the page through the shared `/files/js/pyops-html.js` helper; the
Monitor and the Scheduler copy a param from memory, never from a hidden
table cell.

Every injected value carries `data-injected` markup and an `onerror` that
sets `window.__pyopsInjected`; a page renders it correctly when neither an
element with that attribute exists nor the handler ran.
"""

import time

import pytest

from tests.e2e.conftest import expand_set


SET_NAME = "esc_e2e"
TASK_NAME = "esc_task"

INJECT = "<img src=x data-injected onerror=\"window.__pyopsInjected=1\"><b data-injected>b</b>"

# A Python param (it is exec'd by the job) holding markup, quotes, an entity
# and comparisons; a page that parses it as HTML loses most of it.
PARAM = (
    f"x = '{INJECT}'\n"
    "y = 1 if 2<3 and 3>2 else \"&lt;\"\n"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_task(api, set_factory, commit_set):
    set_factory(SET_NAME)
    r = api.put("/scriptlet/inventory/item", json={"set": SET_NAME, "name": TASK_NAME, "type": "task"})
    assert r.status_code == 200, r.text
    r = api.post("/scriptlet/inventory/draft", json={"set": SET_NAME, "name": TASK_NAME, "code": 'print("ok")\n'})
    assert r.status_code == 200, r.text
    commit_set(SET_NAME, f"add {TASK_NAME}")


def _wait_for_job(api, job_id, states, timeout=60):
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        r = api.get("/scriptlet/job/item", params={"id": job_id})
        assert r.status_code == 200, r.text
        job = r.json()
        if job["state"] in states:
            return job
        time.sleep(0.3)
    pytest.fail(f"job {job_id} never reached {states} (last: {job.get('state')})")


def _nav(e2e_page, link, ready):
    """Open a built-in page in the content frame; returns (frame_locator, frame)."""
    e2e_page.locator(f'[data-testid="menu-link-{link}"]').click()
    frame_locator = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame_locator.locator(f'[data-testid="{ready}"]').wait_for(state="visible", timeout=20_000)
    return frame_locator, e2e_page.frame(name="frame")


def _assert_nothing_injected(frame):
    """No element was created from data and no handler carried by data ran."""
    assert frame.evaluate("() => document.querySelectorAll('[data-injected]').length") == 0
    assert frame.evaluate("() => window.__pyopsInjected === undefined")


def _capture_clipboard(frame):
    frame.evaluate("""() => {
        window.__copied = null;
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: (t) => { window.__copied = t; return Promise.resolve(); } },
        });
    }""")


def _copied(frame, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = frame.evaluate("() => window.__copied")
        if value is not None:
            return value
        time.sleep(0.1)
    pytest.fail("nothing was copied to the clipboard")


def _wait_until(predicate, what, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    pytest.fail(f"timed out waiting for {what}")


# ---------------------------------------------------------------------------
# Free text through the real API
# ---------------------------------------------------------------------------

class TestFreeTextRendersAsText:
    def test_monitor_shows_job_param_and_owner_as_text(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """A job whose param carries markup and whose owner is an email
        address (its `.` and `@` end up in the job id) renders as text; copy
        yields the exact param and delete removes exactly that job."""
        _create_task(api_anon, set_factory, commit_set)
        owner = "ops.user@example.com"
        r = api_anon.put(
            "/scriptlet/job/item",
            headers={"X-User": owner},
            json={"set": SET_NAME, "name": TASK_NAME, "drop_done": False, "param": PARAM, "kind": "task"},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job"]
        assert job_id.startswith(owner + "_")
        _wait_for_job(api_anon, job_id, {"done"})

        frame_locator, frame = _nav(e2e_page, "scriptlet_monitor", "monitor-navbar")
        expand_set(frame_locator, "monitor", SET_NAME)
        row = frame_locator.get_by_test_id(f"job-row-{job_id}")
        row.wait_for(state="visible", timeout=10_000)
        assert row.locator("th").inner_text() == job_id
        _assert_nothing_injected(frame)

        _capture_clipboard(frame)
        frame_locator.get_by_test_id(f"job-copy-{job_id}").click()
        assert _copied(frame) == PARAM

        frame_locator.get_by_test_id(f"job-delete-{job_id}").click()
        _wait_until(
            lambda: all(j["id"] != job_id for j in api_anon.get("/scriptlet/job/list").json()),
            f"job {job_id} to be deleted",
            timeout=10,
        )
        pyops_server.assert_no_unexpected_errors()

    def test_scheduler_shows_schedule_param_as_text(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """A schedule param with markup is not parsed into the page and copy
        yields it verbatim."""
        _create_task(api_anon, set_factory, commit_set)
        r = api_anon.put(
            "/scriptlet/schedule/item",
            json={"set": SET_NAME, "name": TASK_NAME, "drop_done": False, "max_retries": 0,
                  "schedule": "0 0 1 1 *", "param": PARAM},
        )
        assert r.status_code == 200, r.text
        sched_id = r.json()["schedule"]

        frame_locator, frame = _nav(e2e_page, "scriptlet_scheduler", "scheduler-navbar")
        expand_set(frame_locator, "scheduler", SET_NAME)
        frame_locator.get_by_test_id(f"schedule-row-{sched_id}").wait_for(state="visible", timeout=10_000)
        _assert_nothing_injected(frame)

        _capture_clipboard(frame)
        frame_locator.get_by_test_id(f"schedule-copy-{sched_id}").click()
        assert _copied(frame) == PARAM
        pyops_server.assert_no_unexpected_errors()

    def test_menu_shows_draft_error_as_text(self, e2e_page, api_anon, pyops_server, set_factory):
        """A controller draft error quoting `<string>` is shown in full in
        the menu's failure list."""
        set_factory(SET_NAME)
        r = api_anon.put("/scriptlet/inventory/item", json={"set": SET_NAME, "name": "broken", "type": "controller"})
        assert r.status_code == 200, r.text
        r = api_anon.post("/scriptlet/inventory/draft", json={"set": SET_NAME, "name": "broken", "code": "name = (\n"})
        assert r.status_code == 200, r.text

        e2e_page.reload()
        e2e_page.wait_for_load_state("networkidle")
        failures = e2e_page.locator(".pyops-failures")
        failures.wait_for(state="visible", timeout=10_000)
        assert f"{SET_NAME}::broken:" in failures.inner_text()
        assert "(<string>, line 1)" in failures.inner_text()
        _assert_nothing_injected(e2e_page)
        pyops_server.assert_no_unexpected_errors()
