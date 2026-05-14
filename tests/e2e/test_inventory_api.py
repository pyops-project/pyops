"""
Inventory edge-case API E2E tests — endpoints not covered by smoke/editor flows.

Covers:
  - GET    /scriptlet/inventory/sets     list of sets (repos with .git)
  - GET    /scriptlet/inventory/status   unstaged-changes flag
  - GET    /repo/status                  repo branch / tag info
  - DELETE /scriptlet/inventory/draft    discard unstaged changes to HEAD
  - POST   /scriptlet/inventory/draft    save draft without commit (roundtrip)

Smoke test_smoke.py covers /sets/list (which returns {name, branch}); this file
covers the separate /inventory/sets endpoint that returns just names.
"""

import pytest


SET = "inv_api_e2e"
TASK = "my_task"

BASE_CODE = 'print("base")\n'
DRAFT_CODE = 'print("draft-unsaved")\n'


def _setup_task(api, set_factory, set_name=SET, task_name=TASK, code=BASE_CODE):
    set_factory(set_name)
    r = api.put(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "type": "task"},
    )
    assert r.status_code == 200, f"create failed: {r.text}"
    r = api.post(
        "/scriptlet/inventory/item",
        json={"set": set_name, "name": task_name, "code": code},
    )
    assert r.status_code == 200, f"save failed: {r.text}"


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

    def test_committed_scriptlet_has_no_unstaged(self, api, pyops_server, set_factory):
        """Right after POST /inventory/item (which commits), has_unstaged is False."""
        _setup_task(api, set_factory)
        r = api.get(f"/scriptlet/inventory/status?set={SET}&name={TASK}")
        assert r.status_code == 200
        assert r.json() == {"has_unstaged": False}
        pyops_server.assert_no_unexpected_errors()

    def test_draft_write_sets_unstaged_true(self, api, pyops_server, set_factory):
        """After /inventory/draft POST, has_unstaged flips to True."""
        _setup_task(api, set_factory)
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


class TestInventoryDraftRoundtrip:
    """POST /inventory/draft saves draft; GET /inventory/code returns it; DELETE rolls back."""

    def test_draft_is_readable_via_code_endpoint(self, api, pyops_server, set_factory):
        """Draft content is visible via /inventory/code (reads unstaged)."""
        _setup_task(api, set_factory)

        r = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        assert r.status_code == 200

        r = api.get(f"/scriptlet/inventory/code?set={SET}&name={TASK}")
        assert r.status_code == 200
        assert "draft-unsaved" in r.json()["code"]
        pyops_server.assert_no_unexpected_errors()

    def test_draft_delete_rolls_back_to_committed(self, api, pyops_server, set_factory):
        """DELETE /inventory/draft discards unstaged write."""
        _setup_task(api, set_factory)
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

    def test_draft_delete_wrong_type_returns_400(self, api, pyops_server, set_factory):
        """Providing type_ that mismatches inferred filetype rejects with 400."""
        _setup_task(api, set_factory)
        api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET, "name": TASK, "code": DRAFT_CODE},
        )
        r = api.delete(
            f"/scriptlet/inventory/draft?set={SET}&name={TASK}&type_=view"
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        pyops_server.assert_no_unexpected_errors()
