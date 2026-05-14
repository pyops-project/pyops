"""
Job retry E2E tests.

Covers the max_retries field on task jobs (execute_job_code in
app/job_runner.py). With max_retries=N a failing task is retried up to N
additional times (total N+1 attempts) with a 60-second sleep between each.

Tests:
  - max_retries=0: a failing task immediately reaches "failed" (no retry).
  - max_retries=1: a task that fails once retries and succeeds on attempt 2.

NOTE: test_task_retries_and_eventually_succeeds waits up to 150 s because
there is a hard-coded 60-second sleep between retry attempts in
execute_job_code. This is expected and documented behaviour.
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET_ZERO = "retry_zero_e2e"
SET_SUCC = "retry_succ_e2e"

# Task code that uses the `workspace` global to persist a per-job attempt
# counter across retry iterations. The workspace directory lives for the
# lifetime of the job subprocess, so the file survives the 60-second sleep.
RETRY_THEN_SUCCEED_CODE = """\
import os
counter_file = os.path.join(workspace, "attempt.txt")
n = 0
if os.path.exists(counter_file):
    n = int(open(counter_file).read())
n += 1
open(counter_file, "w").write(str(n))
if n == 1:
    raise RuntimeError(f"planned_failure_attempt_{n}")
print(f"succeeded_on_attempt={n}")
"""


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


def _enqueue(api, set_name, task_name, max_retries=0):
    r = api.put(
        "/scriptlet/job/item",
        json={
            "set": set_name,
            "name": task_name,
            "drop_done": False,
            "max_retries": max_retries,
            "param": "",
            "kind": "task",
        },
    )
    assert r.status_code == 200, f"enqueue failed: {r.text}"
    return r.json()["job"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJobRetries:
    def test_no_retry_on_zero_max_retries(self, api, pyops_server, set_factory):
        """With max_retries=0 a failing task reaches 'failed' without retrying."""
        _setup(api, SET_ZERO, "always_fail", 'raise RuntimeError("fail_immediately")\n', set_factory)

        job_id = _enqueue(api, SET_ZERO, "always_fail", max_retries=0)
        job = _wait_for_job(api, job_id, timeout=30)

        assert job["state"] == "failed"
        pyops_server.assert_no_unexpected_errors(allow=["fail_immediately"])

    def test_task_retries_and_eventually_succeeds(self, api, pyops_server, set_factory):
        """With max_retries=1, a task that fails on attempt 1 retries and succeeds on attempt 2.

        The retry sleep is 60 s (hard-coded in execute_job_code), so this
        test uses a 150-second timeout to allow for startup and execution
        overhead on top of the mandatory wait.
        """
        _setup(api, SET_SUCC, "retry_task", RETRY_THEN_SUCCEED_CODE, set_factory)

        job_id = _enqueue(api, SET_SUCC, "retry_task", max_retries=1)
        # 150 s: 60 s retry sleep + startup/execution overhead on both sides.
        job = _wait_for_job(api, job_id, timeout=150)

        assert job["state"] == "done", (
            f"Expected done after retry, got {job['state']}: {job.get('detail', '')}"
        )
        assert "succeeded_on_attempt=2" in job["stdout"]
        pyops_server.assert_no_unexpected_errors(allow=["planned_failure_attempt"])
