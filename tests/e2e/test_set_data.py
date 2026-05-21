"""End-to-end coverage for set-scoped YAML/JSON data files.

Verifies the v3.3.0 feature wire-to-disk: API CRUD on data files,
runtime reads/writes through the injected `data` global, and the flock
serialisation under a real runner (not just the multiprocessing harness
used in the integration test).
"""
import json
import os
import time

import pytest


SET = "data_e2e"


def _wait_for_job(api, job_id, timeout=30):
    """Poll until terminal state."""
    terminal = {"done", "failed", "cancelled", "killed"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/scriptlet/job/item?id={job_id}")
        if r.status_code == 200:
            job = r.json()
            if job["state"] in terminal:
                return job
        time.sleep(0.2)
    pytest.fail(f"job {job_id} did not reach terminal state within {timeout}s")


class TestDataInventory:
    """The inventory API treats data/*.{yaml,json} files as
    type='data' entries. PUT/GET/POST/LIST/DELETE round-trip."""

    def test_create_lands_as_yaml_with_ext_reported(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        r = api.put("/scriptlet/inventory/item",
                    json={"set": SET, "name": "config", "type": "data"})
        assert r.status_code == 200, r.text

        # On disk: data/config.yaml with default empty-mapping content.
        repo = e2e_env["data_dir"] / "git" / SET
        assert (repo / "data" / "config.yaml").read_text() == "{}\n"

        # list_scriptlets reports the entry with ext.
        scripts = api.get("/scriptlet/inventory/list").json()
        data_entries = [s for s in scripts if s["set"] == SET and s["type"] == "data"]
        assert len(data_entries) == 1
        assert data_entries[0]["name"] == "config"
        assert data_entries[0]["ext"] == "yaml"

        # code_get reports the ext alongside the content (editor uses it
        # to pick the right Ace mode).
        r = api.get(f"/scriptlet/inventory/code?set={SET}&name=config")
        body = r.json()
        assert body["type"] == "data"
        assert body["ext"] == "yaml"
        assert body["code"] == "{}\n"
        pyops_server.assert_no_unexpected_errors()

    def test_draft_round_trips(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "state", "type": "data"})
        # Overwrite with valid YAML.
        new_content = "n: 7\nmessage: hello\n"
        r = api.post("/scriptlet/inventory/draft",
                     json={"set": SET, "name": "state", "code": new_content})
        assert r.status_code == 200, r.text
        on_disk = (e2e_env["data_dir"] / "git" / SET / "data" / "state.yaml").read_text()
        assert on_disk == new_content
        pyops_server.assert_no_unexpected_errors()


class TestDataRuntime:
    """The `data` global injected into scriptlet namespaces reaches the
    on-disk file through the real runner, the per-file flock serialises
    concurrent writes, and cross-set access is refused."""

    def test_data_write_then_read_persists(self, api, pyops_server, set_factory, commit_set, e2e_env):
        set_factory(SET)
        # Create config.yaml seeded via API.
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "cfg", "type": "data"})
        # A task that reads + writes data via the injected `data` global.
        code = (
            "with data.write('cfg') as c:\n"
            "    c['greeting'] = 'hello'\n"
            "    c['counter'] = c.get('counter', 0) + 1\n"
            "with data.read('cfg') as c:\n"
            "    import os, json\n"
            "    open(os.path.join(workspace, 'snapshot.json'), 'w').write(json.dumps(c))\n"
        )
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "writer", "type": "task"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "writer", "code": code})
        commit_set(SET, "add writer")

        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "writer", "drop_done": False,
                          "max_retries": 0, "param": "", "kind": "task"})
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "done", f"failed: {job.get('detail')}"

        # Workspace artefact confirms the read saw what the write set.
        snap_path = e2e_env["data_dir"] / "jobs" / SET / job_id / "snapshot.json"
        snapshot = json.loads(snap_path.read_text())
        assert snapshot == {"greeting": "hello", "counter": 1}
        # Authoritative on-disk copy in data/cfg.yaml has the same.
        import yaml as _yaml
        cfg = _yaml.safe_load((e2e_env["data_dir"] / "git" / SET / "data" / "cfg.yaml").read_text())
        assert cfg == {"greeting": "hello", "counter": 1}
        pyops_server.assert_no_unexpected_errors()

    def test_concurrent_increments_serialise(self, api, pyops_server, set_factory, commit_set, e2e_env):
        """Two parallel jobs each increment `n` four times. Without the
        exclusive flock, read-modify-write races would lose updates;
        with it, the final count is exact."""
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "counter", "type": "data"})
        code = (
            "for _ in range(4):\n"
            "    with data.write('counter') as c:\n"
            "        c['n'] = c.get('n', 0) + 1\n"
        )
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "incrementer", "type": "task"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "incrementer", "code": code})
        commit_set(SET, "add incrementer")

        # Enqueue two — the runner serialises spawns, but the in-job loop
        # leaves the lock between iterations, so a second job that starts
        # during the first's run will still contend on the file lock.
        ids = []
        for _ in range(2):
            r = api.put("/scriptlet/job/item",
                        json={"set": SET, "name": "incrementer", "drop_done": False,
                              "max_retries": 0, "param": "", "kind": "task"})
            ids.append(r.json()["job"])
        for jid in ids:
            job = _wait_for_job(api, jid, timeout=60)
            assert job["state"] == "done", f"{jid} failed: {job.get('detail')}"

        import yaml as _yaml
        final = _yaml.safe_load((e2e_env["data_dir"] / "git" / SET / "data" / "counter.yaml").read_text())
        assert final == {"n": 8}, f"lost updates: {final}"
        pyops_server.assert_no_unexpected_errors()

    def test_cross_set_access_rejected(self, api, pyops_server, set_factory, commit_set, e2e_env):
        set_factory(SET)
        code = (
            "with data.read('../other/x') as _:\n"
            "    pass\n"
        )
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "escape", "type": "task"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "escape", "code": code})
        commit_set(SET, "add escape")

        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "escape", "drop_done": False,
                          "max_retries": 0, "param": "", "kind": "task"})
        job = _wait_for_job(api, r.json()["job"])
        assert job["state"] == "failed"
        assert "ValueError" in (job.get("detail") or "") or "path separators" in (job.get("detail") or "")
        pyops_server.assert_no_unexpected_errors(allow=["ValueError"])
