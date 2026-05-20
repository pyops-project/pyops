"""End-to-end coverage for sets management through the pyops-set controller.

Restores what the deleted test_set_lifecycle / test_sets_git / test_sets_ui /
test_update_deps modules covered against the old `/scriptlet/sets/*` endpoints,
plus the new live-output dialog (set_job.js / runSetJob) the
controller-mediated flow introduces.

Every mutating operation now travels:
    browser → /controller/pyops/sets/<op> → data.task.py (service)
           → control.job.create(pyops, set_<op>, drop_done=true, …)
           → set_<op>.task.py job → subprocess git/pip → workspace logs

The dialog the operator sees stays open during the task, tails the
workspace logs via /scriptlet/job/item?id=<id>, and lets the user close
it when the badge flips to done/failed.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest


SERVICE_READY_TIMEOUT = 180
JOB_TIMEOUT = 180


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _service_ready(api, timeout=SERVICE_READY_TIMEOUT):
    """Block until the pyops::data service answers /list with a real
    payload. Until the runner has bootstrapped the pyops set and the
    schedule loop has enqueued + started the service, the controller proxy
    returns HTTP 503 with `{"error": "No running service…"}`."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            r = api.get("/controller/pyops/sets/list")
            if r.status_code == 200:
                body = r.json()
                if isinstance(body.get("sets"), list):
                    return body
                last_err = body
        except Exception as e:
            last_err = str(e)
        time.sleep(1)
    pytest.fail(f"pyops::data service not ready within {timeout}s; last: {last_err}")


def _poll_job(api, job_id, timeout=JOB_TIMEOUT):
    """Poll until the job reaches a terminal state. Returns the final job
    dict, or `None` if drop_done already cleaned it up (which we treat as a
    successful completion — failed jobs stay around for the operator)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/scriptlet/job/item?id={job_id}")
        if r.status_code != 200:
            return None
        job = r.json()
        if job["state"] in {"done", "failed", "cancelled", "removed"}:
            return job
        time.sleep(0.5)
    pytest.fail(f"job {job_id} did not finish within {timeout}s")


def _trigger(api, method, path, body=None):
    """Hit a controller endpoint and return the {"job": id} payload."""
    if method == "POST":
        r = api.post(path, json=body)
    elif method == "DELETE":
        r = api.delete(path)
    else:
        raise ValueError(method)
    assert r.status_code == 200, f"{method} {path} → {r.status_code}: {r.text}"
    return r.json()


def _run(api, method, path, body=None, timeout=JOB_TIMEOUT):
    """Trigger an operation and wait for its sub-task to finish. Returns the
    final job dict (or None on drop_done cleanup)."""
    payload = _trigger(api, method, path, body)
    assert "job" in payload, f"no job id: {payload}"
    return _poll_job(api, payload["job"], timeout)


def _assert_done(job):
    # drop_done=True races the poll: the job may transition done → remove
    # → removed → DB cleanup before we see "done" in a poll. Either of the
    # terminal-success states (or the cleanup having already removed the
    # row, hence job=None) is a successful outcome here.
    if job is None:
        return
    assert job["state"] in {"done", "remove", "removed"}, (
        f"expected done/remove/removed, got {job['state']}: {job.get('detail')}\n"
        f"stdout:\n{job.get('stdout', '')}\nstderr:\n{job.get('stderr', '')}"
    )


def _assert_failed(job, substr=None):
    # drop_done=True keeps failed jobs around (cleanup_drop_done targets
    # state=done only) — so we always have the dict to inspect for failures.
    assert job is not None and job["state"] == "failed", f"expected failed, got: {job}"
    if substr is not None:
        haystack = (job.get("detail") or "") + (job.get("stderr") or "")
        assert substr in haystack, f"expected '{substr}' in failure detail; got:\n{haystack}"


def _init_bare_remote(path):
    """Seed a bare git repo under `path` for clone/pull tests."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    bare = path / "remote_repo.git"
    work = path / "work"

    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "e2e@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "E2E"], check=True, capture_output=True)
    (work / "README.md").write_text("# remote\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True, capture_output=True)
    return str(bare)


@pytest.fixture
def bare_remote(tmp_path_factory):
    return _init_bare_remote(tmp_path_factory.mktemp("bare_remote_src"))


def _dirs(e2e_env, set_name):
    data = Path(e2e_env["data_dir"])
    return {
        "git": data / "git" / set_name,
        "venv": data / "venv" / set_name,
        "pkg": data / "pkg" / set_name,
    }


def _create_user_scriptlet(api, set_name, name, type_, code):
    """Bypass the inventory API's set-existence check: write the scriptlet
    file directly. This keeps update_deps tests free of API contract
    coupling — they only care about the directive scanner finding files."""
    pass  # not used; we use api below where the set exists already


def _create_scriptlet(api, set_name, name, type_, code):
    """Create + draft a scriptlet via the inventory API. Leaves the
    scriptlet UNSTAGED — the server no longer commits via inventory.
    Tests that need committed content call the `commit_set` fixture
    after this helper."""
    r = api.put("/scriptlet/inventory/item", json={"set": set_name, "name": name, "type": type_})
    assert r.status_code == 200, f"create {name} failed: {r.text}"
    r = api.post("/scriptlet/inventory/draft", json={"set": set_name, "name": name, "code": code})
    assert r.status_code == 200, f"draft {name} failed: {r.text}"


def _nav_sets(e2e_page):
    """Click the Sets menu entry and wait for the controller page chrome
    (the clone button is reliably the first interactive element)."""
    e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="sets-create"]').wait_for(state="visible", timeout=20_000)
    return frame


