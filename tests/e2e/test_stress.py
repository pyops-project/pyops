"""
Stress E2E tests: concurrent scriptlet and job operations under load.

Targets the repo_lock (fcntl.flock) code path and ensures PyOps handles
concurrent git operations without deadlocks, crashes, or data corruption.

Expected under contention:
  - "nothing to commit" (git exit 1) when concurrent saves overlap
  - "index.lock exists" (git exit 128) when git's own lock is held
  - These are NOT treated as failures — only deadlocks, crashes, and
    unexpected 500s are failures.

Run with:  pytest tests/e2e/test_stress.py -v
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests


BASE_URL = "http://127.0.0.1:18321"
SET_PREFIX = "stress"

# Git contention error patterns (expected under concurrent writes).
_GIT_CONTENTION_RE = re.compile(
    r"(nothing to commit|index\.lock|exit status 1\b|exit status 128\b|non-zero exit status [12])",
    re.IGNORECASE,
)


def _is_contention_error(status_code, text):
    """True if the error is an expected git contention failure."""
    return status_code == 500 and bool(_GIT_CONTENTION_RE.search(text))


def _api_session():
    """Create a fresh requests session for a worker thread."""
    session = requests.Session()
    original_request = session.request

    def patched_request(method, url, **kwargs):
        if url.startswith("/"):
            url = BASE_URL + url
        return original_request(method, url, **kwargs)

    session.request = patched_request
    return session


def _create_set(name):
    api = _api_session()
    resp = api.post("/scriptlet/sets/create", json={"name": name})
    api.close()
    return resp


def _create_scriptlet(set_name, name, type_="task"):
    api = _api_session()
    resp = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "type": type_},
    )
    api.close()
    return resp


def _save_code(set_name, name, code):
    api = _api_session()
    resp = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": name, "code": code},
    )
    api.close()
    return resp


def _read_code(set_name, name):
    api = _api_session()
    resp = api.get(f"/scriptlet/inventory/code?set={set_name}&name={name}")
    api.close()
    return resp


def _save_draft(set_name, name, code):
    api = _api_session()
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": set_name, "name": name, "code": code},
    )
    api.close()
    return resp


def _enqueue_task(set_name, name, param=""):
    api = _api_session()
    resp = api.put(
        "/scriptlet/job/item",
        json={
            "set": set_name,
            "name": name,
            "drop_done": False,
            "max_retries": 0,
            "param": param,
            "kind": "task",
        },
    )
    api.close()
    return resp


def _get_job(job_id):
    api = _api_session()
    resp = api.get(f"/scriptlet/job/item?id={job_id}")
    api.close()
    return resp


def _wait_for_job(job_id, timeout=60):
    terminal = {"done", "failed", "cancelled", "killed"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _get_job(job_id)
        if resp.status_code == 200:
            job = resp.json()
            if job["state"] in terminal:
                return job
        time.sleep(0.5)
    return None


def _list_inventory():
    api = _api_session()
    resp = api.get("/scriptlet/inventory/list")
    api.close()
    return resp


# Allow patterns for server log assertion — git contention tracebacks.
_GIT_CONTENTION_LOG_ALLOW = [
    r"exit status",
    r"index\.lock",
    r"nothing to commit",
    r"perform_git_add_commit_push",
    r"item_put|item_post|item_del",
    r"CalledProcessError",
]


def _run_concurrent(workers, max_workers=5):
    """Run worker callables concurrently, return (successes, contention_errors, real_errors)."""
    successes = 0
    contention = []
    real_errors = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(w) for w in workers]
        for f in as_completed(futures):
            result = f.result()
            if result is None:
                successes += 1
            elif result.startswith("CONTENTION:"):
                contention.append(result)
            else:
                real_errors.append(result)
    return successes, contention, real_errors


def _check_result(resp, label):
    """Classify a response: None=ok, CONTENTION:...=expected, else real error."""
    if resp.status_code in (200, 204):
        return None
    if _is_contention_error(resp.status_code, resp.text):
        return f"CONTENTION: {label}: {resp.text}"
    return f"{label}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConcurrentScriptletOps:
    """Hammer the inventory API with concurrent creates, saves, reads."""

    def test_concurrent_creates_in_same_set(self, api, pyops_server):
        """Create many scriptlets in one set concurrently.

        Some may fail with git contention — verify most succeed and
        the final state is consistent.
        """
        set_name = f"{SET_PREFIX}_creates"
        api.post("/scriptlet/sets/create", json={"name": set_name})

        count = 10

        def worker(i):
            resp = _create_scriptlet(set_name, f"task_{i}")
            return _check_result(resp, f"task_{i}")

        workers = [lambda i=i: worker(i) for i in range(count)]
        successes, contention, errors = _run_concurrent(workers)

        assert not errors, f"Unexpected errors: {errors}"
        assert successes >= count // 2, (
            f"Too few successes: {successes}/{count} "
            f"(contention: {len(contention)})"
        )

        # Verify created scriptlets exist (some contention failures may still
        # have written the file but failed on commit).
        resp = _list_inventory()
        names = [s["name"] for s in resp.json() if s["set"] == set_name]
        assert len(names) >= successes

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )

    def test_concurrent_saves_same_scriptlet(self, api, pyops_server):
        """Concurrent committed saves to the same scriptlet.

        Under contention, git commit may fail with "nothing to commit"
        or index.lock errors.  The final file must be readable.
        """
        set_name = f"{SET_PREFIX}_saves"
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, "target")
        assert resp.status_code == 200

        count = 10

        def worker(i):
            code = f'print("version {i}")\n'
            resp = _save_code(set_name, "target", code)
            return _check_result(resp, f"save {i}")

        workers = [lambda i=i: worker(i) for i in range(count)]
        successes, contention, errors = _run_concurrent(workers)

        assert not errors, f"Unexpected errors: {errors}"
        # At least one save should succeed.
        assert successes >= 1, f"No saves succeeded (contention: {len(contention)})"

        # Final code must be readable and contain a version string.
        resp = _read_code(set_name, "target")
        assert resp.status_code == 200
        assert "version" in resp.json()["code"] or "pass" in resp.json()["code"]

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )

    def test_concurrent_drafts_same_scriptlet(self, api, pyops_server):
        """Concurrent draft saves (no git commit) should ALL succeed."""
        set_name = f"{SET_PREFIX}_drafts"
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, "drafted")
        assert resp.status_code == 200

        count = 15

        def worker(i):
            code = f'print("draft {i}")\n'
            resp = _save_draft(set_name, "drafted", code)
            return _check_result(resp, f"draft {i}")

        workers = [lambda i=i: worker(i) for i in range(count)]
        successes, contention, errors = _run_concurrent(workers)

        assert not errors, f"Draft errors: {errors}"
        assert successes == count, (
            f"Draft saves should all succeed: {successes}/{count}"
        )

        pyops_server.assert_no_unexpected_errors()

    def test_concurrent_reads_and_writes(self, api, pyops_server):
        """Mix reads and draft writes to the same scriptlet concurrently."""
        set_name = f"{SET_PREFIX}_rw"
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, "mixed")
        assert resp.status_code == 200
        _save_code(set_name, "mixed", 'print("initial")\n')

        count = 20

        def reader(i):
            resp = _read_code(set_name, "mixed")
            return _check_result(resp, f"read {i}")

        def writer(i):
            resp = _save_draft(set_name, "mixed", f'print("write {i}")\n')
            return _check_result(resp, f"write {i}")

        workers = []
        for i in range(count):
            if i % 3 == 0:
                workers.append(lambda i=i: writer(i))
            else:
                workers.append(lambda i=i: reader(i))

        successes, contention, errors = _run_concurrent(workers, max_workers=6)

        assert not errors, f"Read/write errors: {errors}"
        assert successes == count

        pyops_server.assert_no_unexpected_errors()

    def test_concurrent_creates_across_sets(self, api, pyops_server):
        """Create scriptlets across multiple sets concurrently."""
        set_count = 4
        scripts_per_set = 5

        for i in range(set_count):
            api.post("/scriptlet/sets/create", json={"name": f"{SET_PREFIX}_multi_{i}"})

        def worker(si, ti):
            set_name = f"{SET_PREFIX}_multi_{si}"
            resp = _create_scriptlet(set_name, f"t_{ti}")
            return _check_result(resp, f"{set_name}/t_{ti}")

        workers = [
            lambda si=si, ti=ti: worker(si, ti)
            for si in range(set_count)
            for ti in range(scripts_per_set)
        ]
        successes, contention, errors = _run_concurrent(workers, max_workers=8)

        assert not errors, f"Multi-set errors: {errors}"
        # Most should succeed — sets use independent locks.
        total = set_count * scripts_per_set
        assert successes >= total * 3 // 4, (
            f"Too few successes: {successes}/{total}"
        )

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )


class TestConcurrentJobExecution:
    """Run multiple jobs concurrently and verify all complete."""

    def _setup_task(self, api, set_name, task_name, code):
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, task_name)
        assert resp.status_code == 200
        resp = _save_code(set_name, task_name, code)
        assert resp.status_code == 200

    def test_concurrent_job_enqueue_and_run(self, api, pyops_server):
        """Enqueue multiple tasks at once; most should reach done state."""
        set_name = f"{SET_PREFIX}_jobs"
        self._setup_task(
            api,
            set_name,
            "worker",
            'import time; time.sleep(0.2); print("done")\n',
        )

        job_count = 6
        job_ids = []

        for i in range(job_count):
            resp = _enqueue_task(set_name, "worker", param=f'idx = {i}')
            assert resp.status_code == 200
            job_ids.append(resp.json()["job"])

        done_count = 0
        for jid in job_ids:
            job = _wait_for_job(jid, timeout=120)
            assert job is not None, f"Job {jid} did not finish"
            if job["state"] == "done":
                done_count += 1

        assert done_count == job_count, (
            f"Job failures: {done_count}/{job_count} done"
        )

        pyops_server.assert_no_unexpected_errors()

    def test_concurrent_jobs_and_inventory_ops(self, api, pyops_server):
        """Run jobs while also doing draft saves in parallel."""
        set_name = f"{SET_PREFIX}_mixed"
        self._setup_task(
            api,
            set_name,
            "busy",
            'import time; time.sleep(0.5); print("ok")\n',
        )
        resp = _create_scriptlet(set_name, "editable")
        assert resp.status_code == 200

        job_count = 3
        job_ids = []

        for i in range(job_count):
            resp = _enqueue_task(set_name, "busy")
            assert resp.status_code == 200
            job_ids.append(resp.json()["job"])

        # While jobs run, hammer draft saves (no commit, no git contention).
        draft_count = 10
        draft_errors = []

        def draft_worker(i):
            code = f'print("edit {i}")\n'
            resp = _save_draft(set_name, "editable", code)
            if resp.status_code != 200:
                return f"draft {i}: {resp.status_code} {resp.text}"
            return None

        workers = [lambda i=i: draft_worker(i) for i in range(draft_count)]
        successes, _, errors = _run_concurrent(workers, max_workers=4)
        assert not errors, f"Draft errors during job run: {errors}"

        for jid in job_ids:
            job = _wait_for_job(jid, timeout=120)
            assert job is not None, f"Job {jid} did not finish"
            assert job["state"] == "done", (
                f"Job {jid} state={job['state']}, stderr={job.get('stderr', '')}"
            )

        pyops_server.assert_no_unexpected_errors()

    def test_rapid_enqueue_same_task(self, api, pyops_server):
        """Rapidly enqueue the same task many times; all should complete."""
        set_name = f"{SET_PREFIX}_rapid"
        self._setup_task(
            api,
            set_name,
            "quick",
            'print("fast")\n',
        )

        job_count = 10
        job_ids = []

        for i in range(job_count):
            resp = _enqueue_task(set_name, "quick")
            assert resp.status_code == 200
            job_ids.append(resp.json()["job"])

        for jid in job_ids:
            job = _wait_for_job(jid, timeout=120)
            assert job is not None, f"Job {jid} did not finish"
            assert job["state"] == "done", (
                f"Job {jid} state={job['state']}, stderr={job.get('stderr', '')}"
            )

        pyops_server.assert_no_unexpected_errors()


class TestLockContention:
    """Specifically target repo_lock contention patterns."""

    def test_interleaved_commit_and_draft_same_file(self, api, pyops_server):
        """Alternate between committed saves and draft writes.

        Commits may fail under contention; drafts should all succeed.
        No deadlocks or crashes.
        """
        set_name = f"{SET_PREFIX}_lock"
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, "contested")
        assert resp.status_code == 200

        rounds = 8

        def commit_worker(i):
            resp = _save_code(set_name, "contested", f'print("commit {i}")\n')
            return _check_result(resp, f"commit {i}")

        def draft_worker(i):
            resp = _save_draft(set_name, "contested", f'print("draft {i}")\n')
            return _check_result(resp, f"draft {i}")

        workers = []
        for i in range(rounds):
            workers.append(lambda i=i: commit_worker(i))
            workers.append(lambda i=i: draft_worker(i))

        successes, contention, errors = _run_concurrent(workers, max_workers=4)

        assert not errors, f"Lock contention errors: {errors}"
        # Drafts should all succeed (rounds of them).
        # Some commits may fail under contention.
        assert successes >= rounds, (
            f"Expected at least {rounds} successes (drafts): got {successes}"
        )

        # File should still be readable.
        resp = _read_code(set_name, "contested")
        assert resp.status_code == 200

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )

    def test_concurrent_create_and_delete(self, api, pyops_server):
        """Create and delete scriptlets concurrently in the same set."""
        set_name = f"{SET_PREFIX}_cd"
        api.post("/scriptlet/sets/create", json={"name": set_name})

        for i in range(5):
            resp = _create_scriptlet(set_name, f"pre_{i}")
            assert resp.status_code == 200

        def creator(i):
            resp = _create_scriptlet(set_name, f"new_{i}")
            if resp.status_code in (200, 409):
                return None
            return _check_result(resp, f"create new_{i}")

        def deleter(i):
            api = _api_session()
            resp = api.delete(
                f"/scriptlet/inventory/item?set={set_name}&name=pre_{i}"
            )
            api.close()
            if resp.status_code in (200, 404):
                return None
            return _check_result(resp, f"delete pre_{i}")

        workers = []
        for i in range(5):
            workers.append(lambda i=i: creator(i))
            workers.append(lambda i=i: deleter(i))

        successes, contention, errors = _run_concurrent(workers, max_workers=4)

        assert not errors, f"Create/delete errors: {errors}"

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )

    def test_concurrent_operations_across_sets(self, api, pyops_server):
        """Operations on different sets use separate locks — should not interfere."""
        set_count = 3
        for i in range(set_count):
            api.post("/scriptlet/sets/create", json={"name": f"{SET_PREFIX}_iso_{i}"})
            resp = _create_scriptlet(f"{SET_PREFIX}_iso_{i}", "target")
            assert resp.status_code == 200

        rounds = 6

        def worker(si, ri):
            set_name = f"{SET_PREFIX}_iso_{si}"
            code = f'print("s{si}_r{ri}")\n'
            resp = _save_code(set_name, "target", code)
            return _check_result(resp, f"{set_name} r{ri}")

        workers = [
            lambda si=si, ri=ri: worker(si, ri)
            for si in range(set_count)
            for ri in range(rounds)
        ]
        successes, contention, errors = _run_concurrent(workers, max_workers=6)

        assert not errors, f"Cross-set errors: {errors}"
        # At least some saves per set should succeed.
        assert successes >= set_count, (
            f"Expected at least {set_count} successes: got {successes}"
        )

        # All sets should still be readable.
        for i in range(set_count):
            resp = _read_code(f"{SET_PREFIX}_iso_{i}", "target")
            assert resp.status_code == 200

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )

    def test_no_deadlock_under_sustained_load(self, api, pyops_server):
        """Sustained mixed operations complete within timeout (no deadlock)."""
        set_name = f"{SET_PREFIX}_deadlock"
        api.post("/scriptlet/sets/create", json={"name": set_name})
        resp = _create_scriptlet(set_name, "target")
        assert resp.status_code == 200

        total_ops = 30
        timeout_per_op = 30  # seconds

        def mixed_worker(i):
            if i % 3 == 0:
                resp = _save_code(set_name, "target", f'print("c{i}")\n')
            elif i % 3 == 1:
                resp = _save_draft(set_name, "target", f'print("d{i}")\n')
            else:
                resp = _read_code(set_name, "target")
            return _check_result(resp, f"op {i}")

        start = time.time()
        workers = [lambda i=i: mixed_worker(i) for i in range(total_ops)]
        successes, contention, errors = _run_concurrent(workers, max_workers=6)
        elapsed = time.time() - start

        assert not errors, f"Unexpected errors: {errors}"
        assert elapsed < timeout_per_op, (
            f"Operations took {elapsed:.1f}s — possible deadlock"
        )
        # Reads and drafts should all succeed, only commits may fail.
        expected_successes = total_ops - (total_ops // 3)  # reads + drafts
        assert successes >= expected_successes, (
            f"Too few successes: {successes} (expected >= {expected_successes})"
        )

        pyops_server.assert_no_unexpected_errors(
            allow=_GIT_CONTENTION_LOG_ALLOW
        )
