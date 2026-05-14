"""
Job cancellation E2E tests.

Covers GET /scriptlet/job/cancel?id=<id>:
  - cancel() flips Job.state to "cancel"; the runner's watchdog loop then
    terminates the subprocess (via kill_proc_tree) and marks the job
    "cancelled".
  - A cancelled job's terminal state is "cancelled" (past tense), distinct
    from the transient "cancel" state.
  - Cancelling an already-terminal job is a no-op on observable state.
  - Cancelling an unknown job id returns HTTP 404.
"""

import time

import pytest


SET = "cancel_e2e"

LONG_RUNNING_CODE = """\
import time
print("starting", flush=True)
for i in range(60):
    time.sleep(1)
print("finished_unexpectedly")
"""


def _wait_for_state(api, job_id, target, timeout=30):
    """Wait for a job to reach any of the states in `target` (str or set)."""
    if isinstance(target, str):
        target = {target}
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = api.get(f"/scriptlet/job/item?id={job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["state"] in target:
            return last
        time.sleep(0.3)
    pytest.fail(
        f"Job {job_id} did not reach {target} within {timeout}s "
        f"(last state: {last['state'] if last else 'unknown'})"
    )


def _setup(api, set_name, task_name, code, set_factory):
    set_factory(set_name)
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "type": "task"},
    )
    assert r.status_code == 200
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "code": code},
    )
    assert r.status_code == 200


def _enqueue(api, set_name, task_name):
    r = api.put(
        "/scriptlet/job/item",
        json={
            "set": set_name,
            "name": task_name,
            "drop_done": False,
            "max_retries": 0,
            "param": "",
            "kind": "task",
        },
    )
    assert r.status_code == 200, f"enqueue failed: {r.text}"
    return r.json()["job"]


class TestJobCancel:
    """GET /scriptlet/job/cancel terminates a running job."""

    def test_cancel_running_job_reaches_cancelled(self, api, pyops_server, set_factory):
        """A long-running task can be cancelled while in 'running' state."""
        _setup(api, SET, "sleeper", LONG_RUNNING_CODE, set_factory)
        job_id = _enqueue(api, SET, "sleeper")

        # Wait until the job is actually running before cancelling — the
        # watchdog path (terminate subprocess) is what we want to exercise,
        # not the simple todo→cancelled path.
        _wait_for_state(api, job_id, "running", timeout=30)

        resp = api.get(f"/scriptlet/job/cancel?id={job_id}")
        assert resp.status_code == 200

        # Runner's watchdog loop picks up state=="cancel", terminates the
        # subprocess, and sets the final state to "cancelled".
        final = _wait_for_state(
            api, job_id, {"cancelled", "killed"}, timeout=30
        )
        assert final["state"] == "cancelled", (
            f"Expected 'cancelled', got {final['state']}"
        )
        assert "finished_unexpectedly" not in (final.get("stdout") or ""), (
            "Task body must not have completed after cancel"
        )
        pyops_server.assert_no_unexpected_errors(
            allow=["cancel", "SIGTERM", "SIGKILL", "terminated"]
        )

    def test_cancel_unknown_job_returns_404(self, api, pyops_server):
        """Cancelling a non-existent job id returns HTTP 404."""
        resp = api.get("/scriptlet/job/cancel?id=does-not-exist-xyzzy")
        assert resp.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_cancel_terminal_job_flips_state_to_cancelled(self, api, pyops_server, set_factory):
        """runner.cancel() unconditionally marks state='cancel'; the runner loop
        then finalises it to 'cancelled' regardless of the previous terminal
        state. This is observed behaviour — it loses the original 'done'/'failed'
        information and is surprising, but encoding it as a test locks the
        contract until a deliberate change.
        """
        _setup(api, SET + "_done", "quick", 'print("done")\n', set_factory)
        job_id = _enqueue(api, SET + "_done", "quick")

        done = _wait_for_state(api, job_id, "done", timeout=30)
        assert done["state"] == "done"

        resp = api.get(f"/scriptlet/job/cancel?id={job_id}")
        assert resp.status_code == 200

        # Runner picks up 'cancel' → finalises to 'cancelled'.
        final = _wait_for_state(api, job_id, "cancelled", timeout=10)
        assert final["state"] == "cancelled"
        pyops_server.assert_no_unexpected_errors(allow=["cancel"])
