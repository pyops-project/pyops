"""
Set git-remote E2E tests — clone, switch branch, pull.

These endpoints are thin wrappers around `app.lib.sets.{clone_set,
switch_branch, pull_set}`, which shell out to git. A file:// bare repo is used
as the "remote" so the tests don't need network access.

Covers:
  - POST /scriptlet/sets/clone      success (file://), name inferred from URL,
                                    invalid URL → 400.
  - POST /scriptlet/sets/switch     create new branch from HEAD, switch back,
                                    404 for unknown set.
  - POST /scriptlet/sets/pull/{n}   404 for unknown set, success for a cloned
                                    set with a reachable remote.
"""

import os
import subprocess
from pathlib import Path

import pytest


def _init_bare_remote(path):
    """Create a seeded bare remote at `path` so clone works end-to-end."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)

    bare_repo = path / "remote_repo.git"
    work = path / "work"

    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

    # Seed the remote via a throwaway working clone.
    subprocess.run(["git", "clone", str(bare_repo), str(work)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "e2e@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "user.name", "E2E"],
        check=True, capture_output=True,
    )
    (work / "README.md").write_text("# remote\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "seed"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "push", "origin", "HEAD"],
        check=True, capture_output=True,
    )
    return str(bare_repo)


@pytest.fixture
def bare_remote(tmp_path_factory):
    remote_dir = tmp_path_factory.mktemp("remote")
    return _init_bare_remote(remote_dir)


class TestSetsClone:
    """POST /scriptlet/sets/clone."""

    def test_clone_from_file_url_creates_set(self, api, pyops_server, bare_remote, e2e_env):
        url = f"file://{bare_remote}"
        r = api.post("/scriptlet/sets/clone", json={"url": url})
        assert r.status_code == 200, f"clone failed: {r.text}"
        body = r.json()
        assert body["status"] == "ok"
        # Name is inferred from URL: .../remote_repo.git → "remote_repo"
        assert "remote_repo" in body["message"]

        # Set shows up in listings and has git/venv/pkg on disk.
        sets = api.get("/scriptlet/sets/list").json()["sets"]
        assert any(s["name"] == "remote_repo" for s in sets), f"not listed: {sets}"

        data = Path(e2e_env["data_dir"])
        assert (data / "git" / "remote_repo" / ".git").is_dir()
        assert (data / "venv" / "remote_repo").is_dir()
        assert (data / "pkg" / "remote_repo").is_dir()
        pyops_server.assert_no_unexpected_errors()

    def test_clone_invalid_url_returns_400(self, api, pyops_server):
        """An empty / malformed URL must be rejected at the clone endpoint."""
        r = api.post("/scriptlet/sets/clone", json={"url": ""})
        # Implementation raises ValueError → 400; any failure other than 200 is acceptable
        # as long as no set is created. Locking 400 as the contract.
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        pyops_server.assert_no_unexpected_errors()

    def test_clone_duplicate_name_returns_400(self, api, pyops_server, bare_remote):
        """Cloning the same URL twice returns 400 (name collision)."""
        url = f"file://{bare_remote}"
        r = api.post("/scriptlet/sets/clone", json={"url": url})
        assert r.status_code == 200
        r = api.post("/scriptlet/sets/clone", json={"url": url})
        assert r.status_code == 400, f"expected collision: {r.status_code}: {r.text}"
        pyops_server.assert_no_unexpected_errors()


class TestSetsSwitch:
    """POST /scriptlet/sets/switch — branch switching."""

    def test_switch_to_new_branch_creates_it(self, api, pyops_server, e2e_env):
        """Switching to a non-existent branch creates it from current HEAD."""
        api.post("/scriptlet/sets/create", json={"name": "branch_e2e"})

        r = api.post(
            "/scriptlet/sets/switch",
            json={"name": "branch_e2e", "branch": "feature-x"},
        )
        assert r.status_code == 200, r.text

        # Verify via git rev-parse directly on disk.
        repo = Path(e2e_env["data_dir"]) / "git" / "branch_e2e"
        out = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
        assert out == "feature-x", f"HEAD is {out}"

        # Repo status endpoint should also report it.
        status = api.get("/repo/status?set=branch_e2e").json()
        assert status["branch"] == "feature-x"
        # switch_branch probes with `git rev-parse --verify` and `git remote get-url`;
        # those emit "fatal: Needed a single revision" / "error: No such remote" when
        # the branch/remote doesn't exist yet — expected on the happy path.
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote"]
        )

    def test_switch_back_to_original_branch(self, api, pyops_server, e2e_env):
        """Switching between two existing branches succeeds."""
        api.post("/scriptlet/sets/create", json={"name": "branch_back"})
        # Capture initial branch name (master vs main depending on git config).
        initial = api.get("/repo/status?set=branch_back").json()["branch"]

        api.post(
            "/scriptlet/sets/switch",
            json={"name": "branch_back", "branch": "temp"},
        )
        assert api.get("/repo/status?set=branch_back").json()["branch"] == "temp"

        # Back to original.
        r = api.post(
            "/scriptlet/sets/switch",
            json={"name": "branch_back", "branch": initial},
        )
        assert r.status_code == 200
        assert api.get("/repo/status?set=branch_back").json()["branch"] == initial
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote"]
        )

    def test_switch_unknown_set_returns_404(self, api, pyops_server):
        r = api.post(
            "/scriptlet/sets/switch",
            json={"name": "does-not-exist-xyzzy", "branch": "anything"},
        )
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()


class TestSetsPull:
    """POST /scriptlet/sets/pull/{name}."""

    def test_pull_unknown_set_returns_404(self, api, pyops_server):
        r = api.post("/scriptlet/sets/pull/does-not-exist-xyzzy")
        assert r.status_code == 404
        pyops_server.assert_no_unexpected_errors()

    def test_pull_cloned_set_picks_up_remote_changes(
        self, api, pyops_server, bare_remote, e2e_env, tmp_path
    ):
        """After cloning a set, new commits on the remote are fetched by pull."""
        # 1. Clone the remote → creates set "remote_repo".
        url = f"file://{bare_remote}"
        r = api.post("/scriptlet/sets/clone", json={"url": url})
        assert r.status_code == 200

        # 2. Add a new commit to the bare remote (via a second working clone).
        upstream = tmp_path / "upstream_work"
        subprocess.run(["git", "clone", bare_remote, str(upstream)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(upstream), "config", "user.email", "e2e@example.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(upstream), "config", "user.name", "E2E"],
            check=True, capture_output=True,
        )
        (upstream / "new_scriptlet.py").write_text("hello from upstream\n")
        subprocess.run(["git", "-C", str(upstream), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(upstream), "commit", "-m", "new commit"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(upstream), "push", "origin", "HEAD"],
            check=True, capture_output=True,
        )

        # 3. Pull via the API — the new file should appear locally.
        r = api.post("/scriptlet/sets/pull/remote_repo")
        assert r.status_code == 200, r.text

        local_file = Path(e2e_env["data_dir"]) / "git" / "remote_repo" / "new_scriptlet.py"
        assert local_file.exists(), "pull should have fetched+merged the new commit"
        assert "upstream" in local_file.read_text()
        pyops_server.assert_no_unexpected_errors()
