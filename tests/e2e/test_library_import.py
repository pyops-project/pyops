"""
Library import E2E tests.

Covers the #!{::name} (same-set) and #!{set::name} (cross-set) import
directive syntax that lets task scriptlets load and use library scriptlets.

The import directive is resolved by process_imports() in app/lib/job.py:
  - #!{::libname}        — imports "libname" from the task's own set
  - #!{otherset::libname} — imports "libname" from "otherset"
  - Libraries are executed in topological order before the task code.
  - Transitivity: a library can itself import other libraries.
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET = "libimport_e2e"
CROSS_SET = "libimport_cross_e2e"

GREETER_CODE = """\
def greet(name):
    return f"hello, {name}!"

def add(a, b):
    return a + b
"""

# Two-level transitive dependency: task → mid_lib → base_lib
BASE_LIB_CODE = "BASE_VALUE = 42\n"

MID_LIB_CODE = """\
#!{::base_lib}
def doubled():
    return BASE_VALUE * 2
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


def _create(api, set_name, name, type_, code):
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "type": type_},
    )
    assert r.status_code == 200, f"create {name} ({type_}) failed: {r.text}"
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "code": code},
    )
    assert r.status_code == 200, f"save {name} failed: {r.text}"


def _run(api, set_name, task_name, timeout=30):
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
    return _wait_for_job(api, r.json()["job"], timeout=timeout)


# ---------------------------------------------------------------------------
# Tests: same-set imports
# ---------------------------------------------------------------------------


class TestSameSetLibraryImport:
    """Tasks importing library scriptlets from the same set via #!{::name}."""

    def test_task_calls_library_function(self, api, pyops_server):
        """Task using #!{::greeter} can call greet() and add() in its scope."""
        api.post("/scriptlet/sets/create", json={"name": SET})
        _create(api, SET, "greeter", "library", GREETER_CODE)

        task_code = (
            '#!{::greeter}\n'
            'print(greet("e2e"))\n'
            'print(f"sum={add(3, 4)}")\n'
        )
        _create(api, SET, "consumer", "task", task_code)

        job = _run(api, SET, "consumer")
        assert job["state"] == "done", job.get("detail")
        assert "hello, e2e!" in job["stdout"]
        assert "sum=7" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_library_state_is_isolated_between_jobs(self, api, pyops_server):
        """Each job execution gets a fresh global scope — library state does not leak."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_iso"})
        lib_code = "COUNTER = 0\ndef inc(): global COUNTER; COUNTER += 1; return COUNTER\n"
        _create(api, SET + "_iso", "counter_lib", "library", lib_code)

        task_code = '#!{::counter_lib}\nprint(f"count={inc()}")\n'
        _create(api, SET + "_iso", "counter_task", "task", task_code)

        # Run twice — each run should start COUNTER from 0, so inc() always returns 1.
        for _ in range(2):
            job = _run(api, SET + "_iso", "counter_task")
            assert job["state"] == "done", job.get("detail")
            assert "count=1" in job["stdout"]

        pyops_server.assert_no_unexpected_errors()

    def test_transitive_library_import(self, api, pyops_server):
        """Library importing another library resolves transitively before execution."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_tr"})
        _create(api, SET + "_tr", "base_lib", "library", BASE_LIB_CODE)
        _create(api, SET + "_tr", "mid_lib", "library", MID_LIB_CODE)

        task_code = '#!{::mid_lib}\nprint(f"doubled={doubled()}")\n'
        _create(api, SET + "_tr", "task_tr", "task", task_code)

        job = _run(api, SET + "_tr", "task_tr")
        assert job["state"] == "done", job.get("detail")
        assert "doubled=84" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_missing_library_fails_job(self, api, pyops_server):
        """Importing a non-existent library causes the job to reach 'failed' state."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_miss"})
        task_code = '#!{::no_such_lib}\nprint("unreachable")\n'
        _create(api, SET + "_miss", "bad_import", "task", task_code)

        job = _run(api, SET + "_miss", "bad_import")
        assert job["state"] == "failed"
        pyops_server.assert_no_unexpected_errors(allow=["no_such_lib", "not existing"])


# ---------------------------------------------------------------------------
# Tests: cross-set imports
# ---------------------------------------------------------------------------


class TestCrossSetLibraryImport:
    """Tasks importing library scriptlets from a different set via #!{set::name}."""

    def test_task_imports_library_from_other_set(self, api, pyops_server):
        """#!{otherset::libname} loads the library from the named set."""
        api.post("/scriptlet/sets/create", json={"name": CROSS_SET})
        _create(api, CROSS_SET, "shared_utils", "library", GREETER_CODE)

        api.post("/scriptlet/sets/create", json={"name": SET + "_cross"})
        task_code = f'#!{{{CROSS_SET}::shared_utils}}\nprint(greet("cross"))\n'
        _create(api, SET + "_cross", "cross_task", "task", task_code)

        job = _run(api, SET + "_cross", "cross_task")
        assert job["state"] == "done", job.get("detail")
        assert "hello, cross!" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_cross_set_missing_library_fails_job(self, api, pyops_server):
        """Importing from a non-existent set causes the job to fail."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_cross_miss"})
        task_code = '#!{nonexistent_set_xyz::some_lib}\nprint("unreachable")\n'
        _create(api, SET + "_cross_miss", "bad_cross", "task", task_code)

        job = _run(api, SET + "_cross_miss", "bad_cross")
        assert job["state"] == "failed"
        pyops_server.assert_no_unexpected_errors(
            allow=["nonexistent_set_xyz", "not existing"]
        )
