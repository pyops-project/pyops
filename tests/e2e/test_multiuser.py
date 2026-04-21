"""
Multi-user job visibility E2E tests.

There is no permission system — /scriptlet/job/list returns all jobs
regardless of the requesting user's X-User header. Each job's `owner`
metadata is still populated from the X-User header that enqueued it.

Covers:
  - User A sees User B's jobs in the job list.
  - User B sees User A's jobs in the job list.
  - Both users see the same, complete job list.
  - A job's owner metadata reflects the enqueueing user.
"""

import time

import pytest
import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_A = "e2e_user_alpha"
USER_B = "e2e_user_beta"
SET = "multiuser_e2e"

E2E_PORT = 18321
BASE_URL = f"http://127.0.0.1:{E2E_PORT}"


# ---------------------------------------------------------------------------
# Fixtures — per-user API sessions
# ---------------------------------------------------------------------------


def _make_api_session(pyops_server, e2e_env, x_user):
    """Return a requests.Session sending X-User: x_user."""
    server_url = e2e_env["base_url"]
    session = requests.Session()
    if x_user:
        session.headers["X-User"] = x_user

    original = session.request

    def patched(method, url, **kwargs):
        if url.startswith("/"):
            url = server_url + url
        return original(method, url, **kwargs)

    session.request = patched
    return session


@pytest.fixture
def api_a(pyops_server, e2e_env):
    s = _make_api_session(pyops_server, e2e_env, USER_A)
    yield s
    s.close()


@pytest.fixture
def api_b(pyops_server, e2e_env):
    s = _make_api_session(pyops_server, e2e_env, USER_B)
    yield s
    s.close()


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


def _setup_shared_task(api):
    """Create a set + task scriptlet that both users can run."""
    api.post("/scriptlet/sets/create", json={"name": SET})
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET, "name": "shared", "type": "task"},
    )
    assert r.status_code == 200
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": SET, "name": "shared", "code": 'print("shared task output")\n'},
    )
    assert r.status_code == 200


def _enqueue(api, set_name="multiuser_e2e", task_name="shared"):
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


def _job_ids_for(api):
    """Return the set of job IDs visible to this user."""
    resp = api.get("/scriptlet/job/list")
    assert resp.status_code == 200
    return {j["id"] for j in resp.json()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiUserJobVisibility:
    """Job lists are not filtered per user — all users see all jobs."""

    def test_user_a_sees_user_b_jobs(self, api_a, api_b, pyops_server):
        """Jobs created by User B are visible in User A's job list."""
        _setup_shared_task(api_a)  # sets are shared; created once

        job_b = _enqueue(api_b)
        _wait_for_job(api_b, job_b)

        ids_a = _job_ids_for(api_a)
        assert job_b in ids_a, (
            f"User A should see User B's job {job_b}"
        )
        pyops_server.assert_no_unexpected_errors()

    def test_user_b_sees_user_a_jobs(self, api_a, api_b, pyops_server):
        """Jobs created by User A are visible in User B's job list."""
        _setup_shared_task(api_a)

        job_a = _enqueue(api_a)
        _wait_for_job(api_a, job_a)

        ids_b = _job_ids_for(api_b)
        assert job_a in ids_b, (
            f"User B should see User A's job {job_a}"
        )
        pyops_server.assert_no_unexpected_errors()

    def test_both_users_see_same_job_list(self, api_a, api_b, pyops_server):
        """After both users enqueue jobs, both lists contain both jobs."""
        _setup_shared_task(api_a)

        job_a = _enqueue(api_a)
        job_b = _enqueue(api_b)
        _wait_for_job(api_a, job_a)
        _wait_for_job(api_b, job_b)

        ids_a = _job_ids_for(api_a)
        ids_b = _job_ids_for(api_b)

        assert job_a in ids_a, "User A should see their own job"
        assert job_b in ids_a, "User A should see User B's job"
        assert job_a in ids_b, "User B should see User A's job"
        assert job_b in ids_b, "User B should see their own job"

        pyops_server.assert_no_unexpected_errors()

    def test_job_owner_metadata_is_correct(self, api_a, api_b, pyops_server):
        """The owner field on a job matches the X-User header used to enqueue it."""
        _setup_shared_task(api_a)

        job_a_id = _enqueue(api_a)
        job_b_id = _enqueue(api_b)

        resp_a = api_a.get(f"/scriptlet/job/item?id={job_a_id}")
        resp_b = api_b.get(f"/scriptlet/job/item?id={job_b_id}")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        assert resp_a.json()["owner"] == USER_A
        assert resp_b.json()["owner"] == USER_B

        pyops_server.assert_no_unexpected_errors()

    def test_multiple_jobs_same_user_all_visible(self, api_a, pyops_server):
        """A user who runs the same task multiple times sees all their own jobs."""
        _setup_shared_task(api_a)

        job_ids = [_enqueue(api_a) for _ in range(3)]
        for jid in job_ids:
            _wait_for_job(api_a, jid)

        ids_a = _job_ids_for(api_a)
        for jid in job_ids:
            assert jid in ids_a, f"User A's own job {jid} missing from their list"

        pyops_server.assert_no_unexpected_errors()
