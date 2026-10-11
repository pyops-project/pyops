"""Unit tests for app/pyops/set_update_deps.task.py — directive parsing,
conflict resolution, and the per-set filesystem scan.

The task script normally runs inside the pyops runner with `appspace`,
`config`, `validate_set_name` and `CORE_DEPS` injected as job globals (see
job_runner.setup_job_globals for the `setname == "pyops"` branch) and with
set.library.py's symbols added via the `#!{::set}` directive. The fixture
below mirrors that setup by exec'ing both files into one shared namespace,
so each pure function (`_parse_directives`, `_resolve`, `_scan`) is
callable directly without spinning up the runner. The injected `CORE_DEPS`
is a stand-in list, so a test can tell it apart from any list the task
might carry or derive itself.

End-to-end behaviour (pip subprocess, lock acquisition, .update_warnings.json
write) is covered by tests/e2e/test_sets_via_controller.py — only the
algorithmic pieces are unit-tested here.
"""

import os

import pytest


PYOPS_SET_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
))
# Stand-in for the runner-injected CORE_DEPS (app.lib.sets.CORE_DEPS at
# runtime — asserted in tests/unit/runner/test_job_globals.py).
INJECTED_CORE_DEPS = ("core-dep-a", "core-dep-b")


@pytest.fixture
def task(tmp_path):
    from app.lib.git import validate_set_name
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
        "validate_set_name": validate_set_name,
        "CORE_DEPS": INJECTED_CORE_DEPS,
        "name": None,
    }
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "data" / "git").mkdir(parents=True, exist_ok=True)
    for fname in ("set.library.py", "set_update_deps.task.py"):
        path = os.path.join(PYOPS_SET_DIR, fname)
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

    def test_requirements_file_directive_skipped(self, task):
        """`#!-r requirements.txt` names no package to aggregate (and its
        path is relative to a job workspace): Update Dependencies skips it."""
        assert task["_parse_directives"]("#!-r requirements.txt\n") == []

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


