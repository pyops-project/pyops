"""
Job artifact download E2E tests.

Covers GET /scriptlet/job/download?id=<id>:
  - Streams a ZIP containing the job's workspace directory (stdout.log,
    stderr.log and anything else written under data/jobs/<set>/<id>).
  - Unknown id returns HTTP 404.
"""

import io
import time
import zipfile

import pytest


SET = "download_e2e"


def _wait_for_terminal(api, job_id, timeout=30):
    terminal = {"done", "failed", "cancelled", "killed"}
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = api.get(f"/scriptlet/job/item?id={job_id}")
        assert r.status_code == 200
        last = r.json()
        if last["state"] in terminal:
            return last
        time.sleep(0.3)
    pytest.fail(f"job {job_id} not terminal within {timeout}s (last={last})")


def _setup_and_run(api, code, set_name=SET, task_name="t"):
    api.post("/scriptlet/sets/create", json={"name": set_name})
    api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "type": "task"},
    )
    api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "code": code},
    )
    r = api.put(
        "/scriptlet/job/item",
        json={
            "set": set_name, "name": task_name,
            "drop_done": False, "max_retries": 0, "param": "", "kind": "task",
        },
    )
    assert r.status_code == 200
    return r.json()["job"]


class TestJobDownload:
    """GET /scriptlet/job/download returns a ZIP of the job workspace."""

    def test_download_contains_stdout_log(self, api, pyops_server):
        """A completed task's workspace ZIP includes its stdout.log with output."""
        job_id = _setup_and_run(api, 'print("zip_marker_line")\n')
        _wait_for_terminal(api, job_id)

        r = api.get(f"/scriptlet/job/download?id={job_id}")
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "application/x-zip-compressed",
            "application/zip",
        )
        disp = r.headers.get("content-disposition", "")
        assert f'{job_id}.zip' in disp

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            stdout_entries = [n for n in names if n.endswith("stdout.log")]
            assert stdout_entries, f"no stdout.log in zip; got {names}"
            content = zf.read(stdout_entries[0]).decode("utf-8", errors="replace")
            assert "zip_marker_line" in content
        pyops_server.assert_no_unexpected_errors()

    def test_download_unknown_job_returns_404(self, api, pyops_server):
        r = api.get("/scriptlet/job/download?id=does-not-exist-xyzzy")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()
