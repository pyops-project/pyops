import pytest
from fastapi.testclient import TestClient
from app.web import app
from app.routers.scriptlet.inventory import CreateArgument, SaveArgument
from unittest.mock import patch, call, MagicMock

class TestInventoryRouter:
    def test_get_inventory_list(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/list")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0  # From mock sample files
        assert all("set" in item and "name" in item and "type" in item for item in data)

    def test_get_inventory_sets(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/sets")
        assert response.status_code == 200
        data = response.json()["sets"]
        assert isinstance(data, list)
        assert "default" in data  # From mock

    def test_put_inventory_item_create(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "test_set", "name": "test_task", "type": "task"}
        with patch("os.makedirs") as mock_makedirs:
            response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
            data = response.json()
            assert data["set"] == "test_set"
            assert data["name"] == "test_task"
            assert data["type"] == "task"

    def test_put_inventory_item_create_docs_markdown(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        """Markdown files other than README live under docs/{name}.md.
        The inventory router should create such files transparently —
        type='markdown', name='intro' resolves to docs/intro.md via
        get_file_path; write_content makes the docs/ dir on demand."""
        create_data = {"set": "test_set", "name": "intro", "type": "markdown"}
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["set"] == "test_set"
        assert data["name"] == "intro"
        assert data["type"] == "markdown"

    def test_put_inventory_item_create_conflict(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "default", "name": "sample_task", "type": "task"}  # Exists in mock
        from app.lib.errors import NameTakenError
        with patch("app.lib.git.create_scriptlet", side_effect=NameTakenError("Name 'sample_task' already exists")):
            response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
            assert response.status_code == 409

    def test_put_inventory_item_create_invalid_type(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        create_data = {"set": "test_set", "name": "invalid", "type": "invalid"}
        response = mock_client.put("/scriptlet/inventory/item", json=create_data, headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["set"] == "test_set"
        assert data["name"] == "invalid"
        assert data["type"] == "invalid"

    def test_post_inventory_draft(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        save_data = {"set": "test_set", "name": "draft_task", "code": "# Draft code"}
        repo_path = "data/git/test_set"
        with patch("os.listdir", return_value=["draft_task.py"]):
            response = mock_client.post("/scriptlet/inventory/draft", json=save_data, headers={"X-User": "testuser"})
            assert response.status_code == 200
        # No commit expected

    def test_delete_inventory_item(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("os.listdir", return_value=["sample_task.py"]):
            response = mock_client.delete("/scriptlet/inventory/item?set=default&name=sample_task", headers={"X-User": "testuser"})
            assert response.status_code == 200

    def test_delete_inventory_draft(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = "pass\n"

        with patch("os.listdir", return_value=["draft_task.py"]), \
             patch("app.lib.git.read_content", return_value="pass\n"), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", return_value=mock_file):
            response = mock_client.delete("/scriptlet/inventory/draft?set=test_set&name=draft_task", headers={"X-User": "testuser"})
            assert response.status_code == 200

    def test_get_inventory_code(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        # Assume file written in previous mock
        with patch("app.lib.git.read_content", return_value="# Sample code"), \
             patch("os.listdir", return_value=["sample_task.py"]):
            response = mock_client.get("/scriptlet/inventory/code?set=default&name=sample_task", headers={"X-User": "testuser"})
            assert response.status_code == 200
            assert response.json()["code"] == "# Sample code"

    def test_get_repo_status(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/repo/status?set=default", headers={"X-User": "testuser"})
        assert response.status_code == 200
        data = response.json()
        assert data["branch"] == "main"

    def test_get_inventory_status_has_unstaged(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.git.has_unstaged_changes", return_value=True), \
             patch("os.listdir", return_value=["test.py"]):
            response = mock_client.get("/scriptlet/inventory/status?set=test_set&name=test")
            assert response.status_code == 200
            assert response.json() == {"has_unstaged": True}

    def test_get_inventory_status_no_unstaged(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("app.lib.git.has_unstaged_changes", return_value=False), \
             patch("os.listdir", return_value=["test.py"]):
            response = mock_client.get("/scriptlet/inventory/status?set=test_set&name=test")
            assert response.status_code == 200
            assert response.json() == {"has_unstaged": False}

    def test_get_inventory_status_not_found(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("os.listdir", return_value=[]):
            response = mock_client.get("/scriptlet/inventory/status?set=test_set&name=missing")
            assert response.status_code == 404

    def test_inventory_unauthorized(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.get("/scriptlet/inventory/list")  # No X-User
        assert response.status_code == 200  # Anonymous allowed
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_inventory_invalid_set(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        with patch("os.path.exists", return_value=False):
            response = mock_client.put("/scriptlet/inventory/item", json={"set": "nonexistent", "name": "test", "type": "task"}, headers={"X-User": "testuser"})
            assert response.status_code == 400


class TestInventoryPyopsGuard:
    """Inventory write endpoints must reject `set=pyops` — those scriptlets
    ship with the pyops source and are versioned via the pyops repo, not via
    the API. Allowing writes would corrupt the application's own code."""

    def test_put_pyops_rejected(self, mock_client, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.put(
            "/scriptlet/inventory/item",
            json={"set": "pyops", "name": "set_clone", "type": "task"},
            headers={"X-User": "alice"},
        )
        assert response.status_code == 400
        assert "bundled with pyops" in response.json()["detail"]

    def test_draft_post_pyops_rejected(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.post(
            "/scriptlet/inventory/draft",
            json={"set": "pyops", "name": "set_clone", "code": "print('hi')"},
            headers={"X-User": "alice"},
        )
        assert response.status_code == 400

    def test_delete_pyops_rejected(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.delete(
            "/scriptlet/inventory/item",
            params={"set": "pyops", "name": "set_clone"},
            headers={"X-User": "alice"},
        )
        assert response.status_code == 400

    def test_draft_delete_pyops_rejected(self, mock_client, mock_git_ops, test_temp_dir, patch_tempfile, mock_datetime):
        response = mock_client.delete(
            "/scriptlet/inventory/draft",
            params={"set": "pyops", "name": "set_clone"},
            headers={"X-User": "alice"},
        )
        assert response.status_code == 400


class TestInventoryRealSet:
    """Inventory routes against a real git set repo (no git mocks)."""

    @pytest.mark.parametrize("type_, name, path, content", [
        ("task", "job", "job.task.py", "pass\n"),
        ("view", "page", "page.html", '<div class="container">\n  <!-- View content here -->\n</div>'),
        ("jslibrary", "lib", "lib.js", "// JS Library\n"),
        ("data", "cfg", "data/cfg.yaml", ""),
    ])
    def test_put_item_writes_initial_content(self, mock_client, set_repo, type_, name, path, content):
        """A new scriptlet starts with its type's initial content, as an
        uncommitted draft; a new data file is an empty YAML file."""
        import os
        repo = set_repo("s1")
        response = mock_client.put("/scriptlet/inventory/item", json={"set": "s1", "name": name, "type": type_})
        assert response.status_code == 200
        assert response.json() == {"set": "s1", "name": name, "type": type_}
        with open(os.path.join(repo, path), encoding="utf-8") as f:
            assert f.read() == content

    def test_draft_post_unknown_set_404(self, mock_client, set_repo):
        set_repo("s1")
        response = mock_client.post("/scriptlet/inventory/draft", json={"set": "nope", "name": "job", "code": "pass"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Set 'nope' does not exist"

    def test_item_delete_unknown_set_404(self, mock_client, set_repo):
        set_repo("s1")
        response = mock_client.delete("/scriptlet/inventory/item", params={"set": "nope", "name": "job"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Set 'nope' does not exist"

    def test_draft_delete_unknown_name_404(self, mock_client, set_repo):
        set_repo("s1", committed={"job.task.py": "pass\n"})
        response = mock_client.delete("/scriptlet/inventory/draft", params={"set": "s1", "name": "ghost"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Scriptlet s1::ghost not existing"

    @pytest.mark.parametrize("name, path", [("job", "job.task.py"), ("guide", "docs/guide.md")])
    def test_draft_delete_reverts_to_committed(self, mock_client, set_repo, name, path):
        import os
        repo = set_repo("s1", committed={path: "committed\n"}, drafts={path: "draft\n"})
        response = mock_client.delete("/scriptlet/inventory/draft", params={"set": "s1", "name": name})
        assert response.status_code == 200
        with open(os.path.join(repo, path), encoding="utf-8") as f:
            assert f.read() == "committed\n"

    def test_draft_delete_never_committed_keeps_draft(self, mock_client, set_repo):
        """A scriptlet created but never committed has nothing to revert to:
        the route reports that as a conflict (409), and the draft is left as
        it is."""
        import os
        repo = set_repo("s1", drafts={"job.task.py": "draft\n"})
        response = mock_client.delete("/scriptlet/inventory/draft", params={"set": "s1", "name": "job"})
        assert response.status_code == 409
        assert response.json()["detail"] == "No committed version to revert to"
        with open(os.path.join(repo, "job.task.py"), encoding="utf-8") as f:
            assert f.read() == "draft\n"

    def test_code_get_unknown_name_404(self, mock_client, set_repo):
        set_repo("s1", committed={"job.task.py": "pass\n"})
        response = mock_client.get("/scriptlet/inventory/code", params={"set": "s1", "name": "ghost"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Scriptlet s1::ghost not existing"

    @pytest.mark.parametrize("ext, content", [("json", '{"a": 1}\n'), ("yaml", "a: 1\n")])
    def test_code_get_data_file_reports_its_extension(self, mock_client, set_repo, ext, content):
        """A data file's code response carries the on-disk extension so the
        editor can pick the json/yaml syntax mode."""
        set_repo("s1", committed={f"data/cfg.{ext}": content})
        response = mock_client.get("/scriptlet/inventory/code", params={"set": "s1", "name": "cfg"})
        assert response.status_code == 200
        assert response.json() == {"code": content, "type": "data", "ext": ext}

    @pytest.mark.parametrize("name", ["../escaped", "", "t.x", "a\x00b", "a b", "<b>"])
    def test_put_item_invalid_name_400(self, mock_client, set_repo, tmp_path, name):
        """A scriptlet name is [A-Za-z0-9_-]+: a traversal, an empty name, a
        dotted name (clashing with another one's files), a NUL byte or
        markup is refused before anything is written — nothing appears in
        or next to the set."""
        repo = set_repo("s1")
        before = sorted(p for p in tmp_path.rglob("*") if ".git" not in p.parts)
        response = mock_client.put("/scriptlet/inventory/item", json={"set": "s1", "name": name, "type": "task"})
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Invalid scriptlet name")
        assert sorted(p for p in tmp_path.rglob("*") if ".git" not in p.parts) == before

    def test_traversing_name_reaches_no_file_outside_the_set(self, mock_client, set_repo, tmp_path):
        """With a data/ dir in the set, `../../../x` used to resolve to a
        .yaml/.json outside the set: code read it, draft overwrote it, item
        DELETE removed it. Every route now refuses the name with 400."""
        import os
        set_repo("s1", committed={"data/cfg.yaml": "a: 1\n"})
        outside = tmp_path / "secret.json"
        outside.write_text('{"token": "x"}')
        name = os.path.relpath(str(outside)[:-len(".json")], os.path.join(str(tmp_path), "data", "git", "s1", "data"))
        assert name.startswith("..")
        calls = [
            ("get", "/scriptlet/inventory/code", {"params": {"set": "s1", "name": name}}),
            ("get", "/scriptlet/inventory/status", {"params": {"set": "s1", "name": name}}),
            ("post", "/scriptlet/inventory/draft", {"json": {"set": "s1", "name": name, "code": "{}"}}),
            ("delete", "/scriptlet/inventory/draft", {"params": {"set": "s1", "name": name}}),
            ("delete", "/scriptlet/inventory/item", {"params": {"set": "s1", "name": name}}),
        ]
        for method, url, kwargs in calls:
            response = getattr(mock_client, method)(url, **kwargs)
            assert response.status_code == 400, (method, url)
            assert "token" not in response.text
        assert outside.read_text() == '{"token": "x"}'

    @pytest.mark.parametrize("path, name", [("docs/notes.md", "notes"), ("data/cfg.yaml", "cfg"), ("data/st.json", "st")])
    def test_status_of_docs_and_data(self, mock_client, set_repo, path, name):
        """status resolves docs/ pages and data/ files like code does (the
        markdown and data editors' Revert button depends on it)."""
        import os
        repo = set_repo("s1", committed={path: "a: 1\n"})
        response = mock_client.get("/scriptlet/inventory/status", params={"set": "s1", "name": name})
        assert response.status_code == 200
        assert response.json() == {"has_unstaged": False}
        with open(os.path.join(repo, path), "a", encoding="utf-8") as f:
            f.write("b: 2\n")
        response = mock_client.get("/scriptlet/inventory/status", params={"set": "s1", "name": name})
        assert response.json() == {"has_unstaged": True}

    def test_status_ignores_stray_top_level_markdown(self, mock_client, set_repo):
        """A top-level `notes.md` (e.g. from a cloned repo) is no scriptlet:
        404, not a 500 from type inference."""
        set_repo("s1", committed={"notes.md": "# n\n"})
        response = mock_client.get("/scriptlet/inventory/status", params={"set": "s1", "name": "notes"})
        assert response.status_code == 404

    def test_status_of_bundled_set_runs_no_git(self, mock_client, mock_conf):
        """The bundled set has no repository: status answers without a git
        command against the application tree."""
        with patch("subprocess.check_output") as mock_check:
            response = mock_client.get("/scriptlet/inventory/status", params={"set": "pyops", "name": "data"})
        assert response.status_code == 200
        assert response.json() == {"has_unstaged": False}
        mock_check.assert_not_called()

    def test_code_beside_a_prefix_clash(self, mock_client, set_repo):
        """`c.x.task.py` (cloned in) never answers for `c`, whatever the
        directory order: code returns the controller `c`, not a 500 for a
        `c.task.py` that does not exist."""
        set_repo("s1", committed={"c.x.task.py": "other\n", "c.controller.py": "ctrl\n"})
        with patch("os.listdir", return_value=["c.x.task.py", "c.controller.py"]):
            response = mock_client.get("/scriptlet/inventory/code", params={"set": "s1", "name": "c"})
        assert response.status_code == 200
        assert response.json() == {"code": "ctrl\n", "type": "controller"}

    def test_create_beside_a_prefix_clash(self, mock_client, set_repo):
        """Only exact names collide: `c` is free beside `c.x.task.py`."""
        set_repo("s1", committed={"c.x.task.py": "other\n"})
        response = mock_client.put("/scriptlet/inventory/item", json={"set": "s1", "name": "c", "type": "controller"})
        assert response.status_code == 200

    def test_plain_directory_is_no_set(self, mock_client, mock_conf, tmp_path):
        """A directory without .git is no set (404), and a read does not
        git-init or commit anything in it."""
        plain = tmp_path / "data" / "git" / "plain"
        plain.mkdir()
        response = mock_client.get("/scriptlet/inventory/code", params={"set": "plain", "name": "x"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Set 'plain' does not exist"
        assert list(plain.iterdir()) == []

    def test_lock_file_name_is_no_set(self, mock_client, set_repo):
        """repo_lock leaves `<set>.lock` files under the git base: such a name
        is an invalid set name (400), not a 500 'Not a directory'."""
        repo = set_repo("s1")
        open(repo + ".lock", "a").close()
        for url, params in [("/scriptlet/inventory/code", {"set": "s1.lock", "name": "t"}),
                            ("/repo/status", {"set": "s1.lock"}),
                            ("/scriptlet/jslib/s1.lock/x", {})]:
            response = mock_client.get(url, params=params)
            assert response.status_code == 400, url

    def test_repo_status(self, mock_client, set_repo):
        set_repo("s1")
        response = mock_client.get("/repo/status", params={"set": "s1"})
        assert response.status_code == 200
        assert response.json()["is_tag"] is False
        response = mock_client.get("/repo/status", params={"set": "nope"})
        assert response.status_code == 404

    def test_repo_status_of_bundled_set_400(self, mock_client, mock_conf):
        """The bundled set has no repository of its own: 400, and no git
        command runs against the application tree (which reported the pyops
        source branch, or failed with 500 where app/ has no .git)."""
        with patch("subprocess.check_output") as mock_check, patch("subprocess.run") as mock_run:
            response = mock_client.get("/repo/status", params={"set": "pyops"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Set 'pyops' is bundled with pyops and has no repository."
        mock_check.assert_not_called()
        mock_run.assert_not_called()

    def test_listings_hold_only_valid_names(self, mock_client, set_repo, tmp_path):
        """Sets and scriptlets outside the identifier charset (a cloned
        `ops.tools`, a file `my notes.md`) are not listed — no route could
        open them."""
        set_repo("s1", committed={"job.task.py": "pass\n", "my.job.task.py": "pass\n", "docs/my notes.md": "x\n",
                                  "data/a.b.yaml": "a: 1\n"})
        set_repo("ops.tools", committed={"t.task.py": "pass\n"})
        assert mock_client.get("/scriptlet/inventory/sets").json() == {"sets": ["s1"]}
        listed = mock_client.get("/scriptlet/inventory/list").json()
        assert [(e["set"], e["name"]) for e in listed] == [("s1", "README"), ("s1", "job")]

