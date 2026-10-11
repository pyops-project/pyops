"""
Inventory edge-case API E2E tests — endpoints not covered by smoke/editor flows.

Covers:
  - GET    /scriptlet/inventory/sets     list of sets (repos with .git)
  - GET    /scriptlet/inventory/status   unstaged-changes flag
  - GET    /repo/status                  repo branch / tag info
  - DELETE /scriptlet/inventory/draft    discard unstaged changes to HEAD
  - POST   /scriptlet/inventory/draft    save draft without commit (roundtrip)
  - invalid set names ("", "..", NUL byte) rejected with 400
  - unknown set on job / schedule PUT answered with 404

Smoke test_smoke.py covers /sets/list (which returns {name, branch}); this file
covers the separate /inventory/sets endpoint that returns just names.
"""

import pytest

from tests.e2e.conftest import expand_set


SET = "inv_api_e2e"
TASK = "my_task"

BASE_CODE = 'print("base")\n'
DRAFT_CODE = 'print("draft-unsaved")\n'


def _setup_task(api, set_factory, commit_set, set_name=SET, task_name=TASK, code=BASE_CODE):
    """Create a scriptlet, draft the code, then commit via subprocess.

    The server no longer commits via the inventory API — only the
    `pyops::set_commit_drafts` task does. For tests that need a
    "committed baseline" (e.g. `has_unstaged` flipping), the test-side
    `commit_set` fixture bakes the draft into HEAD directly."""
    set_factory(set_name)
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "type": "task"},
    )
    assert r.status_code == 200, f"create failed: {r.text}"
    r = api.post(
        "/scriptlet/inventory/draft",
        json={"set": set_name, "name": task_name, "code": code},
    )
    assert r.status_code == 200, f"draft failed: {r.text}"
    commit_set(set_name, f"add {task_name}")


class TestInventorySets:
    """GET /scriptlet/inventory/sets — list committed set names."""

    def test_empty_server_returns_empty_sets(self, api, pyops_server):
        r = api.get("/scriptlet/inventory/sets")
        assert r.status_code == 200
        assert r.json() == {"sets": []}
        pyops_server.assert_no_unexpected_errors()

    def test_lists_created_sets_sorted(self, api, pyops_server, set_factory):
        set_factory("zeta")
        set_factory("alpha")
        set_factory("mid")

        r = api.get("/scriptlet/inventory/sets")
        assert r.status_code == 200
        sets = r.json()["sets"]
        # Endpoint must return sorted names.
        assert sets == sorted(sets)
        assert "alpha" in sets and "mid" in sets and "zeta" in sets
        pyops_server.assert_no_unexpected_errors()


class TestRepoStatus:
    """GET /repo/status — branch / tag info for a given set."""

    def test_fresh_repo_has_master_or_main_branch(self, api, pyops_server, set_factory):
        set_factory(SET)
        r = api.get(f"/repo/status?set={SET}")
        assert r.status_code == 200
        status = r.json()
        assert "branch" in status
        # PyOps init'ed repo is on whatever Git's init.defaultBranch is; accept both common values.
        assert status["branch"] in ("master", "main"), f"unexpected branch: {status}"
        pyops_server.assert_no_unexpected_errors()


class TestInventoryStatus:
    """GET /scriptlet/inventory/status — has_unstaged flag."""

    def test_committed_scriptlet_has_no_unstaged(self, api, pyops_server, set_factory, commit_set):
        """Right after a baseline commit, has_unstaged is False."""
        _setup_task(api, set_factory, commit_set)
        r = api.get(f"/scriptlet/inventory/status?set={SET}&name={TASK}")
        assert r.status_code == 200
        assert r.json() == {"has_unstaged": False}
        pyops_server.assert_no_unexpected_errors()

    def test_draft_write_sets_unstaged_true(self, api, pyops_server, set_factory, commit_set):
        """After /inventory/draft POST, has_unstaged flips to True."""
        _setup_task(api, set_factory, commit_set)
        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        r = api.get(f"/scriptlet/inventory/status?set={SET}&name={TASK}")
        assert r.status_code == 200
        assert r.json() == {"has_unstaged": True}
        pyops_server.assert_no_unexpected_errors()

    def test_status_missing_scriptlet_returns_404(self, api, pyops_server, set_factory):
        set_factory(SET)
        r = api.get(f"/scriptlet/inventory/status?set={SET}&name=nope")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()