class TestScanTransitiveLibraryIncludes:
    """`_scan(name)` must follow `#!{set::name}` library-include
    directives recursively, the same way `process_imports` does at
    per-job time. Cami's regression scenario: cami's task only
    declares `#!{system::causal}` (a library include); the actual
    git directive for `causal.py` lives inside
    `system/causal.library.py`. Per-job correctly clones causal.py
    into `data/pkg/cami/causal.py` via the transitive walk. Update-
    Dependencies for cami used to skip the include and never see
    the causal.py directive -- so cami's pkg/causal.py drifted
    while update-deps cami reported "0 conflict(s)" successfully."""

    def test_resolves_cross_set_library_include(self, task):
        tmp = task["_tmp_path"]
        cami_dir = tmp / "data" / "git" / "cami"
        system_dir = tmp / "data" / "git" / "system"
        cami_dir.mkdir(parents=True)
        system_dir.mkdir(parents=True)
        (cami_dir / "service.task.py").write_text(
            "#!torch\n"
            "#!{system::causal}\n"
        )
        (system_dir / "causal.library.py").write_text(
            "#!https://git.ralph.or.at/causal/causal.py.git main\n"
            "#!pyzmq\n"
        )

        pip, git = task["_scan"]("cami")

        # The cami-direct pip directive AND the system-via-include pips
        # are both present.
        pip_pkgs = {d["pkg"] for d in pip}
        assert pip_pkgs == {"torch", "pyzmq"}, pip_pkgs
        assert len(git) == 1
        assert git[0]["url"] == "https://git.ralph.or.at/causal/causal.py.git"
        assert git[0]["branch"] == "main"
        # Source attribution points at the file where the directive
        # textually appears (the included library), not the top-level
        # scriptlet.
        assert git[0]["source"] == "causal.library.py", git[0]["source"]

    def test_resolves_same_set_library_include(self, task):
        """`#!{::name}` resolves against the current set, matching the
        per-job parser. A library inside the same set can carry
        further pip / git directives that count toward the set's
        effective install graph."""
        tmp = task["_tmp_path"]
        myset_dir = tmp / "data" / "git" / "myset"
        myset_dir.mkdir(parents=True)
        (myset_dir / "task1.task.py").write_text(
            "#!{::shared}\n"
            "#!requests\n"
        )
        (myset_dir / "shared.library.py").write_text(
            "#!numpy==2.0.0\n"
        )

        pip, _git = task["_scan"]("myset")
        pkgs = {d["pkg"] for d in pip}
        assert pkgs == {"requests", "numpy"}, pkgs

    def test_transitive_cycle_does_not_loop(self, task):
        """Cycle: A includes B, B includes A. Walk must terminate."""
        tmp = task["_tmp_path"]
        myset_dir = tmp / "data" / "git" / "myset"
        myset_dir.mkdir(parents=True)
        (myset_dir / "alpha.task.py").write_text("#!{::beta}\n#!pkg-a\n")
        (myset_dir / "beta.library.py").write_text("#!{::alpha}\n#!pkg-b\n")

        pip, _git = task["_scan"]("myset")
        pkgs = sorted(d["pkg"] for d in pip)
        # Each directive appears exactly once despite the cycle.
        assert pkgs == ["pkg-a", "pkg-b"], pkgs

    def test_missing_library_silently_skipped(self, task):
        """Update-Dependencies isn't the right place to surface a
        broken library reference -- the per-job directive processor
        would. Missing libs are skipped so the operator can still
        update the rest of the set's deps."""
        tmp = task["_tmp_path"]
        myset_dir = tmp / "data" / "git" / "myset"
        myset_dir.mkdir(parents=True)
        (myset_dir / "t.task.py").write_text(
            "#!{::nonexistent}\n"
            "#!requests\n"
        )

        pip, _git = task["_scan"]("myset")
        pkgs = {d["pkg"] for d in pip}
        assert pkgs == {"requests"}, pkgs

    def test_include_from_missing_set_skipped(self, task):
        """An include of a set that does not exist (not cloned here) is
        skipped like a missing library; the rest of the set is updated."""
        tmp = task["_tmp_path"]
        myset_dir = tmp / "data" / "git" / "myset"
        myset_dir.mkdir(parents=True)
        (myset_dir / "t.task.py").write_text("#!{nosuchset::lib}\n#!requests\n")

        pip, git = task["_scan"]("myset")
        assert [d["pkg"] for d in pip] == ["requests"]
        assert git == []

    def test_diamond_include_walked_once(self, task):
        """t includes b and c, both include d: d's directives count once."""
        tmp = task["_tmp_path"]
        other_dir = tmp / "data" / "git" / "other"
        myset_dir = tmp / "data" / "git" / "myset"
        other_dir.mkdir(parents=True)
        myset_dir.mkdir(parents=True)
        (myset_dir / "t.task.py").write_text("#!{other::b}\n#!{other::c}\n")
        (other_dir / "b.library.py").write_text("#!{::d}\n#!pkg-b\n")
        (other_dir / "c.library.py").write_text("#!{::d}\n#!pkg-c\n")
        (other_dir / "d.library.py").write_text("#!pkg-d\n")

        pip, _git = task["_scan"]("myset")
        assert sorted(d["pkg"] for d in pip) == ["pkg-b", "pkg-c", "pkg-d"]

    def test_transitive_chain_two_hops(self, task):
        """Cross-set chain A -> B -> C is fully walked."""
        tmp = task["_tmp_path"]
        for s in ("a", "b", "c"):
            (tmp / "data" / "git" / s).mkdir(parents=True)
        (tmp / "data" / "git" / "a" / "t.task.py").write_text("#!{b::lib1}\n")
        (tmp / "data" / "git" / "b" / "lib1.library.py").write_text("#!{c::lib2}\n")
        (tmp / "data" / "git" / "c" / "lib2.library.py").write_text(
            "#!https://far.git deep_branch\n"
        )

        _pip, gits = task["_scan"]("a")
        assert len(gits) == 1
        assert gits[0]["url"] == "https://far.git"
        assert gits[0]["branch"] == "deep_branch"


