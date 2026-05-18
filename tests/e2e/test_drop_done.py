"""
drop_done auto-removal E2E tests.

When a job is enqueued with drop_done=True and reaches the "done" state,
the runner's cleanup_drop_done_jobs() loop marks it "remove" once
drop_done_delay_seconds have elapsed, which then causes it to be deleted
from the DB on the next disposeJobs() pass.

This file overrides the e2e_env fixture to set drop_done_delay_seconds=2
so the test does not have to wait for the default 60-second delay.
"""

import json
import os
import time

import pytest

from tests.e2e.conftest import E2E_PORT, BASE_URL


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET = "dropdone_e2e"
DROP_DONE_DELAY = 2   # seconds — short enough for a test, long enough to be deterministic


# ---------------------------------------------------------------------------
# Fixtures: override e2e_env with short drop_done_delay_seconds
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_env(tmp_path):
    """Override conftest e2e_env: sets drop_done_delay_seconds=2."""
    import os
    PROJECT_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    data_dir = tmp_path / "data"
    (data_dir / "jobs").mkdir(parents=True)
    (data_dir / "git").mkdir(parents=True)
    (data_dir / "venv").mkdir(parents=True)
    (data_dir / "pkg").mkdir(parents=True)

    config = {
        "web": {
            "bind": f"127.0.0.1:{E2E_PORT}",
            "worker": 1,
            "timeout": 300,
        },
        "data": {
            "jobs": str(data_dir / "jobs"),
            "git": str(data_dir / "git"),
            "db": str(data_dir / "app.db"),
        },
        "runner": {
            "venv": str(data_dir / "venv"),
            "pkg": str(data_dir / "pkg"),
            "git_pull_interval": 9999,
            "drop_done_delay_seconds": DROP_DONE_DELAY,
        },
        "log": {"loglevel": "debug"},
        "users": {"anonymous": {"debug_port": 15679}},
    }

    config_path = tmp_path / "pyops_e2e.json"
    config_path.write_text(json.dumps(config, indent=2))

    return {
        "config_path": str(config_path),
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "base_url": BASE_URL,
        "port": E2E_PORT,
    }


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


def _job_visible(api, job_id):
    """Return True if the job still appears in the job list."""
    resp = api.get("/scriptlet/job/list")
    assert resp.status_code == 200
    return any(j["id"] == job_id for j in resp.json())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDropDone:
    """Jobs with drop_done=True are auto-removed after the configured delay."""

    def test_drop_done_job_removed_after_delay(self, api, pyops_server, set_factory):
        """A drop_done=True task disappears from the job list after completion + delay."""
        set_factory(SET)
        api.put(
            "/scriptlet/inventory/item",
            json={"set": SET, "name": "quick", "type": "task"},
        )
        api.post(
            "/scriptlet/inventory/item",
            json={"set": SET, "name": "quick", "code": 'print("done")\n'},
        )

        resp = api.put(
            "/scriptlet/job/item",
            json={
                "set": SET,
                "name": "quick",
                "drop_done": True,   # ← key flag under test
                "max_retries": 0,
                "param": "",
                "kind": "task",
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job"]

        # Wait for "done" state.
        job = _wait_for_job(api, job_id, timeout=30)
        assert job["state"] == "done"

        # Job should still be visible immediately after completion.
        assert _job_visible(api, job_id), "Job should be visible right after completion"

        # After drop_done_delay_seconds + a cleanup loop cycle the job is gone.
        # The runner loop runs every ~0.5 s, so DROP_DONE_DELAY + 3 s is ample.
        deadline = time.time() + DROP_DONE_DELAY + 6
        while time.time() < deadline:
            if not _job_visible(api, job_id):
                break
            time.sleep(0.5)
        else:
            pytest.fail(
                f"Job {job_id} was not auto-removed within "
                f"{DROP_DONE_DELAY + 6}s of completion"
            )

        pyops_server.assert_no_unexpected_errors()

    def test_drop_done_false_job_stays_in_list(self, api, pyops_server, set_factory):
        """A drop_done=False task stays in the job list after completion."""
        set_factory(SET + "_keep")
        api.put(
            "/scriptlet/inventory/item",
            json={"set": SET + "_keep", "name": "keeper", "type": "task"},
        )
        api.post(
            "/scriptlet/inventory/item",
            json={"set": SET + "_keep", "name": "keeper", "code": 'print("keep me")\n'},
        )

        resp = api.put(
            "/scriptlet/job/item",
            json={
                "set": SET + "_keep",
                "name": "keeper",
                "drop_done": False,  # ← explicitly retained
                "max_retries": 0,
                "param": "",
                "kind": "task",
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job"]

        job = _wait_for_job(api, job_id, timeout=30)
        assert job["state"] == "done"

        # Wait well past the configured delay; job should not disappear.
        time.sleep(DROP_DONE_DELAY + 4)
        assert _job_visible(api, job_id), (
            "Job with drop_done=False should not be auto-removed"
        )

        pyops_server.assert_no_unexpected_errors()

    def test_failed_job_with_drop_done_is_not_removed(self, api, pyops_server, set_factory, commit_set):
        """drop_done only applies to successful ("done") jobs, not failed ones."""
        set_factory(SET + "_fail")
        api.put(
            "/scriptlet/inventory/item",
            json={"set": SET + "_fail", "name": "bad", "type": "task"},
        )
        api.post(
            "/scriptlet/inventory/draft",
            json={
                "set": SET + "_fail",
                "name": "bad",
                "code": 'raise RuntimeError("intentional")\n',
            },
        )
        commit_set(SET + "_fail", "add bad")

        resp = api.put(
            "/scriptlet/job/item",
            json={
                "set": SET + "_fail",
                "name": "bad",
                "drop_done": True,  # flag set, but job will fail
                "max_retries": 0,
                "param": "",
                "kind": "task",
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job"]

        job = _wait_for_job(api, job_id, timeout=30)
        assert job["state"] == "failed"

        # Give the runner several cycles; failed job must not disappear.
        time.sleep(DROP_DONE_DELAY + 4)
        assert _job_visible(api, job_id), (
            "Failed job should not be auto-removed by drop_done logic"
        )

        pyops_server.assert_no_unexpected_errors(allow=["intentional"])
