"""End-to-end coverage for multi-page markdown under docs/.

Verifies that the v3.3.0 feature works wire-to-disk on a real pyops
server: README.md at the set root continues to work, and any number of
free-named docs/{name}.md pages can be created, listed, read, edited,
and deleted through the standard inventory API.
"""
import os

import pytest


SET = "md_e2e"


class TestMarkdownDocsCRUD:
    """The inventory API treats docs/*.md as type='markdown' entries
    alongside README.md, and all five operations (PUT/POST/GET/DELETE/
    LIST) round-trip through the same endpoints used for scriptlets."""

    def test_create_lists_alongside_readme(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        # README is created by set_factory; create a docs/ page.
        r = api.put(
            "/scriptlet/inventory/item",
            json={"set": SET, "name": "intro", "type": "markdown"},
        )
        assert r.status_code == 200, f"create failed: {r.text}"
        # The list endpoint should report both entries with type=markdown.
        scripts = api.get("/scriptlet/inventory/list").json()
        md = [s for s in scripts if s["set"] == SET and s["type"] == "markdown"]
        names = sorted(s["name"] for s in md)
        assert "README" in names
        assert "intro" in names
        # On disk: README.md at the root, docs/intro.md under docs/.
        repo = e2e_env["data_dir"] / "git" / SET
        assert (repo / "README.md").is_file()
        assert (repo / "docs" / "intro.md").is_file()
        pyops_server.assert_no_unexpected_errors()

    def test_draft_writes_to_docs_dir(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "guide", "type": "markdown"})
        # POST draft updates the working-tree file.
        r = api.post("/scriptlet/inventory/draft",
                     json={"set": SET, "name": "guide", "code": "# Guide\n\nWelcome.\n"})
        assert r.status_code == 200, r.text
        content = (e2e_env["data_dir"] / "git" / SET / "docs" / "guide.md").read_text()
        assert content == "# Guide\n\nWelcome.\n"
        pyops_server.assert_no_unexpected_errors()

    def test_code_get_returns_docs_md_content(self, api, pyops_server, set_factory):
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "ref", "type": "markdown"})
        api.post("/scriptlet/inventory/draft",
                 json={"set": SET, "name": "ref", "code": "## API\n"})
        r = api.get(f"/scriptlet/inventory/code?set={SET}&name=ref")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == "## API\n"
        # Endpoint also reports the resolved type so editor clients can
        # pick the right Ace mode.
        assert body["type"] == "markdown"
        pyops_server.assert_no_unexpected_errors()

    def test_delete_removes_docs_md(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "tmp", "type": "markdown"})
        repo = e2e_env["data_dir"] / "git" / SET
        assert (repo / "docs" / "tmp.md").is_file()
        r = api.delete(f"/scriptlet/inventory/item?set={SET}&name=tmp")
        assert r.status_code == 200, r.text
        assert not (repo / "docs" / "tmp.md").exists()
        # README.md untouched.
        assert (repo / "README.md").is_file()
        pyops_server.assert_no_unexpected_errors()


class TestMarkdownNamespace:
    """Names share one namespace per set: a docs/*.md can't collide
    with a scriptlet of the same name (or vice versa)."""

    def test_doc_name_collides_with_existing_task(self, api, pyops_server, set_factory):
        set_factory(SET)
        api.put("/scriptlet/inventory/item",
                json={"set": SET, "name": "notes", "type": "task"})
        # Same name with type=markdown should be rejected.
        r = api.put("/scriptlet/inventory/item",
                    json={"set": SET, "name": "notes", "type": "markdown"})
        assert r.status_code in (400, 409), r.text
        pyops_server.assert_no_unexpected_errors()

    def test_two_doc_pages_coexist(self, api, pyops_server, set_factory, e2e_env):
        set_factory(SET)
        for name in ("alpha", "beta", "gamma"):
            r = api.put("/scriptlet/inventory/item",
                        json={"set": SET, "name": name, "type": "markdown"})
            assert r.status_code == 200, f"{name}: {r.text}"
        repo = e2e_env["data_dir"] / "git" / SET
        for name in ("alpha", "beta", "gamma"):
            assert (repo / "docs" / f"{name}.md").is_file()
        # README still present.
        assert (repo / "README.md").is_file()
        pyops_server.assert_no_unexpected_errors()