class TestInstallGitFetchBeforeCheckout:
    """`_install_git` is the per-directive git install path used by
    Update Dependencies. On an existing local clone, it must
    `git fetch --prune --tags origin` *before* the `git checkout
    <branch>` / `git pull` so a directive change that flips from
    `main` to a remote branch added after the original clone
    resolves on the next Update-Dependencies run. Pre-3.3.0 the
    operator had to do a manual `git fetch` in the package dir."""

    def test_existing_repo_fetches_before_checkout_and_pull(self, task, monkeypatch):
        from unittest.mock import patch
        calls = []

        def fake_run_cmd(cmd, cwd=None, **_kw):
            calls.append((tuple(cmd), cwd))

        # Skip the os.path.exists branch in _install_git by faking it
        # to True (the "existing clone" case).
        with patch.object(task["os"].path, "exists", return_value=True), \
             patch.dict(task, {"run_cmd": fake_run_cmd}):
            task["_install_git"](
                "/venv/bin/pip",
                {"url": "https://repo.git", "branch": "fix"},
                "myset",
            )

        git_cmds = [c[0] for c in calls if c[0] and c[0][0] == "git"]
        ops = [c[1] for c in git_cmds if len(c) > 1]
        # Order must be fetch -> checkout -> pull.
        fetch_idx = next((i for i, c in enumerate(git_cmds) if c[1] == "fetch"), None)
        ckout_idx = next((i for i, c in enumerate(git_cmds) if c[1] == "checkout"), None)
        pull_idx = next((i for i, c in enumerate(git_cmds) if c[1] == "pull"), None)
        assert fetch_idx is not None, f"git fetch missing: {git_cmds}"
        assert ckout_idx is not None, f"git checkout missing: {git_cmds}"
        assert pull_idx is not None, f"git pull missing: {git_cmds}"
        assert fetch_idx < ckout_idx < pull_idx, (
            "Update-Dependencies must fetch first so a new remote "
            f"branch is locally known before checkout; got order "
            f"{git_cmds[fetch_idx]}, {git_cmds[ckout_idx]}, {git_cmds[pull_idx]}"
        )

    def test_existing_repo_fetch_uses_prune_and_tags(self, task):
        from unittest.mock import patch
        calls = []

        def fake_run_cmd(cmd, cwd=None, **_kw):
            calls.append(tuple(cmd))

        with patch.object(task["os"].path, "exists", return_value=True), \
             patch.dict(task, {"run_cmd": fake_run_cmd}):
            task["_install_git"](
                "/venv/bin/pip",
                {"url": "https://repo.git"},  # no branch argument
                "myset",
            )

        fetches = [c for c in calls if c and c[:2] == ("git", "fetch")]
        assert len(fetches) == 1, f"exactly one git fetch expected: {fetches}"
        fetch = fetches[0]
        assert "--prune" in fetch, fetch
        assert "--tags" in fetch, fetch
        assert "origin" in fetch, fetch

    def test_initial_clone_does_not_fetch(self, task):
        """On the initial clone, `git clone` already gets everything
        from origin -- a follow-up fetch would be wasteful."""
        from unittest.mock import patch
        calls = []

        def fake_run_cmd(cmd, cwd=None, **_kw):
            calls.append(tuple(cmd))

        with patch.object(task["os"].path, "exists", return_value=False), \
             patch.dict(task, {"run_cmd": fake_run_cmd}):
            task["_install_git"](
                "/venv/bin/pip",
                {"url": "https://repo.git", "branch": "main"},
                "myset",
            )

        assert any(c[:2] == ("git", "clone") for c in calls), f"git clone expected: {calls}"
        assert not any(c[:2] == ("git", "fetch") for c in calls), (
            f"git fetch must not run on initial clone: {calls}"
        )


