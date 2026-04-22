"""
Set lifecycle E2E tests — create/remove side effects on disk.

A set's on-disk footprint spans three directories:
  - data/git/{name}   — Git repo holding committed scriptlets
  - data/venv/{name}  — Python virtualenv used by job subprocesses
  - data/pkg/{name}   — package cache

`create_set` (POST /scriptlet/sets/create) calls `ensure_set_environment`,
creating all three. `remove_set` (DELETE /scriptlet/sets/remove/{name})
calls `remove_set_environment` + rmtree of the git dir — all three must be
gone afterwards, and the name must be reusable.
"""

from pathlib import Path

import pytest


SET = "lifecycle_e2e"


def _dirs(e2e_env, set_name):
    data = Path(e2e_env["data_dir"])
    return {
        "git": data / "git" / set_name,
        "venv": data / "venv" / set_name,
        "pkg": data / "pkg" / set_name,
    }


class TestSetLifecycle:
    """create/remove endpoints manage git, venv, and pkg directories."""

    def test_create_set_makes_all_three_dirs(self, api, pyops_server, e2e_env):
        """POST /scriptlet/sets/create creates git, venv, and pkg directories."""
        r = api.post("/scriptlet/sets/create", json={"name": SET})
        assert r.status_code == 200

        dirs = _dirs(e2e_env, SET)
        assert dirs["git"].is_dir(), "git dir must be created"
        assert dirs["venv"].is_dir(), "venv dir must be created"
        assert dirs["pkg"].is_dir(), "pkg dir must be created"
        pyops_server.assert_no_unexpected_errors()

    def test_remove_set_deletes_all_three_dirs(self, api, pyops_server, e2e_env):
        """DELETE /scriptlet/sets/remove/{name} removes git, venv, and pkg dirs."""
        r = api.post("/scriptlet/sets/create", json={"name": SET + "_del"})
        assert r.status_code == 200
        dirs = _dirs(e2e_env, SET + "_del")
        assert dirs["git"].exists()

        r = api.delete(f"/scriptlet/sets/remove/{SET}_del")
        assert r.status_code == 200, f"remove failed: {r.text}"

        assert not dirs["git"].exists(), "git dir must be removed"
        assert not dirs["venv"].exists(), "venv dir must be removed"
        assert not dirs["pkg"].exists(), "pkg dir must be removed"
        pyops_server.assert_no_unexpected_errors()

    def test_remove_unknown_set_returns_404(self, api, pyops_server):
        """Removing a set that was never created returns HTTP 404."""
        r = api.delete("/scriptlet/sets/remove/does-not-exist-xyzzy")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_recreate_after_remove_succeeds(self, api, pyops_server, e2e_env):
        """A removed set's name is immediately reusable (proves cleanup was complete)."""
        name = SET + "_recycle"
        api.post("/scriptlet/sets/create", json={"name": name})
        api.delete(f"/scriptlet/sets/remove/{name}")

        r = api.post("/scriptlet/sets/create", json={"name": name})
        assert r.status_code == 200, (
            f"Recreating a deleted set must succeed (cleanup was incomplete): {r.text}"
        )
        dirs = _dirs(e2e_env, name)
        assert dirs["git"].is_dir()
        assert dirs["venv"].is_dir()
        assert dirs["pkg"].is_dir()
        pyops_server.assert_no_unexpected_errors()

    def test_create_duplicate_set_returns_400(self, api, pyops_server):
        """Creating an already-existing set returns HTTP 400."""
        name = SET + "_dup"
        r = api.post("/scriptlet/sets/create", json={"name": name})
        assert r.status_code == 200

        r = api.post("/scriptlet/sets/create", json={"name": name})
        assert r.status_code == 400
        pyops_server.assert_no_unexpected_errors()

    def test_remove_set_cleans_up_after_job_ran(self, api, pyops_server, e2e_env):
        """A set that has actually executed a job can still be removed cleanly."""
        import time

        name = SET + "_used"
        api.post("/scriptlet/sets/create", json={"name": name})
        api.put(
            "/scriptlet/inventory/item",
            json={"set": name, "name": "t", "type": "task"},
        )
        api.post(
            "/scriptlet/inventory/item",
            json={"set": name, "name": "t", "code": 'print("ok")\n'},
        )
        r = api.put(
            "/scriptlet/job/item",
            json={
                "set": name, "name": "t", "drop_done": False,
                "max_retries": 0, "param": "", "kind": "task",
            },
        )
        assert r.status_code == 200
        job_id = r.json()["job"]

        # Wait for terminal state.
        terminal = {"done", "failed", "cancelled", "killed"}
        deadline = time.time() + 30
        while time.time() < deadline:
            j = api.get(f"/scriptlet/job/item?id={job_id}").json()
            if j["state"] in terminal:
                break
            time.sleep(0.3)
        else:
            pytest.fail(f"Job {job_id} did not reach terminal state")

        r = api.delete(f"/scriptlet/sets/remove/{name}")
        assert r.status_code == 200, f"remove failed: {r.text}"

        dirs = _dirs(e2e_env, name)
        assert not dirs["git"].exists()
        assert not dirs["venv"].exists()
        assert not dirs["pkg"].exists()
        pyops_server.assert_no_unexpected_errors()
