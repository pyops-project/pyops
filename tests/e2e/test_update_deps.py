"""E2E tests for the per-set "Update Dependencies" flow.

Covers the wiring: HTTP endpoint → `sets.update_set_environment` → real pip
subprocess + git operations → `.update_warnings.json` written → `/health`
surfaces non-empty conflicts.

Goes through a real pyops_server with real pip — the conflict-resolution
logic itself is exhaustively unit-tested in
`tests/unit/lib/test_update_set_environment.py`. This module only proves
that the end-to-end path is wired correctly.
"""

import time

import pytest


SET = "updatedeps_e2e"


def _create_scriptlet(api, set_name, name, type_, code):
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


class TestUpdateDepsHappyPath:
    def test_update_deps_with_single_scriptlet_returns_clean_summary(self, api, pyops_server):
        """One scriptlet pinning one pip package → no conflicts, summary
        records the install. Real pip subprocess runs; timeout matches the
        existing `test_pip_install` patterns (~120s for venv + download)."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_clean"})
        # 'six' is a tiny package, fast install, no compiled deps.
        _create_scriptlet(api, SET + "_clean", "uses_six", "task", "#!six==1.16.0\nimport six\n")

        r = api.post(f"/scriptlet/sets/update_deps/{SET}_clean", timeout=180)
        assert r.status_code == 200, f"update_deps failed: {r.text}"
        body = r.json()
        assert body["status"] == "ok"
        summary = body["summary"]
        assert summary["set"] == SET + "_clean"
        assert summary["conflicts"]["pip"] == []
        assert summary["conflicts"]["git"] == []
        # Six was visible to the scanner — note the canonicalized name.
        pip_names = [d["pkg"] for d in summary["installed"]["pip"]]
        assert "six" in pip_names
        pyops_server.assert_no_unexpected_errors()


class TestUpdateDepsConflictPath:
    def test_conflict_surfaces_in_summary_and_health(self, api, pyops_server):
        """Two scriptlets pinning conflicting versions of the same package →
        update_deps returns a conflict entry in `summary.conflicts.pip` and
        /health flips to `degraded` with `dependency_conflicts: [...]`."""
        api.post("/scriptlet/sets/create", json={"name": SET + "_conf"})

        # Both pin `six`, different versions. fs-mtime ordering decides:
        # `new.task.py` is touched second so it wins.
        _create_scriptlet(api, SET + "_conf", "old_pin", "task", "#!six==1.15.0\nimport six\n")
        time.sleep(0.1)  # ensure mtime differs
        _create_scriptlet(api, SET + "_conf", "new_pin", "task", "#!six==1.16.0\nimport six\n")

        r = api.post(f"/scriptlet/sets/update_deps/{SET}_conf", timeout=180)
        assert r.status_code == 200
        summary = r.json()["summary"]
        pip_conflicts = summary["conflicts"]["pip"]
        assert len(pip_conflicts) == 1, f"expected 1 conflict, got: {pip_conflicts}"
        assert pip_conflicts[0]["pkg"] == "six"
        # Newer scriptlet wins by filesystem mtime
        assert pip_conflicts[0]["winner"]["source"] == "new_pin.task.py"
        assert pip_conflicts[0]["winner"]["spec"] == "==1.16.0"

        # /health surfaces the conflict from the per-set warnings file.
        h = api.get("/health")
        assert h.status_code == 200
        hbody = h.json()
        assert hbody["status"] == "degraded"
        assert hbody["checks"]["dependency_conflicts"] == "conflicts"
        sets_with_conflicts = [c["set"] for c in hbody["dependency_conflicts"]]
        assert SET + "_conf" in sets_with_conflicts

        pyops_server.assert_no_unexpected_errors(
            allow=["pip conflict", "Update completed with"]
        )


class TestUpdateDepsUnknownSet:
    def test_unknown_set_returns_404(self, api, pyops_server):
        r = api.post("/scriptlet/sets/update_deps/never_existed_e2e")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()
