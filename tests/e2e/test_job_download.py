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


def _setup_and_run(api, code, set_factory, commit_set, set_name=SET, task_name="t"):
    set_factory(set_name)
    api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "type": "task"},
    )
    api.post(
        "/scriptlet/inventory/draft",
        json={"set": set_name, "name": task_name, "code": code},
    )
    commit_set(set_name, f"add {task_name}")
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

    def test_download_contains_stdout_log(self, api, pyops_server, set_factory, commit_set):
        """A completed task's workspace ZIP includes its stdout.log with output."""
        job_id = _setup_and_run(api, 'print("zip_marker_line")\n', set_factory, commit_set)
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


class TestStdoutStderrRedirect:
    """E2E regression for the 3.3.0 fd-level stdout/stderr redirect.

    Pre-3.3.0 the runner used `contextlib.redirect_stdout` /
    `redirect_stderr`, which only swap the Python-level objects. Raw
    fd-level writers — `os.write(1, ...)`, `os.write(2, ...)`, child
    processes inheriting fds, C-extension / native code (HF tqdm,
    torch native, transformers download bars) — bypassed it and ended
    up in the gunicorn worker's stdout/stderr instead of the per-job
    `stdout.log` / `stderr.log`. The 3.3.0 fix uses `os.dup2` to
    rewire fd1 / fd2 to the log files for the duration of the job.

    These tests exercise the full HTTP enqueue → runner → log file
    pathway via the `/scriptlet/job/download` ZIP."""

    def test_fd_level_writes_end_in_logs(self, api, pyops_server, set_factory, commit_set):
        """`os.write(1, ...)` / `os.write(2, ...)` (bypassing
        `sys.stdout` / `sys.stderr`) reach the job's stdout.log /
        stderr.log."""
        code = (
            "import os\n"
            "os.write(1, b'e2e-fd1-marker\\n')\n"
            "os.write(2, b'e2e-fd2-marker\\n')\n"
        )
        job_id = _setup_and_run(api, code, set_factory, commit_set,
                                set_name=SET + "_fd", task_name="fd_writes")
        _wait_for_terminal(api, job_id)

        r = api.get(f"/scriptlet/job/download?id={job_id}")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            stdout_logs = [n for n in names if n.endswith("stdout.log")]
            stderr_logs = [n for n in names if n.endswith("stderr.log")]
            assert stdout_logs and stderr_logs, f"missing logs in zip; got {names}"
            stdout_content = zf.read(stdout_logs[0]).decode("utf-8", errors="replace")
            stderr_content = zf.read(stderr_logs[0]).decode("utf-8", errors="replace")
        assert "e2e-fd1-marker" in stdout_content, (
            f"fd1 write missing from stdout.log; got:\n{stdout_content}"
        )
        assert "e2e-fd2-marker" in stderr_content, (
            f"fd2 write missing from stderr.log; got:\n{stderr_content}"
        )
        pyops_server.assert_no_unexpected_errors()

    def test_child_process_output_ends_in_logs(self, api, pyops_server, set_factory, commit_set):
        """A child process spawned with no explicit stdout/stderr
        capture inherits the redirected fds and its output reaches the
        job log files. Closest proxy in pure-Python land for what
        tqdm / native HF downloaders do."""
        code = (
            "import os\n"
            "os.system('echo e2e-child-stdout; echo e2e-child-stderr >&2')\n"
        )
        job_id = _setup_and_run(api, code, set_factory, commit_set,
                                set_name=SET + "_child", task_name="child_out")
        _wait_for_terminal(api, job_id)

        r = api.get(f"/scriptlet/job/download?id={job_id}")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            stdout_logs = [n for n in names if n.endswith("stdout.log")]
            stderr_logs = [n for n in names if n.endswith("stderr.log")]
            stdout_content = zf.read(stdout_logs[0]).decode("utf-8", errors="replace")
            stderr_content = zf.read(stderr_logs[0]).decode("utf-8", errors="replace")
        assert "e2e-child-stdout" in stdout_content, (
            f"child stdout missing from stdout.log; got:\n{stdout_content}"
        )
        assert "e2e-child-stderr" in stderr_content, (
            f"child stderr missing from stderr.log; got:\n{stderr_content}"
        )
        pyops_server.assert_no_unexpected_errors()
