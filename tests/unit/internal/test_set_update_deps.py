"""Unit tests for app/internal/set_update_deps.task.py — directive parsing,
conflict resolution, and the per-set filesystem scan.

The task script normally runs inside the pyops runner with `appspace` and
`config` injected as job globals (see job_runner.setup_job_globals for the
`setname == "internal"` branch) and with set.library.py's symbols added
via the `#!{::set}` directive. The fixture below mirrors that setup by
exec'ing both files into one shared namespace, so each pure function
(`_parse_directives`, `_resolve`, `_scan`) is callable directly without
spinning up the runner.

End-to-end behaviour (pip subprocess, lock acquisition, .update_warnings.json
write) is covered by tests/e2e/test_sets_via_controller.py — only the
algorithmic pieces are unit-tested here.
"""

import os

import pytest


INTERNAL_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "internal"
))


@pytest.fixture
def task(tmp_path):
    glob = {
        "__name__": "set_update_deps_loaded",
        "appspace": str(tmp_path / "app"),
        "config": {
            "data": {"git": str(tmp_path / "data" / "git"), "jobs": str(tmp_path / "data" / "jobs")},
            "runner": {
                "venv": str(tmp_path / "data" / "venv"),
                "pkg": str(tmp_path / "data" / "pkg"),
            },
        },
        "name": None,
    }
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "data" / "git").mkdir(parents=True, exist_ok=True)
    for fname in ("set.library.py", "set_update_deps.task.py"):
        path = os.path.join(INTERNAL_DIR, fname)
        with open(path) as f:
            src = f.read()
        # Don't fire main() at module-load time.
        src = src.replace('if __name__ == "__main__":', 'if False:')
        exec(compile(src, path, "exec"), glob)
    glob["_tmp_path"] = tmp_path
    return glob


class TestParseDirectives:
    """`_parse_directives` extracts pip/git directives from scriptlet text,
    skips library imports (`#!{set::name}` and `set::name`), tolerates
    blank/garbage lines."""

    def test_pip_simple(self, task):
        result = task["_parse_directives"]("#!requests\n")
        assert result == [
            {"kind": "pip", "pkg": "requests", "spec": "", "raw": ["requests"]}
        ]

    def test_pip_with_version_pin(self, task):
        result = task["_parse_directives"]("#!requests==2.32.0\n")
        assert result[0]["pkg"] == "requests"
        assert result[0]["spec"] == "==2.32.0"

    def test_pip_with_specifier_range(self, task):
        result = task["_parse_directives"]("#!flask>=2.0,<3.0\n")
        assert result[0]["pkg"] == "flask"
        assert result[0]["spec"] == ">=2.0,<3.0"

    def test_pip_with_index_url_kept_in_raw_args(self, task):
        """Compound directives (`--index-url`, `--extra-index-url`) carry
        their flags in `raw` so pip install sees the full arg list. Older
        impls split this into a separate `extra` field; current impl keeps
        everything as the tokenised `raw`."""
        result = task["_parse_directives"]("#!torch --index-url https://example.com/cpu\n")
        assert result[0]["pkg"] == "torch"
        assert result[0]["raw"] == ["torch", "--index-url", "https://example.com/cpu"]

    def test_canonicalize_package_name(self, task):
        """PEP 503 normalization: underscores → hyphens, lowercase. Two
        scriptlets writing `#!My_Package` and `#!my-package` must aggregate
        as the same package — otherwise conflict resolution misses them."""
        a = task["_parse_directives"]("#!My_Package==1.0\n")
        b = task["_parse_directives"]("#!my-package==2.0\n")
        assert a[0]["pkg"] == b[0]["pkg"] == "my-package"

    def test_git_simple(self, task):
        result = task["_parse_directives"]("#!https://github.com/x/y.git\n")
        assert result == [
            {"kind": "git", "url": "https://github.com/x/y.git", "branch": None}
        ]

    def test_git_with_branch(self, task):
        result = task["_parse_directives"]("#!https://github.com/x/y.git develop\n")
        assert result[0]["branch"] == "develop"

    def test_ssh_git_url(self, task):
        result = task["_parse_directives"]("#!git@github.com:x/y.git\n")
        assert result[0]["kind"] == "git"
        assert result[0]["url"] == "git@github.com:x/y.git"

    def test_library_import_skipped(self, task):
        """`#!{set::name}` is a library import handled elsewhere (the
        runner's process_imports), not an install directive."""
        assert task["_parse_directives"]("#!{otherset::shared_lib}\n") == []

    def test_namespaced_library_skipped(self, task):
        assert task["_parse_directives"]("#!otherset::shared_lib\n") == []

    def test_blank_and_garbage_lines_ignored(self, task):
        text = (
            "import os\n"
            "\n"
            "#!\n"
            "#! \n"
            "x = 1\n"
        )
        assert task["_parse_directives"](text) == []

    def test_multiple_directives_in_one_text(self, task):
        text = (
            "#!requests\n"
            "#!https://github.com/a/b.git main\n"
            "code = 1\n"
            "#!numpy>=1.20\n"
        )
        result = task["_parse_directives"](text)
        assert len(result) == 3
        assert [d["kind"] for d in result] == ["pip", "git", "pip"]


