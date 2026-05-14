"""
Pip/package install E2E tests.

Covers the #!<package> directive that installs pip packages into the
per-set venv before task code is executed (process_install_lines in
app/lib/job.py).

Syntax:
  #!six               — runs: pip install six
  #!requests>=2.25    — pip install with version specifier
  Lines starting with #!{ or containing :: are skipped (they are library
  directives, not install directives).

NOTE: These tests require network access to PyPI and are slower than other
e2e tests because they run a real pip install inside a fresh per-set venv.
The 120-second timeout covers venv bootstrap + package download.
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET = "pip_e2e"

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
    assert r.status_code == 200, f"create {name} failed: {r.text}"
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "code": code},
    )
    assert r.status_code == 200, f"save {name} failed: {r.text}"


def _run(api, set_name, task_name, param="", timeout=120):
    r = api.put(
        "/scriptlet/job/item",
        json={
            "set": set_name,
            "name": task_name,
            "drop_done": False,
            "max_retries": 0,
            "param": param,
            "kind": "task",
        },
    )
    assert r.status_code == 200, f"enqueue failed: {r.text}"
    return _wait_for_job(api, r.json()["job"], timeout=timeout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipInstall:
    """Tasks that install pip packages via #!<package> in code or param."""

    def test_package_installed_and_importable(self, api, pyops_server):
        """#!six in task code installs the package and makes it importable.

        'six' is chosen because it is tiny (~33 KB) and has no transitive
        dependencies, minimising download time.
        """
        api.post("/scriptlet/sets/create", json={"name": SET})

        task_code = (
            "#!six\n"
            "import six\n"
            'print(f"six_version={six.__version__}")\n'
            "print(f\"is_py3={six.PY3}\")\n"
        )
        _create(api, SET, "pip_task", "task", task_code)

        job = _run(api, SET, "pip_task")
        assert job["state"] == "done", (
            f"Expected done, got {job['state']}: {job.get('detail', '')}"
        )
        assert "six_version=" in job["stdout"]
        assert "is_py3=True" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_pip_install_in_param_available_in_task(self, api, pyops_server):
        """#!<package> in param is processed before task code executes.

        The param runs first; its pip install makes the package available
        to the main task code even though the main code has no #! line.
        """
        api.post("/scriptlet/sets/create", json={"name": SET + "_param"})

        # Main code imports six — no #! directive here.
        task_code = "import six\nprint(f\"param_install={six.__version__}\")\n"
        _create(api, SET + "_param", "param_task", "task", task_code)

        # Install directive lives in param.
        job = _run(api, SET + "_param", "param_task", param="#!six")
        assert job["state"] == "done", (
            f"Expected done, got {job['state']}: {job.get('detail', '')}"
        )
        assert "param_install=" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()

    def test_second_run_reuses_venv_without_reinstall_errors(self, api, pyops_server):
        """Running the same pip-install task twice succeeds both times.

        Per-job (default) runs `pip install <pkg>` without `--upgrade`. The
        second run sees the package already in the venv → pip says
        "already satisfied" → fast, no index lookup, no reinstall.
        """
        api.post("/scriptlet/sets/create", json={"name": SET + "_reuse"})

        task_code = "#!six\nimport six\nprint(f\"ok={six.__version__}\")\n"
        _create(api, SET + "_reuse", "reuse_task", "task", task_code)

        for _ in range(2):
            job = _run(api, SET + "_reuse", "reuse_task")
            assert job["state"] == "done", (
                f"Expected done, got {job['state']}: {job.get('detail', '')}"
            )
            assert "ok=" in job["stdout"]

        pyops_server.assert_no_unexpected_errors()

    def test_nonexistent_package_fails_job(self, api, pyops_server):
        """A #! line naming a non-existent package causes the job to fail."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_bad"})

        task_code = (
            "#!nonexistent-package-xyzzy-e2e-12345\n"
            'print("should not reach here")\n'
        )
        _create(api, SET + "_bad", "bad_pkg_task", "task", task_code)

        job = _run(api, SET + "_bad", "bad_pkg_task")
        assert job["state"] == "failed", (
            f"Expected failed, got {job['state']}"
        )
        pyops_server.assert_no_unexpected_errors(
            allow=[
                "nonexistent-package-xyzzy",
                "Install processing failed",
                "CalledProcessError",
                "No matching distribution",
            ]
        )

    def test_library_directive_not_treated_as_pip_install(self, api, pyops_server):
        """#!{::libname} lines are skipped by process_install_lines.

        process_install_lines skips lines where rest starts with '{',
        so library directives do not accidentally trigger pip installs.
        """
        api.post("/scriptlet/sets/create", json={"name": SET + "_skip"})

        lib_code = "MAGIC = 99\n"
        _create(api, SET + "_skip", "my_lib", "library", lib_code)

        # The #!{::my_lib} line is a library directive, not a pip install.
        task_code = "#!{::my_lib}\nprint(f\"magic={MAGIC}\")\n"
        _create(api, SET + "_skip", "lib_task", "task", task_code)

        # Should succeed via library import, not pip install.
        job = _run(api, SET + "_skip", "lib_task")
        assert job["state"] == "done", job.get("detail")
        assert "magic=99" in job["stdout"]
        pyops_server.assert_no_unexpected_errors()
