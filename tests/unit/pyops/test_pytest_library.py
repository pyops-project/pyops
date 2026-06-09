"""Unit tests for app/pyops/pytest.library.py.

The library runs merged into the consuming task's globals (libraries
are `exec`'d into the task's `glob`), so it sees the same job-runner
injected globals every job gets — in particular `setname` and the
absolute `gitspace` git root. It loads into *user* sets via
`#!{pyops::pytest}`, where setname != 'pyops', so it must NOT depend on
the `config`/`appspace` globals the runner injects only for the pyops
set. We exec it into a controlled namespace mirroring that contract so
the helpers are testable directly without spinning up a runner.

The full kind=test lifecycle (materialise → exec → pytest.main →
completion) is covered by tests/integration/test_test_kind.py.
"""
import os
import textwrap

import pytest


PYOPS_SET_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "pyops"
))


@pytest.fixture
def lib(tmp_path):
    """Materialise pyops::pytest into a tmp workspace, exec'd with the
    same globals the runner would inject. Returns the populated dict."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    test_src = workspace / ".test" / "src" / "myset" / "library"
    test_src.mkdir(parents=True)

    glob = {
        "__name__": "pyops_pytest_loaded",
        "setname": "myset",
        # Absolute git root, exactly as job_runner.setup_job_globals
        # injects it for every job (user sets included). No `config`
        # or `appspace` — those are pyops-set-only and the library
        # must not reach for them.
        "gitspace": str(workspace / "data" / "git"),
        "name": None,
    }
    cwd = os.getcwd()
    try:
        os.chdir(workspace)
        path = os.path.join(PYOPS_SET_DIR, "pytest.library.py")
        with open(path) as f:
            src = f.read()
        exec(compile(src, path, "exec"), glob)
        glob["_workspace"] = str(workspace)
        glob["_test_src"] = str(test_src)
        yield glob
    finally:
        os.chdir(cwd)


class TestScanClasses:
    def test_finds_classes_and_methods(self, lib):
        test_src = lib["_test_src"]
        (open(os.path.join(test_src, "unit_tests.py"), "w")
         .write(textwrap.dedent("""
            class TestWork:
                def test_positive(self): pass
                def test_zero(self): pass
            class TestEdge:
                def test_negative(self): pass
        """).strip() + "\n"))
        idx = lib["_scan_classes"]()
        assert set(idx) == {"TestWork", "TestEdge"}
        _, work_methods = idx["TestWork"]
        assert work_methods == {"test_positive", "test_zero"}

    def test_empty_dir_returns_empty(self, lib):
        assert lib["_scan_classes"]() == {}

    def test_duplicate_class_across_files_raises(self, lib):
        test_src = lib["_test_src"]
        for filename in ("a.py", "b.py"):
            with open(os.path.join(test_src, filename), "w") as f:
                f.write("class TestDup:\n    def test_x(self): pass\n")
        with pytest.raises(ValueError, match="defined in both"):
            lib["_scan_classes"]()


class TestResolveNodeIds:
    def _index(self, lib):
        test_src = lib["_test_src"]
        path = os.path.join(test_src, "unit_tests.py")
        with open(path, "w") as f:
            f.write(textwrap.dedent("""
                class TestWork:
                    def test_positive(self): pass
                    def test_zero(self): pass
            """).strip() + "\n")
        return lib["_scan_classes"]()

    def test_whole_class(self, lib):
        idx = self._index(lib)
        ids = lib["_resolve_node_ids"](["TestWork"], idx)
        assert len(ids) == 1
        assert ids[0].endswith("unit_tests.py::TestWork")

    def test_single_method(self, lib):
        idx = self._index(lib)
        ids = lib["_resolve_node_ids"](["TestWork::test_zero"], idx)
        assert ids[0].endswith("unit_tests.py::TestWork::test_zero")

    def test_unknown_class_raises(self, lib):
        idx = self._index(lib)
        with pytest.raises(ValueError, match="Test class 'Nope' not found"):
            lib["_resolve_node_ids"](["Nope"], idx)

    def test_unknown_method_on_known_class_raises(self, lib):
        idx = self._index(lib)
        with pytest.raises(ValueError, match="Method 'test_missing' not found"):
            lib["_resolve_node_ids"](["TestWork::test_missing"], idx)


class TestCovOverrideDetection:
    @pytest.mark.parametrize("args", [
        ["--no-cov"],
        ["--cov=mypkg"],
        ["-v", "--cov-report=term"],
        ["--cov-config=other.rc"],
    ])
    def test_detects_override(self, lib, args):
        assert lib["_has_cov_override"](args) is True

    @pytest.mark.parametrize("args", [
        [],
        ["-v"],
        ["--tb=short", "-x"],
        None,
    ])
    def test_no_override(self, lib, args):
        assert lib["_has_cov_override"](args) is False


class TestWriteCoveragerc:
    """Regression: _write_coveragerc must resolve the set's canonical
    git checkout from the universally-injected `gitspace` global, not
    from a `config` global that the runner injects only for the pyops
    set. The old `config["data"]["git"]` lookup raised NameError on
    every user set that loaded this library via `#!{pyops::pytest}`."""

    def test_uses_gitspace_not_config(self, lib, tmp_path):
        out = tmp_path / "cov.rc"
        # Must not raise NameError('config') — that was the bug.
        lib["_write_coveragerc"](str(out))
        contents = out.read_text()
        # canonical_set_dir == gitspace/setname
        expected = os.path.join(lib["gitspace"], "myset")
        assert "[paths]" in contents
        assert f"    {expected}/\n" in contents
        # Both task and library sections map to the same canonical dir.
        assert contents.count(f"    {expected}/\n") == 2
        assert ".test/src/myset/task/" in contents
        assert ".test/src/myset/library/" in contents

    def test_no_config_global_in_namespace(self, lib):
        # The library must not depend on a `config` global at all.
        assert "config" not in lib


class TestSubprocessCoveragePth:
    """Regression: _subprocess_coverage_pth must locate the per-set
    venv via `sys.prefix` (the interpreter the job already runs in),
    not by reconstructing a path from the pyops-set-only `config`
    global."""

    def test_writes_pth_into_sys_prefix_site_packages(self, lib, tmp_path, monkeypatch):
        import sys
        fake_venv = tmp_path / "venv"
        sp = fake_venv / "lib" / "python3.13" / "site-packages"
        sp.mkdir(parents=True)
        monkeypatch.setattr(sys, "prefix", str(fake_venv))
        lib["_subprocess_coverage_pth"]()
        pth = sp / "coverage_subprocess.pth"
        assert pth.is_file()
        assert pth.read_text() == "import coverage; coverage.process_startup()\n"

    def test_idempotent_no_rewrite_when_unchanged(self, lib, tmp_path, monkeypatch):
        import sys
        fake_venv = tmp_path / "venv"
        sp = fake_venv / "lib" / "python3.13" / "site-packages"
        sp.mkdir(parents=True)
        pth = sp / "coverage_subprocess.pth"
        pth.write_text("import coverage; coverage.process_startup()\n")
        before = pth.stat().st_mtime_ns
        monkeypatch.setattr(sys, "prefix", str(fake_venv))
        lib["_subprocess_coverage_pth"]()
        assert pth.stat().st_mtime_ns == before

    def test_no_site_packages_is_silent(self, lib, tmp_path, monkeypatch):
        import sys
        fake_venv = tmp_path / "empty_venv"
        fake_venv.mkdir()
        monkeypatch.setattr(sys, "prefix", str(fake_venv))
        # No lib/python*/site-packages — must return without error.
        lib["_subprocess_coverage_pth"]()


class TestPytestRunValidation:
    def test_empty_list_raises(self, lib):
        with pytest.raises(ValueError, match="non-empty list"):
            lib["pytest_run"]([])

    def test_non_list_raises(self, lib):
        with pytest.raises(ValueError, match="non-empty list"):
            lib["pytest_run"]("TestX")


class TestSeedTestModules:
    """Verifies the namespace-bridge function in isolation: pre-register
    a synthetic test file in sys.modules with a seed dict + exec the
    file body against it. Asserts the file body sees the seeded
    symbols (so module-level decorators / class bodies resolve them
    by bare name) and that the resulting module ends up in
    sys.modules with both the seed and the file's own definitions."""

    def test_seeds_caller_globals_into_module(self, lib, tmp_path):
        test_file = tmp_path / "stub_tests.py"
        test_file.write_text(
            # Refers to `injected_value` by bare name — only resolvable
            # if the seed lands in module globals before this exec runs.
            "captured = injected_value * 2\n"
            "class TestStub:\n"
            "    def test_ok(self): pass\n"
        )
        seed = {"injected_value": 7}
        import sys
        sys.modules.pop("stub_tests", None)
        try:
            registered = lib["_seed_test_modules"]([str(test_file)], seed)
            assert registered == ["stub_tests"]
            mod = sys.modules["stub_tests"]
            assert mod.injected_value == 7
            assert mod.captured == 14
            assert mod.__file__ == str(test_file)
            assert hasattr(mod, "TestStub")
        finally:
            sys.modules.pop("stub_tests", None)

    def test_dunder_entries_are_not_seeded(self, lib, tmp_path):
        """`__name__`, `__builtins__`, `__file__` etc. must remain the
        synthesised module's own — otherwise the test file's exec
        would clobber its own identity with the caller's."""
        test_file = tmp_path / "name_tests.py"
        test_file.write_text("captured_name = __name__\n")
        seed = {"__name__": "caller_module", "__builtins__": None, "regular": 1}
        import sys
        sys.modules.pop("name_tests", None)
        try:
            lib["_seed_test_modules"]([str(test_file)], seed)
            mod = sys.modules["name_tests"]
            assert mod.captured_name == "name_tests"
            assert mod.regular == 1
        finally:
            sys.modules.pop("name_tests", None)

    def test_skip_if_already_registered(self, lib, tmp_path):
        """Re-entry safety: if the stem is already in sys.modules
        (e.g. from a previous pytest_run in the same job), leave it
        alone and exclude it from the returned list."""
        test_file = tmp_path / "already_tests.py"
        test_file.write_text("counter = counter + 1\n")
        import sys
        import types
        existing = types.ModuleType("already_tests")
        existing.counter = 99
        sys.modules["already_tests"] = existing
        try:
            registered = lib["_seed_test_modules"]([str(test_file)], {"counter": 0})
            assert registered == []
            # Existing module unchanged — counter was 99 and exec did not run.
            assert sys.modules["already_tests"].counter == 99
        finally:
            sys.modules.pop("already_tests", None)

    def test_production_lib_filenames_not_pre_registered(self, lib, tmp_path):
        """Only files explicitly passed in are touched — sibling
        production libraries that happen to share their stem with a
        stdlib / venv module (e.g. `os.py`) must NOT be pre-registered
        from this function so a test's `import os` keeps resolving to
        the stdlib."""
        test_file = tmp_path / "consumer_tests.py"
        test_file.write_text("class TestNoop:\n    def test_pass(self): pass\n")
        import sys
        sys_os_before = sys.modules["os"]
        sys.modules.pop("consumer_tests", None)
        try:
            lib["_seed_test_modules"]([str(test_file)], {})
            assert sys.modules["os"] is sys_os_before
            assert "consumer_tests" in sys.modules
        finally:
            sys.modules.pop("consumer_tests", None)
