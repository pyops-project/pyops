"""Sets UI E2E tests — clone modal, switch-branch modal, create-cancel.

The API-level paths are covered by test_sets_git.py. These tests exercise
the browser-driven flows that a user clicks through.
"""

import subprocess
import time
from pathlib import Path

import pytest


def _init_bare_remote(path):
    """Create a seeded bare git repo at `path` for clone-source use."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)

    bare = path / "ui_remote.git"
    work = path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "e2e@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "user.name", "E2E"],
        check=True, capture_output=True,
    )
    (work / "README.md").write_text("# ui remote\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True, capture_output=True)
    return str(bare)


@pytest.fixture
def bare_remote(tmp_path_factory):
    return _init_bare_remote(tmp_path_factory.mktemp("ui_remote_src"))


def _nav_sets(e2e_page):
    e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="sets-navbar"]').wait_for(state="visible", timeout=10_000)
    return frame


class TestSetsClone:
    def test_clone_from_ui_creates_set(self, e2e_page, api_anon, pyops_server, bare_remote):
        """Opening the clone modal, entering a file:// URL, submitting creates the set."""
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)

        frame.locator('[data-testid="sets-clone-url"]').fill(f"file://{bare_remote}")
        frame.locator('[data-testid="sets-clone-git-name"]').fill("UI Test")
        frame.locator('[data-testid="sets-clone-git-email"]').fill("ui@example.com")
        frame.locator('[data-testid="sets-clone-submit"]').click()

        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="hidden", timeout=10_000)
        # Row appears — inferred from URL basename ("ui_remote").
        frame.locator('[data-testid="set-row-ui_remote"]').wait_for(
            state="visible", timeout=10_000
        )

        sets = api_anon.get("/scriptlet/sets/list").json()["sets"]
        assert any(s["name"] == "ui_remote" for s in sets), f"not listed: {sets}"
        pyops_server.assert_no_unexpected_errors()

    def test_clone_missing_url_shows_error_no_modal_close(self, e2e_page, api_anon, pyops_server):
        """Submitting an empty URL surfaces an inline error and keeps the modal open."""
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)

        # Leave URL empty; submit.
        frame.locator('[data-testid="sets-clone-submit"]').click()

        # Inline error is populated (client-side check before POST).
        error_text = frame.locator('[data-testid="sets-clone-error"]').inner_text()
        assert "URL" in error_text, f"expected URL error, got {error_text!r}"
        # Modal must still be visible (not closed).
        assert frame.locator('[data-testid="sets-clone-modal"]').is_visible()

        # No set got created.
        assert api_anon.get("/scriptlet/sets/list").json()["sets"] == []

        # Close cleanly.
        frame.locator('[data-testid="sets-clone-cancel"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="hidden", timeout=5_000)
        pyops_server.assert_no_unexpected_errors()

    def test_clone_cancel_button_closes_without_cloning(self, e2e_page, api_anon, pyops_server):
        """Cancel on the clone modal closes it without creating a set."""
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-clone"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="visible", timeout=5_000)

        frame.locator('[data-testid="sets-clone-url"]').fill("file:///nowhere/at/all.git")
        frame.locator('[data-testid="sets-clone-cancel"]').click()
        frame.locator('[data-testid="sets-clone-modal"]').wait_for(state="hidden", timeout=5_000)

        assert api_anon.get("/scriptlet/sets/list").json()["sets"] == []
        pyops_server.assert_no_unexpected_errors()


class TestSetsSwitchBranch:
    def test_switch_branch_via_ui_creates_and_switches(self, e2e_page, api_anon, pyops_server, e2e_env):
        """Opening the switch modal, entering a branch name and submitting updates HEAD."""
        api_anon.post("/scriptlet/sets/create", json={"name": "switch_ui"})

        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-switch-switch_ui"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="visible", timeout=5_000)

        frame.locator('[data-testid="sets-switch-branch"]').fill("feature-ui")
        frame.locator('[data-testid="sets-switch-submit"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="hidden", timeout=10_000)

        repo = Path(e2e_env["data_dir"]) / "git" / "switch_ui"
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()
        assert head == "feature-ui"
        pyops_server.assert_no_unexpected_errors(
            allow=["Needed a single revision", "No such remote"]
        )

    def test_switch_branch_empty_branch_shows_error(self, e2e_page, api_anon, pyops_server, e2e_env):
        """Submitting the switch modal with no branch value shows an inline error."""
        api_anon.post("/scriptlet/sets/create", json={"name": "switch_err"})
        initial = api_anon.get("/repo/status?set=switch_err").json()["branch"]

        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-refresh"]').click()
        frame.locator('[data-testid="set-switch-switch_err"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="visible", timeout=5_000)

        # Leave branch empty, submit.
        frame.locator('[data-testid="sets-switch-submit"]').click()
        err = frame.locator('[data-testid="sets-switch-error"]').inner_text()
        assert "branch" in err.lower(), f"expected branch error, got {err!r}"
        assert frame.locator('[data-testid="sets-switch-modal"]').is_visible()

        frame.locator('[data-testid="sets-switch-cancel"]').click()
        frame.locator('[data-testid="sets-switch-modal"]').wait_for(state="hidden", timeout=5_000)

        # HEAD unchanged.
        assert api_anon.get("/repo/status?set=switch_err").json()["branch"] == initial
        pyops_server.assert_no_unexpected_errors()


class TestSetsCreateCancel:
    def test_create_modal_cancel_does_not_create(self, e2e_page, api_anon, pyops_server):
        """Cancel on the create-set modal closes it without creating a set."""
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5_000)

        frame.locator('[data-testid="sets-create-name"]').fill("never_created")
        frame.locator('[data-testid="sets-create-cancel"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="hidden", timeout=5_000)

        sets = api_anon.get("/scriptlet/sets/list").json()["sets"]
        assert not any(s["name"] == "never_created" for s in sets)
        pyops_server.assert_no_unexpected_errors()

    def test_create_empty_name_shows_error(self, e2e_page, api_anon, pyops_server):
        """Submitting the create modal without a name surfaces an inline error."""
        frame = _nav_sets(e2e_page)
        frame.locator('[data-testid="sets-create"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="visible", timeout=5_000)

        # Leave name empty; submit.
        frame.locator('[data-testid="sets-create-submit"]').click()

        err = frame.locator('[data-testid="sets-create-error"]').inner_text()
        assert "name" in err.lower(), f"expected name error, got {err!r}"
        assert frame.locator('[data-testid="sets-create-modal"]').is_visible()
        assert api_anon.get("/scriptlet/sets/list").json()["sets"] == []

        frame.locator('[data-testid="sets-create-cancel"]').click()
        frame.locator('[data-testid="sets-create-modal"]').wait_for(state="hidden", timeout=5_000)
        pyops_server.assert_no_unexpected_errors()
