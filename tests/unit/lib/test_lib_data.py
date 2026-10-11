"""Unit tests for app/lib/data.py — the set-scoped YAML/JSON facade.

Coverage focus: write/read round-trip per format, lock semantics (shared
vs exclusive), cross-format name uniqueness, escape-attempt rejection,
gitignore append. Concurrent-process lock behaviour is exercised
end-to-end in tests/integration/test_data_concurrent.py.
"""
import json
import os

import pytest


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "myset"
    p.mkdir()
    (p / ".gitignore").write_text("*.bck\n")
    return str(p)


class TestRoundTrip:
    def test_yaml_write_then_read(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("config", format="yaml") as c:
            c["threshold"] = 42
            c["nested"] = {"a": 1}
        with sd.read("config") as c:
            assert c == {"threshold": 42, "nested": {"a": 1}}

    def test_json_write_then_read(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("state", format="json") as s:
            s["last_run"] = 12345
        with sd.read("state") as s:
            assert s == {"last_run": 12345}
        # And the file on disk is real JSON.
        with open(os.path.join(repo, "data", "state.json")) as f:
            assert json.load(f) == {"last_run": 12345}

    def test_yml_alias_writes_yaml(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("aliased", format="yml") as c:
            c["x"] = 1
        # Stored as .yaml on disk (the writer normalises yml → yaml)
        assert os.path.isfile(os.path.join(repo, "data", "aliased.yaml"))

    def test_existing_file_keeps_format(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("cfg", format="json") as c:
            c["x"] = 1
        # Second write without format uses the existing .json
        with sd.write("cfg") as c:
            c["x"] = 2
        with sd.read("cfg") as c:
            assert c == {"x": 2}

    def test_write_requires_format_for_new_file(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with pytest.raises(ValueError):
            with sd.write("brand_new") as _:
                pass


class TestNameUniqueness:
    def test_cross_format_collision_rejected(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("config", format="yaml") as c:
            c["x"] = 1
        with pytest.raises(FileExistsError):
            with sd.write("config", format="json") as c:
                c["x"] = 2

    def test_check_data_name_unique(self, repo):
        from app.lib.data import SetData, check_data_name_unique
        sd = SetData("myset", repo)
        assert check_data_name_unique(repo, "fresh") is True
        with sd.write("fresh", format="yaml") as c:
            c["x"] = 1
        assert check_data_name_unique(repo, "fresh") is False


class TestApiErrors:
    """Errors a scriptlet author gets from the `data` global."""

    def test_read_missing_without_data_dir(self, repo):
        from app.lib.data import SetData
        with pytest.raises(FileNotFoundError, match="data 'cfg' does not exist in set 'myset'"):
            with SetData("myset", repo).read("cfg") as _:
                pass

    def test_read_missing_name(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("other", format="yaml") as c:
            c["x"] = 1
        with pytest.raises(FileNotFoundError, match="data 'cfg' does not exist"):
            with sd.read("cfg") as _:
                pass

    def test_name_in_two_formats_is_ambiguous(self, repo):
        """`cfg.yaml` and `cfg.json` side by side (e.g. both arrived with a
        pull): reading or writing `cfg` refuses to pick one."""
        from app.lib.data import SetData
        data_dir = os.path.join(repo, "data")
        os.makedirs(data_dir)
        with open(os.path.join(data_dir, "cfg.yaml"), "w") as f:
            f.write("x: 1\n")
        with open(os.path.join(data_dir, "cfg.json"), "w") as f:
            f.write('{"x": 2}\n')
        sd = SetData("myset", repo)
        with pytest.raises(FileExistsError, match=r"multiple formats \['.json', '.yaml'\]"):
            with sd.read("cfg") as _:
                pass
        with pytest.raises(FileExistsError, match=r"multiple formats \['.json', '.yaml'\]"):
            with sd.write("cfg") as c:
                c["x"] = 3
        with open(os.path.join(data_dir, "cfg.json")) as f:
            assert json.load(f) == {"x": 2}

    def test_unsupported_format_rejected(self, repo):
        from app.lib.data import SetData
        with pytest.raises(ValueError, match="unsupported format: 'toml'"):
            with SetData("myset", repo).write("cfg", format="toml") as _:
                pass
        assert not os.path.exists(os.path.join(repo, "data", "cfg.toml"))


class TestEscapeRejection:
    @pytest.mark.parametrize("bad_name", [
        "", "../other", "/abs", "sub/dir", "..", ".hidden", "with\\bs",
    ])
    def test_invalid_names_rejected_on_read(self, repo, bad_name):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with pytest.raises(ValueError):
            with sd.read(bad_name) as _:
                pass

    @pytest.mark.parametrize("bad_name", [
        "", "../other", "/abs", "sub/dir", "..", ".hidden",
    ])
    def test_invalid_names_rejected_on_write(self, repo, bad_name):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with pytest.raises(ValueError):
            with sd.write(bad_name, format="yaml") as _:
                pass


class TestNameRule:
    """A data name follows the rule of every scriptlet name,
    `[A-Za-z0-9_-]+`: what a job writes, the inventory API lists and
    serves (it refuses any other name)."""

    @pytest.mark.parametrize("name", ["a.b", "app.config", "my cfg", "café", "x\x00"])
    def test_name_outside_charset_refused(self, repo, name):
        from app.lib.data import SetData
        from app.lib.errors import InvalidScriptletNameError
        sd = SetData("myset", repo)
        with pytest.raises(InvalidScriptletNameError):
            with sd.write(name, format="yaml") as c:
                c["x"] = 1
        with pytest.raises(InvalidScriptletNameError):
            with sd.read(name) as _:
                pass
        assert not os.path.exists(os.path.join(repo, "data"))

    def test_valid_names_accepted(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("app_config-2", format="yaml") as c:
            c["x"] = 1
        with sd.read("app_config-2") as c:
            assert c == {"x": 1}

    def test_list_skips_file_with_invalid_name(self, repo):
        """A file put there by other means (e.g. a commit) whose name the
        rule refuses is not listed — `read` would refuse it, and so does
        the inventory API."""
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("good", format="yaml") as c:
            c["x"] = 1
        with open(os.path.join(repo, "data", "app.config.yaml"), "w") as f:
            f.write("x: 2\n")
        assert sd.list() == ["good"]


class TestList:
    def test_list_empty(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        assert sd.list() == []

    def test_list_sorted(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("zebra", format="yaml") as c:
            c["x"] = 1
        with sd.write("alpha", format="json") as c:
            c["x"] = 2
        with sd.write("bravo", format="yaml") as c:
            c["x"] = 3
        assert sd.list() == ["alpha", "bravo", "zebra"]

    def test_list_skips_lock_sidecars(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("a", format="yaml") as c:
            c["x"] = 1
        # The .a.lock sidecar exists alongside a.yaml; list() must ignore it
        assert os.path.isfile(os.path.join(repo, "data", ".a.lock"))
        assert sd.list() == ["a"]


class TestGitignore:
    def test_gitignore_appends_lock_pattern(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("cfg", format="yaml") as c:
            c["x"] = 1
        with open(os.path.join(repo, ".gitignore")) as f:
            content = f.read()
        assert "data/.*.lock" in content

    def test_gitignore_idempotent(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        # Trigger ensure_gitignore twice
        with sd.write("a", format="yaml") as c:
            c["x"] = 1
        with sd.write("b", format="yaml") as c:
            c["x"] = 1
        with open(os.path.join(repo, ".gitignore")) as f:
            content = f.read()
        # The entry appears exactly once
        assert content.count("data/.*.lock") == 1

    def test_gitignore_without_trailing_newline(self, repo):
        from app.lib.data import SetData
        with open(os.path.join(repo, ".gitignore"), "w") as f:
            f.write("*.bck")
        with SetData("myset", repo).write("cfg", format="yaml") as c:
            c["x"] = 1
        with open(os.path.join(repo, ".gitignore")) as f:
            assert f.read() == "*.bck\ndata/.*.lock\n"

    def test_unreadable_gitignore_does_not_break_writes(self, repo):
        """The .gitignore entry is best effort: a .gitignore that cannot be
        read or written (here: a directory) does not fail data writes."""
        from app.lib.data import SetData
        os.remove(os.path.join(repo, ".gitignore"))
        os.mkdir(os.path.join(repo, ".gitignore"))
        sd = SetData("myset", repo)
        with sd.write("cfg", format="yaml") as c:
            c["x"] = 1
        with sd.read("cfg") as c:
            assert c == {"x": 1}


class TestRoundTripInsideWrite:
    """Mutating the yielded dict inside a `with data.write(...) as obj:`
    block must persist on close — that's the whole point of the context
    manager API."""

    def test_in_place_mutation_persists(self, repo):
        from app.lib.data import SetData
        sd = SetData("myset", repo)
        with sd.write("cfg", format="yaml") as c:
            c["a"] = 1
        with sd.write("cfg") as c:
            c["b"] = 2  # In-place mutation
        with sd.read("cfg") as c:
            assert c == {"a": 1, "b": 2}