class TestResolvePipDirectives:
    """`_resolve(directives, key=lambda d: d['pkg'])` aggregates pip
    directives by canonical package name; identical specs collapse to a
    single winner, disagreements trigger newest-mtime-wins."""

    @staticmethod
    def _entry(pkg, spec="", source="a.task.py", mtime=100.0):
        return {"kind": "pip", "pkg": pkg, "spec": spec,
                "raw": [pkg + spec], "source": source, "mtime": mtime}

    def test_no_conflict_single_source(self, task):
        winners, conflicts = task["_resolve"](
            [self._entry("requests", "==2.32.0")], lambda d: d["pkg"],
        )
        assert len(winners) == 1
        assert conflicts == []

    def test_no_conflict_agreement(self, task):
        winners, conflicts = task["_resolve"]([
            self._entry("requests", "==2.32.0", source="a.py", mtime=100),
            self._entry("requests", "==2.32.0", source="b.py", mtime=200),
        ], lambda d: d["pkg"])
        assert len(winners) == 1
        assert conflicts == []

    def test_conflict_pinned_versions_newer_wins(self, task):
        winners, conflicts = task["_resolve"]([
            self._entry("requests", "==2.32.0", source="old.py", mtime=100),
            self._entry("requests", "==2.30.0", source="new.py", mtime=200),
        ], lambda d: d["pkg"])
        assert len(winners) == 1
        assert winners[0]["spec"] == "==2.30.0"
        assert winners[0]["source"] == "new.py"
        assert len(conflicts) == 1
        assert conflicts[0]["pkg"] == "requests"
        assert conflicts[0]["winner"]["source"] == "new.py"
        assert conflicts[0]["losers"][0]["source"] == "old.py"

    def test_conflict_specifier_vs_pin(self, task):
        """Conflict trigger is *any* spec disagreement, including `>=X` vs `==Y`."""
        winners, conflicts = task["_resolve"]([
            self._entry("flask", ">=2.0", source="a.py", mtime=100),
            self._entry("flask", "==1.5", source="b.py", mtime=200),
        ], lambda d: d["pkg"])
        assert len(conflicts) == 1
        assert winners[0]["spec"] == "==1.5"

    def test_conflict_disjoint_ranges_resolved_not_passed_to_pip(self, task):
        """`>=2.0` AND `<1.5` is an impossible constraint that would fail
        pip resolution. The mtime rule pre-empts this — only the newer
        specifier is passed to pip, so no resolve error mid-update."""
        winners, conflicts = task["_resolve"]([
            self._entry("torch", ">=2.0", source="a.py", mtime=100),
            self._entry("torch", "<1.5", source="b.py", mtime=200),
        ], lambda d: d["pkg"])
        assert len(winners) == 1
        assert winners[0]["spec"] == "<1.5"
        assert len(conflicts) == 1

    def test_independent_packages_no_conflict(self, task):
        winners, conflicts = task["_resolve"]([
            self._entry("requests", "==1.0", source="a.py"),
            self._entry("flask", "==2.0", source="b.py"),
        ], lambda d: d["pkg"])
        assert len(winners) == 2
        assert conflicts == []


class TestResolveGitDirectives:
    """Same `_resolve` used for git, keyed by URL; branch disagreement
    (including None vs explicit) counts."""

    @staticmethod
    def _entry(url, branch=None, source="a.py", mtime=100.0):
        return {"kind": "git", "url": url, "branch": branch,
                "source": source, "mtime": mtime}

    def test_no_conflict_agreement(self, task):
        winners, conflicts = task["_resolve"]([
            self._entry("https://repo.git", "main", mtime=100),
            self._entry("https://repo.git", "main", mtime=200),
        ], lambda d: d["url"])
        assert len(winners) == 1
        assert conflicts == []

    def test_branch_conflict_newer_wins(self, task):
        winners, conflicts = task["_resolve"]([
            self._entry("https://repo.git", "old-branch", source="a.py", mtime=100),
            self._entry("https://repo.git", "new-branch", source="b.py", mtime=200),
        ], lambda d: d["url"])
        assert winners[0]["branch"] == "new-branch"
        assert conflicts[0]["url"] == "https://repo.git"
        assert conflicts[0]["winner"]["branch"] == "new-branch"

    def test_none_vs_explicit_branch_is_conflict(self, task):
        """One scriptlet specifies a branch, another doesn't — different,
        mtime wins. 'Default branch' and a named branch are not the same."""
        winners, conflicts = task["_resolve"]([
            self._entry("https://repo.git", None, source="a.py", mtime=100),
            self._entry("https://repo.git", "develop", source="b.py", mtime=200),
        ], lambda d: d["url"])
        assert winners[0]["branch"] == "develop"
        assert len(conflicts) == 1


class TestScanSetDirectives:
    """`_scan(name)` walks the per-set `data/git/{name}/` directory,
    extracting pip/git directives from every top-level *.py file. Each
    directive carries `source` (basename) and `mtime`."""

    def test_walks_set_dir_and_extracts_per_file(self, task):
        tmp = task["_tmp_path"]
        set_dir = tmp / "data" / "git" / "myset"
        set_dir.mkdir(parents=True)
        (set_dir / "alpha.task.py").write_text("#!requests==2.32.0\n")
        (set_dir / "beta.service.py").write_text("#!https://repo.git main\n")
        # Hidden file is ignored
        (set_dir / ".bootstrap.json").write_text("{}")
        # Non-python file is ignored
        (set_dir / "readme.md").write_text("#!requests==0.1.0\n")

        pip, git = task["_scan"]("myset")
        assert len(pip) == 1
        assert pip[0]["pkg"] == "requests"
        assert pip[0]["source"] == "alpha.task.py"
        assert pip[0]["mtime"] > 0
        assert len(git) == 1
        assert git[0]["url"] == "https://repo.git"

    def test_nonexistent_set_returns_empty(self, task):
        assert task["_scan"]("never_existed") == ([], [])