class TestMain:
    """main(): the whole Update Dependencies run with run_cmd recorded
    (no pip / git subprocesses)."""

    @staticmethod
    def _record_commands(task):
        """Replace run_cmd with a recorder that also notes whether the set's
        install lock is held while each command runs."""
        import fcntl
        lock_path = os.path.join(task["_venv_base"](), ".locks", "myset.lock")
        calls, lock_held = [], []

        def fake_run_cmd(cmd, cwd=None, check=True):
            calls.append(list(cmd))
            with open(lock_path, "a") as fd:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_held.append(True)
                else:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                    lock_held.append(False)

        task["run_cmd"] = fake_run_cmd
        return calls, lock_held

    @pytest.mark.parametrize("name, message", [
        (None, "name is required"),
        ("pyops", "'pyops' is pyops-managed"),
        ("ghost", "Set 'ghost' not found."),
    ], ids=["missing", "pyops", "unknown"])
    def test_rejects_invalid_set(self, task, name, message):
        calls, _ = self._record_commands(task)
        task["name"] = name
        with pytest.raises(ValueError, match=message):
            task["main"]()
        assert calls == []

    @pytest.mark.parametrize("venv_exists", [False, True], ids=["new_venv", "existing_venv"])
    def test_upgrades_set_and_writes_warnings(self, task, capsys, venv_exists):
        """Ensures the venv (created only if missing), upgrades pip and the
        core deps — exactly the CORE_DEPS the runner injects, the list a set
        venv is created with — installs the newest-mtime winners of
        conflicting pip pins and git branches, all under the set's install
        lock, and writes the conflict summary /health reads."""
        import json
        import sys
        tmp = task["_tmp_path"]
        set_dir = tmp / "data" / "git" / "myset"
        set_dir.mkdir(parents=True)
        (set_dir / "old.task.py").write_text("#!requests==2.0\n#!https://h.example/g/tool.git main\n")
        (set_dir / "new.task.py").write_text("#!requests==2.1\n#!https://h.example/g/tool.git dev\n")
        os.utime(set_dir / "old.task.py", (1000, 1000))
        os.utime(set_dir / "new.task.py", (2000, 2000))
        venv = tmp / "data" / "venv" / "myset"
        pkg = tmp / "data" / "pkg" / "myset"
        if venv_exists:
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").touch()
        calls, lock_held = self._record_commands(task)
        task["name"] = "myset"

        task["main"]()

        pip = str(venv / "bin" / "pip")
        core_deps = list(INJECTED_CORE_DEPS)
        assert calls == ([] if venv_exists else [[sys.executable, "-m", "venv", str(venv)]]) + [
            [pip, "install", *core_deps],
            [pip, "install", "--upgrade", "pip", "setuptools<82", "wheel"],
            [pip, "install", "--upgrade", *core_deps],
            [pip, "install", "--no-cache-dir", "--upgrade", "--upgrade-strategy", "eager", "requests==2.1"],
            ["git", "clone", "--branch", "dev", "https://h.example/g/tool.git", str(pkg / "tool")],
            [pip, "install", "--no-cache-dir", "--upgrade", "-e", str(pkg / "tool")],
        ]
        assert lock_held == [True] * len(calls)
        with open(venv / ".update_warnings.json") as f:
            summary = json.load(f)
        assert summary == {
            "set": "myset",
            "conflicts": {
                "pip": [{
                    "pkg": "requests",
                    "winner": {"source": "new.task.py", "mtime": 2000.0, "spec": "==2.1"},
                    "losers": [{"source": "old.task.py", "mtime": 1000.0, "spec": "==2.0"}],
                }],
                "git": [{
                    "url": "https://h.example/g/tool.git",
                    "winner": {"source": "new.task.py", "mtime": 2000.0, "branch": "dev"},
                    "losers": [{"source": "old.task.py", "mtime": 1000.0, "branch": "main"}],
                }],
            },
            "installed": {
                "pip": [{"pkg": "requests", "spec": "==2.1", "source": "new.task.py"}],
                "git": [{"url": "https://h.example/g/tool.git", "branch": "dev", "source": "new.task.py"}],
            },
        }
        out = capsys.readouterr().out
        assert "pip conflict: requests → chose ==2.1 from new.task.py" in out
        assert "git conflict: https://h.example/g/tool.git → chose branch=dev from new.task.py" in out
        assert "Update completed for 'myset' — 2 conflict(s)." in out


class TestLibraryIncludeResolution:
    """`#!{set::name}` resolves to the scriptlet file `name.<type>.py` —
    never to another scriptlet whose name merely starts with `name.`
    (e.g. `causal.x.task.py` from a cloned repository), whatever order the
    directory lists in."""

    def test_exact_file_resolved_whatever_the_listing_order(self, task, monkeypatch):
        tmp = task["_tmp_path"]
        system = tmp / "data" / "git" / "system"
        system.mkdir(parents=True)
        (system / "causal.library.py").write_text("#!https://h.example/g/causal.git\n")
        (system / "causal.x.task.py").write_text("#!unrelated\n")
        cami = tmp / "data" / "git" / "cami"
        cami.mkdir(parents=True)
        (cami / "main.task.py").write_text("#!{system::causal}\n")
        real_listdir = os.listdir
        # The clashing file first, as an unsorted listing may return it.
        monkeypatch.setattr(os, "listdir", lambda path: sorted(real_listdir(path), reverse=True))

        pips, gits = task["_scan"]("cami")

        assert pips == []
        assert [(g["url"], g["source"]) for g in gits] == [("https://h.example/g/causal.git", "causal.library.py")]
