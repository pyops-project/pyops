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
    # pytest no longer needs its own directive here — `#!{pyops::pytest}`
    # carries `#!pytest` at the top of pytest.library.py and pyops
    # processes library directives transitively.
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
# library file (`.test/src/{set}/library/tests_lib.py`). The
# `pyops::pytest` helper pre-registers that file in `sys.modules` with
# the calling task's exec globals seeded into the module dict — so
# test methods see every pyops-injected and directive-merged symbol
# (`setname`, `workspace`, `data`, library classes / helpers, …) by
# bare name, same as the task body. Classical Python imports inside
# test files are still required for stdlib / venv-installed packages
# (pytest, framework libs); they never run for pyops-side content
# because pyops symbols are not `sys.modules` entries to begin with.
# See `app/pyops/pytest.library.py` "Namespace contract" section.
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

    def test_kind_testdebug_runs_test_block_under_debug_materialisation(self, api, pyops_server, set_factory, commit_set, e2e_env):
        """kind=testdebug is the hybrid: __name__='__test__' (test
        trait) but sources materialise under .debug/src/ (debug trait)
        with a .test → .debug symlink so tooling on either path
        resolves. Param is empty here — the kind's debugger semantics
        only kick in when the client puts a bootstrap into param; the
        materialisation + __name__ wiring is independent."""
        _setup(api, set_factory, commit_set, GREEN_LIB)
        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "my_task", "drop_done": False,
                          "max_retries": 0,
                          "param": "TEST_SETS = ['TestWork']\n",
                          "kind": "testdebug"})
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "done", f"failed: {job.get('detail')}"
        ws = e2e_env["data_dir"] / "jobs" / SET / job_id

        # __test__ block ran, __main__ did not — same as kind=test.
        assert (ws / "test_block_ran.json").is_file()
        assert not (ws / "main_ran.json").exists()

        # Sources materialised under .debug/src/ (NOT .test/src/) —
        # the debug-family trait. The path-mapping in VSIX
        # (`./.debug/src` ↔ workspace pyops dir) therefore works for
        # both debug and testdebug unchanged.
        debug_lib = ws / ".debug" / "src" / SET / "library" / "tests_lib.py"
        assert debug_lib.is_file(), f"expected sources under .debug/src/, got: {list(ws.iterdir())}"

        # .test must be a symlink → .debug so consumers that look at
        # the canonical "test-sources" path find them too.
        test_link = ws / ".test"
        assert test_link.is_symlink(), f".test must be a symlink, got: {test_link}"
        assert os.readlink(str(test_link)) == ".debug"
        # And it must resolve: walking through .test/src/ hits the same files.
        assert (ws / ".test" / "src" / SET / "library" / "tests_lib.py").is_file()

        pyops_server.assert_no_unexpected_errors()

    def test_kind_test_namespace_bridge_exposes_task_scope_to_tests(self, api, pyops_server, set_factory, commit_set, e2e_env):
        """End-to-end coverage for the pyops::pytest namespace bridge:
        test bodies reference pyops-injected globals (`setname`,
        `workspace`) and directive-merged production helpers
        (`shared_constant`, `multiply`) by bare name — no classical
        `from X import Y` for pyops-side content. Asserts the test
        run passes and produces pytest pass markers."""
        set_factory(SET)
        # Production library: provides a function + constant that the
        # test bodies will read by bare name via the namespace bridge.
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "shared_lib", "type": "library"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "shared_lib",
                       "code": "shared_constant = 41\n\n"
                               "def multiply(a, b):\n"
                               "    return a * b\n"})
        # Test library: NO directive imports of shared_lib, NO classical
        # imports of shared_constant / multiply. References them by bare
        # name; only works if the namespace bridge fires.
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "bridge_tests", "type": "library"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "bridge_tests",
                       "code": "class TestBridge:\n"
                               "    def test_directive_merged_constant(self):\n"
                               "        assert shared_constant == 41\n"
                               "\n"
                               "    def test_directive_merged_function(self):\n"
                               "        assert multiply(6, 7) == 42\n"
                               "\n"
                               "    def test_pyops_injected_setname(self):\n"
                               "        assert setname == '" + SET + "'\n"
                               "\n"
                               "    def test_pyops_injected_workspace_isdir(self):\n"
                               "        import os\n"
                               "        assert os.path.isdir(workspace)\n"})
        # Task: pulls both shared_lib + bridge_tests via directives; the
        # __test__ block then runs pytest_run with the bridge fixture.
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "bridge_task", "type": "task"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "bridge_task",
                       "code": "#!{::shared_lib}\n"
                               "#!{::bridge_tests}\n"
                               "#!{pyops::pytest}\n"
                               "\n"
                               "if __name__ == '__test__':\n"
                               "    pytest_run(test_sets=['TestBridge'])\n"})
        commit_set(SET, "add bridge task + libs")

        r = api.put("/scriptlet/job/item",
                    json={"set": SET, "name": "bridge_task", "drop_done": False,
                          "max_retries": 0, "param": "", "kind": "test"})
        assert r.status_code == 200, f"enqueue failed: {r.status_code} {r.text}"
        job_id = r.json()["job"]
        job = _wait_for_job(api, job_id)
        assert job["state"] == "done", f"namespace bridge failed: {job.get('detail')}"
        stdout = (e2e_env["data_dir"] / "jobs" / SET / job_id / "stdout.log").read_text()
        # All four tests must pass — proves bare-name access to both
        # directive-merged symbols (shared_constant, multiply) and
        # pyops-injected globals (setname, workspace).
        assert "4 passed" in stdout, f"expected 4 passes, got:\n{stdout}"
        pyops_server.assert_no_unexpected_errors()

    def test_manager_ui_test_button_enqueues_kind_test(self, e2e_page, api, pyops_server, set_factory, commit_set, e2e_env):
        """Clicking the new 'science' button in the Manager UI on a
        task scriptlet enqueues a kind=test job. Round-trip proof that
        the WebUI surface is wired: the button exists, the handler
        emits the right PUT body, and the runner picks it up."""
        _setup(api, set_factory, commit_set, GREEN_LIB)

        # Navigate to Manager via the sidebar menu.
        e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        # Wait for the row of our task to appear (refresh is polled).
        frame.locator(f'[data-testid="scriptlet-row-{SET}-my_task"]').wait_for(state="visible", timeout=15_000)
        # The new science-icon button.
        frame.locator(f'[data-testid="scriptlet-test-{SET}-my_task"]').click()

        # Poll for the kind=test job to appear and reach terminal state.
        # /scriptlet/job/list returns id only (no set/name fields); the id
        # is `{owner}_{set}_{name}_{timestamp}` so we match by prefix.
        deadline = time.time() + 60
        chosen = None
        terminal = {"done", "failed", "cancelled", "killed"}
        id_prefix_fragment = f"_{SET}_my_task_"
        while time.time() < deadline and chosen is None:
            for j in api.get("/scriptlet/job/list").json():
                if id_prefix_fragment in j["id"] and j["kind"] == "test":
                    chosen = j
                    break
            time.sleep(0.5)
        assert chosen is not None, "no kind=test job appeared after clicking the science button"
        # Use the param-less default; with TEST_SETS undefined, pytest_run
        # raises ValueError (empty list), which is fine — the test only
        # cares that kind=test was enqueued and ran.
        if chosen["state"] not in terminal:
            chosen = _wait_for_job(api, chosen["id"], timeout=60)
        # Failed is acceptable here (the task has no param so test_sets is
        # not set; pytest_run raises). The point is the button wired up
        # to kind=test correctly.
        assert chosen["state"] in terminal
        pyops_server.assert_no_unexpected_errors(allow=["ValueError", "test_sets", "NameError"])