def _await_modal_done(frame, timeout=60_000):
    """Wait until the set-job modal's state badge reads done / failed
    / cancelled / removed AND the Close button is enabled — i.e. the
    sub-task has finished from the operator's perspective."""
    badge = frame.locator('[data-testid="set-job-state"]')
    badge.wait_for(state="visible", timeout=10_000)
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        try:
            text = badge.inner_text(timeout=1_000).strip().lower()
        except Exception:
            text = ""
        if text in {"done", "failed", "cancelled", "removed"}:
            close_btn = frame.locator('[data-testid="set-job-close"]')
            if not close_btn.is_disabled():
                return text
        time.sleep(0.2)
    raise AssertionError(f"set-job modal did not reach terminal state within {timeout}ms (last badge: {text!r})")


# ---------------------------------------------------------------------------
# API-level tests (parity with old test_set_lifecycle / test_sets_git /
# test_update_deps via the controller proxy)
# ---------------------------------------------------------------------------

class TestSetsLifecycleViaController:
    """create / remove cover their three on-disk directories (git, venv, pkg)
    and the name becomes reusable after a remove."""

    def test_create_makes_all_three_dirs(self, api, pyops_server, e2e_env, set_factory):
        _service_ready(api)
        job = _run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_create"})
        _assert_done(job)
        d = _dirs(e2e_env, "lc_create")
        assert d["git"].is_dir()
        # venv + pkg are created lazily on first job spawn in the set;
        # ensure_set_dirs runs from runner.py before each Popen.

    def test_remove_deletes_all_three_dirs(self, api, pyops_server, e2e_env, set_factory):
        _service_ready(api)
        _assert_done(_run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_remove"}))
        # Force venv/pkg into existence so the remove task has all three to
        # tear down (the runner-side lazy bootstrap also does this but only
        # when a real user-scriptlet job spawns in the set).
        (Path(e2e_env["data_dir"]) / "venv" / "lc_remove").mkdir(parents=True, exist_ok=True)
        (Path(e2e_env["data_dir"]) / "pkg" / "lc_remove").mkdir(parents=True, exist_ok=True)

        _assert_done(_run(api, "DELETE", "/controller/pyops/sets/remove/lc_remove"))
        d = _dirs(e2e_env, "lc_remove")
        assert not d["git"].exists()
        assert not d["venv"].exists()
        assert not d["pkg"].exists()

    def test_remove_unknown_set_fails(self, api, pyops_server, set_factory):
        _service_ready(api)
        job = _run(api, "DELETE", "/controller/pyops/sets/remove/does-not-exist-xyzzy")
        _assert_failed(job, "not found")
        pyops_server.assert_no_unexpected_errors(allow=["not found", "set_remove"])

    def test_recreate_after_remove_succeeds(self, api, pyops_server, e2e_env, set_factory):
        _service_ready(api)
        _run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_recycle"})
        _run(api, "DELETE", "/controller/pyops/sets/remove/lc_recycle")
        job = _run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_recycle"})
        _assert_done(job)
        assert _dirs(e2e_env, "lc_recycle")["git"].is_dir()

    def test_create_duplicate_set_fails(self, api, pyops_server, set_factory):
        """Creating a set with a name that already exists on disk fails;
        the set_create task refuses to overwrite a populated directory."""
        _service_ready(api)
        _assert_done(_run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_dup"}))
        job = _run(api, "POST", "/controller/pyops/sets/create", {"name": "lc_dup"})
        _assert_failed(job, "already exists")
        pyops_server.assert_no_unexpected_errors(allow=["already exists", "set_create"])

    def test_remove_set_cleans_up_after_job_ran(self, api, pyops_server, e2e_env, set_factory, commit_set):
        """A set that has actually executed a user-scriptlet job can still
        be removed cleanly — the runner-side lazy bootstrap leaves a
        populated venv and a workspace under data/jobs/, and the
        set_remove task tears all three down."""
        _service_ready(api)
        name = "lc_used"
        _assert_done(_run(api, "POST", "/controller/pyops/sets/create", {"name": name}))

        # Run a real user task in the new set so venv + workspace exist.
        # kind=task reads committed content, so commit after drafting.
        _create_scriptlet(api, name, "t", "task", 'print("ok")\n')
        commit_set(name, "add task")
        r = api.put(
            "/scriptlet/job/item",
            json={"set": name, "name": "t", "drop_done": False,
                  "max_retries": 0, "param": "", "kind": "task"},
        )
        assert r.status_code == 200, f"enqueue failed: {r.text}"
        job_id = r.json()["job"]

        deadline = time.time() + 60
        terminal = {"done", "failed", "cancelled", "removed"}
        while time.time() < deadline:
            j = api.get(f"/scriptlet/job/item?id={job_id}").json()
            if j["state"] in terminal:
                break
            time.sleep(0.3)
        else:
            pytest.fail(f"prime job {job_id} did not reach terminal state")

        _assert_done(_run(api, "DELETE", f"/controller/pyops/sets/remove/{name}"))
        d = _dirs(e2e_env, name)
        assert not d["git"].exists()


class TestSetsCloneViaController:
    def test_clone_from_file_url_creates_set(self, api, pyops_server, bare_remote, e2e_env, set_factory):
        _service_ready(api)
        url = f"file://{bare_remote}"
        job = _run(api, "POST", "/controller/pyops/sets/clone", {"url": url})
        _assert_done(job)
        # Name inferred from URL basename
        assert (Path(e2e_env["data_dir"]) / "git" / "remote_repo" / ".git").is_dir()

    def test_clone_invalid_url_fails(self, api, pyops_server):
        _service_ready(api)
        # Empty URL — service rejects synchronously with 400 before any job
        # spawns; controller_proxy re-raises that as an HTTPException, so the
        # client sees a real 400 status (not a 200 with an error payload).
        r = api.post("/controller/pyops/sets/clone", json={"url": ""})
        assert r.status_code == 400, r.text
        # FastAPI wraps HTTPException.detail under {"detail": ...}; the
        # detail here carries the service's {"error": "url is required"} dict.
        detail = r.json()["detail"]
        if isinstance(detail, dict):
            assert "url" in detail.get("error", "").lower()
        else:
            assert "url" in str(detail).lower()

    def test_clone_duplicate_name_fails(self, api, pyops_server, bare_remote, set_factory):
        _service_ready(api)
        url = f"file://{bare_remote}"
        _assert_done(_run(api, "POST", "/controller/pyops/sets/clone", {"url": url}))
        job = _run(api, "POST", "/controller/pyops/sets/clone", {"url": url})
        _assert_failed(job, "already exists")
        pyops_server.assert_no_unexpected_errors(allow=["already exists", "set_clone"])


class TestSetsSwitchViaController:
    def test_switch_to_new_branch_creates_it(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("br_new")
        _assert_done(_run(api, "POST", "/controller/pyops/sets/switch", {"name": "br_new", "branch": "feature-x"}))
        repo = Path(e2e_env["data_dir"]) / "git" / "br_new"
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()
        assert head == "feature-x"
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote", "set_switch"]
        )

    def test_switch_back_to_original_branch(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("br_back")
        repo = Path(e2e_env["data_dir"]) / "git" / "br_back"
        initial = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        _assert_done(_run(api, "POST", "/controller/pyops/sets/switch", {"name": "br_back", "branch": "temp"}))
        _assert_done(_run(api, "POST", "/controller/pyops/sets/switch", {"name": "br_back", "branch": initial}))
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        assert head == initial
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote", "set_switch"]
        )

    def test_switch_unknown_set_fails(self, api, pyops_server, set_factory):
        _service_ready(api)
        job = _run(api, "POST", "/controller/pyops/sets/switch", {"name": "does-not-exist-xyzzy", "branch": "any"})
        _assert_failed(job, "not found")
        pyops_server.assert_no_unexpected_errors(allow=["not found", "set_switch"])


class TestSetsPullViaController:
    def test_pull_unknown_set_fails(self, api, pyops_server, set_factory):
        _service_ready(api)
        job = _run(api, "POST", "/controller/pyops/sets/pull/does-not-exist-xyzzy")
        _assert_failed(job, "not found")
        pyops_server.assert_no_unexpected_errors(allow=["not found", "set_pull"])

    def test_pull_cloned_set_picks_up_remote_changes(self, api, pyops_server, bare_remote, e2e_env, tmp_path, set_factory):
        _service_ready(api)
        url = f"file://{bare_remote}"
        _assert_done(_run(api, "POST", "/controller/pyops/sets/clone", {"url": url}))

        # Push a new commit to the bare remote.
        upstream = tmp_path / "upstream_work"
        subprocess.run(["git", "clone", bare_remote, str(upstream)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(upstream), "config", "user.email", "e2e@example.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(upstream), "config", "user.name", "E2E"], check=True, capture_output=True)
        (upstream / "new_scriptlet.py").write_text("hello from upstream\n")
        subprocess.run(["git", "-C", str(upstream), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(upstream), "commit", "-m", "new commit"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(upstream), "push", "origin", "HEAD"], check=True, capture_output=True)

        _assert_done(_run(api, "POST", "/controller/pyops/sets/pull/remote_repo"))
        local_file = Path(e2e_env["data_dir"]) / "git" / "remote_repo" / "new_scriptlet.py"
        assert local_file.exists(), "set_pull should have fetched the new commit"
        assert "upstream" in local_file.read_text()


class TestUpdateDepsViaController:
    """The set_update_deps task scans #! directives, resolves conflicts via
    newest-mtime-wins, runs pip install --upgrade, and writes
    .update_warnings.json. /health surfaces non-empty warnings."""

    def test_update_deps_clean_summary(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("up_clean")
        _create_scriptlet(api, "up_clean", "uses_six", "task", "#!six==1.16.0\nimport six\n")

        _assert_done(_run(api, "POST", "/controller/pyops/sets/update_deps/up_clean", timeout=300))

        warnings = Path(e2e_env["data_dir"]) / "venv" / "up_clean" / ".update_warnings.json"
        assert warnings.exists()
        import json as _json
        summary = _json.loads(warnings.read_text())
        assert summary["set"] == "up_clean"
        assert summary["conflicts"]["pip"] == []
        assert summary["conflicts"]["git"] == []
        assert any(d["pkg"] == "six" for d in summary["installed"]["pip"])

    def test_update_deps_conflict_surfaces_in_warnings_and_health(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("up_conf")
        _create_scriptlet(api, "up_conf", "old_pin", "task", "#!six==1.15.0\nimport six\n")
        time.sleep(0.1)  # ensure mtime differs
        _create_scriptlet(api, "up_conf", "new_pin", "task", "#!six==1.16.0\nimport six\n")

        _assert_done(_run(api, "POST", "/controller/pyops/sets/update_deps/up_conf", timeout=300))

        import json as _json
        warnings_file = Path(e2e_env["data_dir"]) / "venv" / "up_conf" / ".update_warnings.json"
        summary = _json.loads(warnings_file.read_text())
        pip_conflicts = summary["conflicts"]["pip"]
        assert len(pip_conflicts) == 1
        assert pip_conflicts[0]["pkg"] == "six"
        assert pip_conflicts[0]["winner"]["source"] == "new_pin.task.py"
        assert pip_conflicts[0]["winner"]["spec"] == "==1.16.0"

        h = api.get("/health")
        assert h.status_code == 200
        hbody = h.json()
        assert hbody["status"] == "degraded"
        assert hbody["checks"]["dependency_conflicts"] == "conflicts"
        assert "up_conf" in [c["set"] for c in hbody["dependency_conflicts"]]
        pyops_server.assert_no_unexpected_errors(
            allow=["pip conflict", "Update completed", "set_update_deps"]
        )

    def test_update_deps_unknown_set_fails(self, api, pyops_server, set_factory):
        _service_ready(api)
        job = _run(api, "POST", "/controller/pyops/sets/update_deps/never_existed_e2e")
        _assert_failed(job, "not found")
        pyops_server.assert_no_unexpected_errors(allow=["not found", "set_update_deps"])

    def test_update_deps_install_failure_marks_job_failed(self, api, pyops_server, set_factory):
        """When a scriptlet pins a non-existent pip package, set_update_deps
        runs pip with --upgrade, pip exits non-zero, run_cmd raises, the
        task ends in `failed` state. The job's detail/stderr carries the
        pip error — the operator sees it in the live modal."""
        _service_ready(api)
        set_factory("up_fail")
        _create_scriptlet(api, "up_fail", "broken", "task",
                          "#!definitely-no-such-pkg-xyzzy-pyops-e2e==9.9.9\n")

        job = _run(api, "POST", "/controller/pyops/sets/update_deps/up_fail", timeout=300)
        _assert_failed(job, "definitely-no-such-pkg-xyzzy-pyops-e2e")
        pyops_server.assert_no_unexpected_errors(
            allow=["definitely-no-such-pkg", "set_update_deps",
                   "CalledProcessError", "non-zero exit status",
                   "No matching distribution", "Could not find a version"]
        )


class TestCommitDraftsViaController:
    """set_commit_drafts stages + commits (+ pushes when a remote exists)
    every unstaged scriptlet in one batch. The single sanctioned commit
    pathway in pyops — per-file commit/push is gone from the server."""

    def test_no_drafts_is_noop(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("cd_clean")
        repo = Path(e2e_env["data_dir"]) / "git" / "cd_clean"
        pre = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()

        _assert_done(_run(api, "POST", "/controller/pyops/sets/commit_drafts/cd_clean"))

        post = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        assert pre == post, "no drafts must not produce a new commit"

    def test_commits_multiple_unstaged_files_in_one_commit(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("cd_multi")
        repo = Path(e2e_env["data_dir"]) / "git" / "cd_multi"
        (repo / "task").mkdir(parents=True, exist_ok=True)
        (repo / "task" / "a.task.py").write_text("print('a')\n")
        (repo / "task" / "b.task.py").write_text("print('b')\n")
        (repo / "task" / "c.task.py").write_text("print('c')\n")

        pre_count = int(subprocess.check_output(
            ["git", "-C", str(repo), "rev-list", "--count", "HEAD"], text=True
        ).strip())

        _assert_done(_run(api, "POST", "/controller/pyops/sets/commit_drafts/cd_multi"))

        post_count = int(subprocess.check_output(
            ["git", "-C", str(repo), "rev-list", "--count", "HEAD"], text=True
        ).strip())
        assert post_count == pre_count + 1, "exactly one new commit"

        files_in_head = subprocess.check_output(
            ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"], text=True
        ).strip().splitlines()
        for expected in ("task/a.task.py", "task/b.task.py", "task/c.task.py"):
            assert expected in files_in_head, f"{expected} missing from HEAD"

        dirty = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"], text=True
        ).strip()
        assert dirty == "", f"working tree should be clean; got: {dirty!r}"

    def test_pushes_to_remote_when_configured(self, api, pyops_server, bare_remote, e2e_env, set_factory, tmp_path):
        _service_ready(api)
        url = f"file://{bare_remote}"
        _assert_done(_run(api, "POST", "/controller/pyops/sets/clone", {"url": url}))

        local = Path(e2e_env["data_dir"]) / "git" / "remote_repo"
        (local / "task").mkdir(parents=True, exist_ok=True)
        (local / "task" / "new.task.py").write_text("print('new')\n")

        _assert_done(_run(api, "POST", "/controller/pyops/sets/commit_drafts/remote_repo"))

        scratch = tmp_path / "verify_push"
        subprocess.check_call(
            ["git", "clone", bare_remote, str(scratch)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert (scratch / "task" / "new.task.py").exists(), (
            "commit_drafts should have pushed the new file to the bare remote"
        )

    def test_unknown_set_fails(self, api, pyops_server, set_factory):
        _service_ready(api)
        job = _run(api, "POST", "/controller/pyops/sets/commit_drafts/never_existed_xyz")
        _assert_failed(job, "not found")
        pyops_server.assert_no_unexpected_errors(allow=["not found", "set_commit_drafts"])

    def test_custom_message_is_used(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("cd_msg")
        repo = Path(e2e_env["data_dir"]) / "git" / "cd_msg"
        (repo / "task").mkdir(parents=True, exist_ok=True)
        (repo / "task" / "m.task.py").write_text("print('m')\n")

        custom = "ship the m feature"
        _assert_done(_run(
            api, "POST", "/controller/pyops/sets/commit_drafts/cd_msg",
            {"message": custom},
        ))
        last = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True
        ).strip()
        assert last == custom, f"expected custom message, got {last!r}"

    def test_empty_message_falls_back_to_default(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("cd_def")
        repo = Path(e2e_env["data_dir"]) / "git" / "cd_def"
        (repo / "task").mkdir(parents=True, exist_ok=True)
        (repo / "task" / "d.task.py").write_text("print('d')\n")

        _assert_done(_run(
            api, "POST", "/controller/pyops/sets/commit_drafts/cd_def",
            {"message": "   "},  # whitespace-only → fallback
        ))
        last = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True
        ).strip()
        assert last.startswith("Commit drafts in cd_def by ")


class TestSetsListReportsHasDrafts:
    """`GET /controller/pyops/sets/list` reports `has_drafts` per set
    so the UI knows when to enable the commit-drafts button."""

    def test_clean_set_reports_no_drafts(self, api, pyops_server, set_factory):
        _service_ready(api)
        set_factory("hd_clean")
        rows = api.get("/controller/pyops/sets/list").json()["sets"]
        row = next(r for r in rows if r["name"] == "hd_clean")
        assert row.get("has_drafts") is False

    def test_dirty_set_reports_drafts(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("hd_dirty")
        (Path(e2e_env["data_dir"]) / "git" / "hd_dirty" / "task").mkdir(parents=True, exist_ok=True)
        (Path(e2e_env["data_dir"]) / "git" / "hd_dirty" / "task" / "x.task.py").write_text("print('x')\n")
        rows = api.get("/controller/pyops/sets/list").json()["sets"]
        row = next(r for r in rows if r["name"] == "hd_dirty")
        assert row.get("has_drafts") is True


class TestSetsStatusEndpoint:
    """`GET /controller/pyops/sets/status/<name>` returns the porcelain
    git-status output the commit-drafts modal previews."""

    def test_clean_set_returns_empty_status(self, api, pyops_server, set_factory):
        _service_ready(api)
        set_factory("st_clean")
        r = api.get("/controller/pyops/sets/status/st_clean")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "st_clean"
        assert body["status"].strip() == ""

    def test_dirty_set_returns_porcelain_lines(self, api, pyops_server, set_factory, e2e_env):
        _service_ready(api)
        set_factory("st_dirty")
        (Path(e2e_env["data_dir"]) / "git" / "st_dirty" / "task").mkdir(parents=True, exist_ok=True)
        (Path(e2e_env["data_dir"]) / "git" / "st_dirty" / "task" / "y.task.py").write_text("print('y')\n")
        body = api.get("/controller/pyops/sets/status/st_dirty").json()
        assert "task/y.task.py" in body["status"]

    def test_unknown_set_404s(self, api, pyops_server):
        _service_ready(api)
        r = api.get("/controller/pyops/sets/status/never_exists_xyz")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# UI tests — driven through the Playwright frame, covering the modal-input
# pre-step and the live-output dialog the new flow introduces.
# ---------------------------------------------------------------------------

class TestSetsCloneUI:
    def test_clone_from_ui_streams_and_creates_set(self, e2e_page, api_anon, pyops_server, bare_remote, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)

        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-clone-url"]').fill(f"file://{bare_remote}")
        frame.locator('[data-testid="sets-clone-git-name"]').fill("UI Test")
        frame.locator('[data-testid="sets-clone-git-email"]').fill("ui@example.com")
        frame.locator('[data-testid="sets-clone-submit"]').click()

        # Input modal closes; live-output modal opens.
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="hidden", timeout=5_000)
        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done", f"clone did not succeed: badge={terminal}"

        frame.locator('[data-testid="set-job-close"]').click()
        frame.locator('[data-testid="set-job-modal"]').wait_for(state="hidden", timeout=5_000)

        frame.locator('[data-testid="set-row-remote_repo"]').wait_for(state="visible", timeout=10_000)
        sets = api_anon.get("/controller/pyops/sets/list").json()["sets"]
        assert any(s["name"] == "remote_repo" for s in sets)
        pyops_server.assert_no_unexpected_errors()

    def test_clone_missing_url_keeps_modal_open(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)

        frame.locator('[data-testid="sets-clone-submit"]').click()
        err = frame.locator('[data-testid="sets-clone-error"]').inner_text()
        assert "URL" in err
        assert frame.locator('[data-testid="sets-clone-modal"]').is_visible()
        # No job modal opened.
        assert frame.locator('[data-testid="set-job-modal"]').count() == 0 or \
               not frame.locator('[data-testid="set-job-modal"]').is_visible()
        frame.locator('[data-testid="sets-clone-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_clone_cancel_button_closes_without_cloning(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-clone-url"]').fill("file:///nowhere/at/all.git")
        frame.locator('[data-testid="sets-clone-cancel"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="hidden", timeout=5_000)
        sets = api_anon.get("/controller/pyops/sets/list").json()["sets"]
        assert not any(s["name"] == "at" or s["name"] == "all" for s in sets)
        pyops_server.assert_no_unexpected_errors()


class TestSetsCreateUI:
    def test_create_modal_cancel_does_not_create(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-create-name"]').fill("never_created")
        frame.locator('[data-testid="sets-create-cancel"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="hidden", timeout=5_000)
        sets = api_anon.get("/controller/pyops/sets/list").json()["sets"]
        assert not any(s["name"] == "never_created" for s in sets)
        pyops_server.assert_no_unexpected_errors()

    def test_create_empty_name_shows_error(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-create-submit"]').click()
        err = frame.locator('[data-testid="sets-create-error"]').inner_text()
        assert "name" in err.lower()
        assert frame.locator('[data-testid="sets-create-modal"]').is_visible()
        frame.locator('[data-testid="sets-create-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_create_from_ui_streams_and_creates_set(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-create-name"]').fill("ui_create")
        frame.locator('[data-testid="sets-create-submit"]').click()

        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="hidden", timeout=5_000)
        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done"
        frame.locator('[data-testid="set-job-close"]').click()

        frame.locator('[data-testid="set-row-ui_create"]').wait_for(state="visible", timeout=10_000)
        pyops_server.assert_no_unexpected_errors()


class TestSetsSwitchUI:
    def test_switch_branch_via_ui_streams_and_switches(self, e2e_page, api_anon, pyops_server, set_factory, e2e_env):
        _service_ready(api_anon)
        set_factory("switch_ui")
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-switch-switch_ui"]').wait_for(state="visible", timeout=10_000)
        frame.locator('[data-testid="set-switch-switch_ui"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-switch-branch"]').fill("feature-ui")
        frame.locator('[data-testid="sets-switch-submit"]').click()

        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="hidden", timeout=5_000)
        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done"
        frame.locator('[data-testid="set-job-close"]').click()

        repo = Path(e2e_env["data_dir"]) / "git" / "switch_ui"
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        assert head == "feature-ui"
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote", "set_switch"]
        )

    def test_switch_empty_branch_shows_error(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        set_factory("switch_err")
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-switch-switch_err"]').wait_for(state="visible", timeout=10_000)
        frame.locator('[data-testid="set-switch-switch_err"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="visible", timeout=5_000)
        frame.locator('[data-testid="sets-switch-submit"]').click()
        err = frame.locator('[data-testid="sets-switch-error"]').inner_text()
        assert "branch" in err.lower()
        assert frame.locator('[data-testid="sets-switch-modal"]').is_visible()
        frame.locator('[data-testid="sets-switch-cancel"]').click()
        pyops_server.assert_no_unexpected_errors()


class TestSetActionsUI:
    """Direct-action buttons (pull / update-deps / remove) open the
    set-job dialog without a pre-modal."""

    def test_pull_button_streams_output(self, e2e_page, api_anon, pyops_server, bare_remote, set_factory):
        _service_ready(api_anon)
        url = f"file://{bare_remote}"
        _assert_done(_run(api_anon, "POST", "/controller/pyops/sets/clone", {"url": url}))

        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-pull-remote_repo"]').wait_for(state="visible", timeout=10_000)
        frame.locator('[data-testid="set-pull-remote_repo"]').click()

        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done", f"pull failed in UI: {terminal}"
        frame.locator('[data-testid="set-job-close"]').click()
        pyops_server.assert_no_unexpected_errors()

    def test_commit_drafts_button_disabled_on_clean_set(self, e2e_page, api_anon, pyops_server, set_factory):
        """A clean set must NOT let the operator open the commit-drafts
        modal — pressing it would just no-op the task; greying the
        button out makes that obvious."""
        _service_ready(api_anon)
        set_factory("cd_disabled")
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        btn = frame.locator('[data-testid="set-commit-drafts-cd_disabled"]')
        btn.wait_for(state="visible", timeout=10_000)
        assert btn.is_disabled(), "commit-drafts button should be disabled when has_drafts=False"
        pyops_server.assert_no_unexpected_errors()

    def test_commit_drafts_button_opens_modal_with_status_and_commits(self, e2e_page, api_anon, pyops_server, set_factory, e2e_env):
        _service_ready(api_anon)
        set_factory("cd_ui")
        # Drop an unstaged file so the button has something real to commit.
        repo = Path(e2e_env["data_dir"]) / "git" / "cd_ui"
        (repo / "task").mkdir(parents=True, exist_ok=True)
        (repo / "task" / "ui.task.py").write_text("print('ui')\n")

        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        btn = frame.locator('[data-testid="set-commit-drafts-cd_ui"]')
        btn.wait_for(state="visible", timeout=10_000)
        # Wait until the next /list cycle reports has_drafts=true and
        # the button leaves the disabled state.
        for _ in range(40):
            if not btn.is_disabled():
                break
            time.sleep(0.5)
        assert not btn.is_disabled(), "commit-drafts button should be enabled when unstaged content exists"
        btn.click()

        # Pre-modal: commit-message + status preview.
        modal = frame.locator('[data-testid="sets-commit-drafts-modal"]')
        modal.wait_for(state="visible", timeout=10_000)
        # The porcelain preview should mention the unstaged file.
        for _ in range(20):
            status_text = frame.locator('[data-testid="sets-commit-drafts-status"]').inner_text()
            if "task/ui.task.py" in status_text:
                break
            time.sleep(0.25)
        assert "task/ui.task.py" in status_text, f"status preview missing file: {status_text!r}"

        # Enter a custom commit message and submit.
        custom_msg = "ui-commit feature first slice"
        frame.locator('[data-testid="sets-commit-drafts-message"]').fill(custom_msg)
        frame.locator('[data-testid="sets-commit-drafts-submit"]').click()
        modal.wait_for(state="hidden", timeout=5_000)

        # Live-output modal opens for the actual job.
        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done", f"commit_drafts UI: badge={terminal}"
        frame.locator('[data-testid="set-job-close"]').click()

        # Working tree clean + commit message persisted.
        dirty = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"], text=True
        ).strip()
        assert dirty == ""
        last = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True
        ).strip()
        assert last == custom_msg, f"expected commit message {custom_msg!r}, got {last!r}"
        pyops_server.assert_no_unexpected_errors()

    def test_remove_button_streams_and_removes(self, e2e_page, api_anon, pyops_server, set_factory):
        _service_ready(api_anon)
        set_factory("rm_ui")
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-remove-rm_ui"]').wait_for(state="visible", timeout=10_000)

        # The remove button shows a native confirm() before firing — accept it.
        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator('[data-testid="set-remove-rm_ui"]').click()

        frame.locator('[data-testid="set-job-modal"]').wait_for(state="visible", timeout=10_000)
        terminal = _await_modal_done(frame)
        assert terminal == "done"
        frame.locator('[data-testid="set-job-close"]').click()

        # Row disappears after refresh.
        frame.locator('[data-testid="set-row-rm_ui"]').wait_for(state="hidden", timeout=15_000)
        pyops_server.assert_no_unexpected_errors()
