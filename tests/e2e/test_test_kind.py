"""End-to-end coverage for kind=test.

Verifies the v3.3.0 feature wire-to-disk: a `*.task.py` enqueued with
`kind=test` runs against materialised `.test/src/` sources with
`__name__='__test__'`, and the bundled `pyops::pytest` library
programmatically invokes pytest against named test classes from
imported test libraries — no filesystem discovery, no pyops-side
pytest knowledge.
"""
import os
import time

import pytest


SET = "test_kind_e2e"


def _wait_for_job(api, job_id, timeout=90):
    terminal = {"done", "failed", "cancelled", "killed"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/scriptlet/job/item?id={job_id}")
        if r.status_code == 200:
            job = r.json()
            if job["state"] in terminal:
                return job
        time.sleep(0.3)
    pytest.fail(f"job {job_id} did not reach terminal state within {timeout}s")


TASK_BODY = (
    "#!pytest\n"
    "#!{::tests_lib}\n"
    "#!{pyops::pytest}\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    import os, json\n"
    "    open(os.path.join(workspace, 'main_ran.json'), 'w').write(json.dumps({'mode': __name__}))\n"
    "\n"
    "if __name__ == '__test__':\n"
    "    import os, json\n"
    "    open(os.path.join(workspace, 'test_block_ran.json'), 'w').write(json.dumps({'mode': __name__}))\n"
    "    pytest_run(test_sets=TEST_SETS)\n"
)

# Test classes live in libraries; pytest collects from the materialised
# library file (`.test/src/{set}/library/tests_lib.py`), importing it as
# a regular Python module. Test methods see what's defined in that file
# plus what it explicitly imports — NOT what the task body's exec
# scope contains. So production code being tested goes here too (or in
# a sibling library imported with a plain `import` statement, since the
# materialised library directory is on pytest's sys.path).
GREEN_LIB = (
    "def double(x):\n"
    "    return x * 2\n"
    "\n"
    "class TestWork:\n"
    "    def test_doubles(self):\n"
    "        assert double(3) == 6\n"
    "\n"
    "    def test_zero(self):\n"
    "        assert double(0) == 0\n"
)

RED_LIB = (
    "def double(x):\n"
    "    return x * 2\n"
    "\n"
    "class TestWork:\n"
    "    def test_passes(self):\n"
    "        assert double(2) == 4\n"
    "\n"
    "    def test_fails(self):\n"
    "        assert double(2) == 5  # intentional failure\n"
)


def _setup(api, set_factory, commit_set, lib_body):
    """Create the test task + library, commit so kind=task can read
    the committed copy (kind=test reads unstaged like debug/draft, but
    we also want to exercise kind=task on the same file)."""
    set_factory(SET)
    api.put("/scriptlet/inventory/item",
            json={"set": SET, "name": "my_task", "type": "task"})
    api.post("/scriptlet/inventory/draft",
             json={"set": SET, "name": "my_task", "code": TASK_BODY})
    api.put("/scriptlet/inventory/item",
            json={"set": SET, "name": "tests_lib", "type": "library"})
    api.post("/scriptlet/inventory/draft",
             json={"set": SET, "name": "tests_lib", "code": lib_body})
    commit_set(SET, "add task + test lib")


class TestKindTestLifecycle:
    """kind=test sets __name__='__test__' and materialises source; the
    pyops::pytest helper runs the named test classes."""

    def test_kind_task_runs_main_block(self, api, pyops_server, set_factory, commit_set, e2e_env):
        _setup(api, set_factory, commit_set, GREEN_LIB)
        # With kind=task, __name__ should be __main__.
        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "my_task", "drop_done": False,
                          "max_retries": 0,
                          "param": "TEST_SETS = []\n",
                          "kind": "task"})
        job = _wait_for_job(api, r.json()["job"])
        assert job["state"] == "done", f"failed: {job.get('detail')}"
        # main_ran.json should exist; test_block_ran.json should NOT.
        ws = e2e_env["data_dir"] / "jobs" / SET / r.json()["job"]
        assert (ws / "main_ran.json").is_file()
        assert not (ws / "test_block_ran.json").exists()
        pyops_server.assert_no_unexpected_errors()

    def test_kind_test_runs_test_block_with_test_name(self, api, pyops_server, set_factory, commit_set, e2e_env):
        _setup(api, set_factory, commit_set, GREEN_LIB)
        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "my_task", "drop_done": False,
                          "max_retries": 0,
                          "param": "TEST_SETS = ['TestWork']\n",
                          "kind": "test"})
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "done", f"failed: {job.get('detail')}"
        ws = e2e_env["data_dir"] / "jobs" / SET / job_id
        # __test__ ran, __main__ didn't.
        assert (ws / "test_block_ran.json").is_file()
        assert not (ws / "main_ran.json").exists()
        # Source was materialised under .test/src/.
        materialised = ws / ".test" / "src" / SET / "library" / "tests_lib.py"
        assert materialised.is_file()
        assert "class TestWork" in materialised.read_text()
        # stdout has pytest output (passes line).
        stdout = (ws / "stdout.log").read_text()
        assert "passed" in stdout or "PASSED" in stdout, f"no pytest pass marker in stdout:\n{stdout}"
        pyops_server.assert_no_unexpected_errors()

    def test_kind_test_failing_test_yields_failed_state(self, api, pyops_server, set_factory, commit_set, e2e_env):
        _setup(api, set_factory, commit_set, RED_LIB)
        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "my_task", "drop_done": False,
                          "max_retries": 0,
                          "param": "TEST_SETS = ['TestWork']\n",
                          "kind": "test"})
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "failed", f"expected failure but got: {job}"
        stdout = (e2e_env["data_dir"] / "jobs" / SET / job_id / "stdout.log").read_text()
        assert "test_fails" in stdout, f"no test_fails marker in stdout:\n{stdout}"
        # Server log warnings + tracebacks from intentional test failure
        # are expected for this test only.
        pyops_server.assert_no_unexpected_errors(allow=["assert", "AssertionError", "test_fails"])

    def test_kind_test_single_method_selection(self, api, pyops_server, set_factory, commit_set, e2e_env):
        _setup(api, set_factory, commit_set, GREEN_LIB)
        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "my_task", "drop_done": False,
                          "max_retries": 0,
                          "param": "TEST_SETS = ['TestWork::test_zero']\n",
                          "kind": "test"})
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "done", f"failed: {job.get('detail')}"
        stdout = (e2e_env["data_dir"] / "jobs" / SET / job_id / "stdout.log").read_text()
        # Pytest's "1 passed" (rather than "2 passed") proves the node-id
        # selection filtered down to one method even though the class
        # has two.
        assert "1 passed" in stdout, f"expected exactly one pass:\n{stdout}"
        assert "2 passed" not in stdout, f"both methods ran; node-id selection ignored:\n{stdout}"
        pyops_server.assert_no_unexpected_errors()