class TestInventoryListHasUnstaged:
    """GET /scriptlet/inventory/list now stamps `has_unstaged` per entry
    so the Manager UI can distinguish dirty drafts from committed
    revisions in a single call (was previously a per-row /status
    follow-up that the UI didn't actually make)."""

    def test_committed_scriptlet_listed_clean(self, api, pyops_server, set_factory, commit_set):
        _setup_task(api, set_factory, commit_set)
        r = api.get("/scriptlet/inventory/list")
        assert r.status_code == 200
        entry = next(e for e in r.json() if e["set"] == SET and e["name"] == TASK)
        assert entry["has_unstaged"] is False
        pyops_server.assert_no_unexpected_errors()

    def test_dirty_scriptlet_listed_with_has_unstaged_true(self, api, pyops_server, set_factory, commit_set):
        _setup_task(api, set_factory, commit_set)
        # Draft over the committed version — has_unstaged must flip.
        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        r = api.get("/scriptlet/inventory/list")
        assert r.status_code == 200
        entry = next(e for e in r.json() if e["set"] == SET and e["name"] == TASK)
        assert entry["has_unstaged"] is True
        pyops_server.assert_no_unexpected_errors()

    def test_mixed_dirty_and_clean_in_same_set(self, api, pyops_server, set_factory, commit_set):
        """One committed, one drafted-over, one brand-new untracked:
        the listing must reflect all three states in a single response."""
        _setup_task(api, set_factory, commit_set, task_name="committed_one")
        _setup_task(api, set_factory, commit_set, task_name="dirty_one")
        # Make dirty_one dirty.
        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": "dirty_one", "code": DRAFT_CODE},
        )
        assert r.status_code == 200
        # Create untracked_one as a brand-new draft (never committed).
        r = api.put(
            "/scriptlet/inventory/item",
            json={"set": SET, "name": "untracked_one", "type": "task"},
        )
        assert r.status_code == 200
        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": "untracked_one", "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        r = api.get("/scriptlet/inventory/list")
        by_name = {e["name"]: e for e in r.json() if e["set"] == SET}
        assert by_name["committed_one"]["has_unstaged"] is False
        assert by_name["dirty_one"]["has_unstaged"] is True
        assert by_name["untracked_one"]["has_unstaged"] is True
        pyops_server.assert_no_unexpected_errors()


class TestManagerUiStatusIcon:
    """The Manager UI surfaces `has_unstaged` from the list endpoint
    as a per-row icon (check for synced, change_history for unstaged
    draft) so the operator distinguishes drafts from committed
    revisions at a glance — the bug this whole class regresses against
    was: pre-3.3.0 the row markup carried no draft signal at all."""

    def test_manager_renders_clean_icon_for_committed_scriptlet(
        self, e2e_page, api, pyops_server, set_factory, commit_set, e2e_env
    ):
        _setup_task(api, set_factory, commit_set)
        e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        expand_set(frame, "manager", SET)
        icon = frame.locator(f'[data-testid="scriptlet-status-{SET}-{TASK}"]')
        icon.wait_for(state="visible", timeout=15_000)
        assert icon.get_attribute("data-status") == "clean"

    def test_manager_renders_dirty_icon_for_drafted_scriptlet(
        self, e2e_page, api, pyops_server, set_factory, commit_set, e2e_env
    ):
        _setup_task(api, set_factory, commit_set)
        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
        frame = e2e_page.frame_locator('[data-testid="content-frame"]')
        expand_set(frame, "manager", SET)
        icon = frame.locator(f'[data-testid="scriptlet-status-{SET}-{TASK}"]')
        icon.wait_for(state="visible", timeout=15_000)
        # The manager refreshes every 10s; wait for the dirty state
        # (the initial render may catch the pre-draft list payload).
        icon.locator(":scope[data-status='dirty']").wait_for(state="attached", timeout=15_000)
        assert icon.get_attribute("data-status") == "dirty"


