"""Unit tests for app/pyops/pytest.library.py.

The library normally runs as a pyops scriptlet — it expects globals
`setname`, `appspace`, `config` injected by job_runner.setup_job_globals
for setname == 'pyops'. We exec it into a controlled namespace with
those globals stubbed so the pure functions (_scan_classes,
_resolve_node_ids, _has_cov_override, _coverage_available) are
testable directly without spinning up a runner.

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
        "appspace": str(tmp_path / "app"),
        "config": {
            "data": {"git": str(workspace / "data" / "git")},
            "runner": {"venv": str(workspace / "data" / "venv")},
        },
        "name": None,
    }
    (tmp_path / "app").mkdir()
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


class TestPytestRunValidation:
    def test_empty_list_raises(self, lib):
        with pytest.raises(ValueError, match="non-empty list"):
            lib["pytest_run"]([])

    def test_non_list_raises(self, lib):
        with pytest.raises(ValueError, match="non-empty list"):
            lib["pytest_run"]("TestX")