class TestInventoryDraftRoundtrip:
    """POST /inventory/draft saves draft; GET /inventory/code returns it; DELETE rolls back."""

    def test_draft_is_readable_via_code_endpoint(self, api, pyops_server, set_factory, commit_set):
        """Draft content is visible via /inventory/code (reads unstaged)."""
        _setup_task(api, set_factory, commit_set)

        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        r = api.get(f"/scriptlet/inventory/code?set={SET}&name={TASK}")
        assert r.status_code == 200
        assert "draft-unsaved" in r.json()["code"]
        pyops_server.assert_no_unexpected_errors()

    def test_draft_delete_rolls_back_to_committed(self, api, pyops_server, set_factory, commit_set):
        """DELETE /inventory/draft discards unstaged write."""
        _setup_task(api, set_factory, commit_set)
        api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )

        # Confirm draft is live.
        code = api.get(f"/scriptlet/inventory/code?set={SET}&name={TASK}").json()["code"]
        assert "draft-unsaved" in code

        # Rollback.
        r = api.delete(f"/scriptlet/inventory/draft?set={SET}&name={TASK}")
        assert r.status_code in (200, 204), f"unexpected {r.status_code}: {r.text}"

        # Content must match the committed baseline.
        code = api.get(f"/scriptlet/inventory/code?set={SET}&name={TASK}").json()["code"]
        assert "base" in code
        assert "draft-unsaved" not in code

        # And status flips back to clean.
        status = api.get(f"/scriptlet/inventory/status?set={SET}&name={TASK}").json()
        assert status == {"has_unstaged": False}
        pyops_server.assert_no_unexpected_errors()

    def test_draft_delete_unknown_returns_404(self, api, pyops_server, set_factory):
        set_factory(SET)
        r = api.delete(f"/scriptlet/inventory/draft?set={SET}&name=ghost")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_draft_delete_never_committed_reports_no_committed_version(self, api, pyops_server, set_factory):
        """A scriptlet created but never committed has nothing to revert to:
        the route says so with a 409 Conflict (no raw git error, no
        traceback) and keeps the draft."""
        set_factory(SET)
        r = api.put("/scriptlet/inventory/item", json={"set": SET, "name": TASK, "type": "task"})
        assert r.status_code == 200, r.text
        api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )

        r = api.delete(f"/scriptlet/inventory/draft?set={SET}&name={TASK}")
        assert r.status_code == 409
        assert r.json()["detail"] == "No committed version to revert to"

        code = api.get(f"/scriptlet/inventory/code?set={SET}&name={TASK}").json()["code"]
        assert code == DRAFT_CODE
        pyops_server.assert_no_unexpected_errors()


class TestInvalidSetName:
    """A set name that does not name a directory under the git base ("" is
    the git base itself, ".." its parent; a NUL byte cannot be in a path)
    is rejected with 400 before anything is git-init'ed there."""

    def test_rejected_without_git_init(self, api, pyops_server, e2e_env):
        data_dir = e2e_env["data_dir"]
        r = api.put("/scriptlet/job/item", json={"set": "", "name": "job", "drop_done": True, "param": ""})
        assert r.status_code == 400, r.text
        r = api.put("/scriptlet/schedule/item", json={"set": "nul\x00byte", "name": "job", "drop_done": True, "schedule": "", "param": ""})
        assert r.status_code == 400, r.text
        # %2E%2E reaches the controller route as the set "..".
        r = api.get("/controller/%2E%2E/panel")
        assert r.status_code == 400, r.text

        assert not (data_dir / "git" / ".git").exists()
        assert not (data_dir / ".git").exists()
        pyops_server.assert_no_unexpected_errors()


class TestUnknownSet:
    """Starting a job or saving a schedule for a set that does not exist is
    a missing resource (404), not a server error with a traceback."""

    def test_job_and_schedule_put_404(self, api, pyops_server):
        r = api.put("/scriptlet/job/item", json={"set": "nope", "name": "job", "drop_done": True, "param": ""})
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "Set 'nope' does not exist"
        r = api.put("/scriptlet/schedule/item", json={"set": "nope", "name": "job", "drop_done": True, "schedule": "", "param": ""})
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "Set 'nope' does not exist"
        pyops_server.assert_no_unexpected_errors()
