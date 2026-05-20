# Library: pyops::pytest — programmatic pytest entry point for kind=test jobs.
#
# Loaded via `#!{pyops::pytest}` from a *.task.py whose __test__ block
# wants to run named test classes (or specific methods) without any
# filesystem discovery. Pyops itself stays pytest-agnostic — see
# docs/insights_app.md "Test Jobs" — so the whole framework integration
# lives here.
#
# Surface:
#
#     pytest_run(test_sets, args=None)
#         test_sets: list of strings, each either 'ClassName' (whole
#                    class) or 'ClassName::method_name' (single test).
#         args:      optional extra pytest CLI args (verbosity,
#                    reporters, ...). If any '--cov*' or '--no-cov'
#                    flag is passed the library's coverage defaults
#                    are suppressed entirely.
#
# Coverage is auto-added iff `import pytest_cov` succeeds — i.e. the
# author put `#!pytest-cov` (or equivalent) in the task's directives.
# When enabled, `.test/.coveragerc` is written on the fly with a
# `[paths]` mapping back to canonical `data/git/{setname}/...` so
# coverage reports reference source the author actually edits.
#
# Test class/method resolution is AST-based against the materialised
# library sources under `.test/src/{setname}/library/*.py`. Pyops's
# run_test_job writes those files verbatim from the imported `#!{...}`
# libraries, so line numbers + class names match the source tree.

import ast
import os
import sys


_VENV_BASE_KEY = "venv"


def _materialised_lib_dir():
    """Path to the materialised library tree for the current test job.

    Pyops's run_test_job writes the materialised sources under
    .test/src/{setname}/library/, relative to the job workspace
    (which is also the pytest cwd at invocation time)."""
    return os.path.join(".test", "src", setname, "library")


def _scan_classes():
    """AST-walk every materialised library .py file and return a dict
    of class_name → (file_path, set(method_names)). Duplicates across
    libraries raise — refuse to run with ambiguous identifiers."""
    lib_dir = _materialised_lib_dir()
    if not os.path.isdir(lib_dir):
        return {}
    index = {}
    for filename in sorted(os.listdir(lib_dir)):
        if not filename.endswith(".py"):
            continue
        file_path = os.path.join(lib_dir, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = set()
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.add(child.name)
                if node.name in index:
                    prior, _ = index[node.name]
                    if prior != file_path:
                        raise ValueError(
                            f"Test class '{node.name}' is defined in both "
                            f"{prior} and {file_path}; rename one before running."
                        )
                index[node.name] = (file_path, methods)
    return index


def _resolve_node_ids(test_sets, index):
    """Map each test_sets entry to a pytest node id, e.g.
    'TestX' → '<file>::TestX', 'TestX::test_y' → '<file>::TestX::test_y'.

    Unknown class / unknown method on a known class → ValueError with
    a concrete message — the author should see exactly which entry
    didn't resolve."""
    node_ids = []
    for entry in test_sets:
        parts = entry.split("::")
        cls_name = parts[0]
        method_name = parts[1] if len(parts) > 1 else None
        if cls_name not in index:
            raise ValueError(
                f"Test class '{cls_name}' not found in any imported "
                f"library. Known classes: {sorted(index)!r}"
            )
        file_path, methods = index[cls_name]
        if method_name is not None and method_name not in methods:
            raise ValueError(
                f"Method '{method_name}' not found on test class "
                f"'{cls_name}' ({file_path}). Known methods: "
                f"{sorted(methods)!r}"
            )
        if method_name is None:
            node_ids.append(f"{file_path}::{cls_name}")
        else:
            node_ids.append(f"{file_path}::{cls_name}::{method_name}")
    return node_ids


def _coverage_available():
    try:
        import pytest_cov  # noqa: F401
        return True
    except ImportError:
        return False


def _has_cov_override(args):
    if not args:
        return False
    return any(a == "--no-cov" or a.startswith("--cov") for a in args)


def _write_coveragerc(path):
    """Emit a .coveragerc that maps materialised paths back to
    canonical data/git/{setname}/<type>/<name>.py — coverage.py
    canonicalises at report time, so the operator sees source paths
    they recognise instead of the per-job .test/src/ tree."""
    git_base = config["data"]["git"]
    canonical_set_dir = git_base if os.path.isabs(git_base) else os.path.join(_project_root(), git_base)
    canonical_set_dir = os.path.join(canonical_set_dir, setname)
    contents = (
        "[paths]\n"
        f"task =\n"
        f"    .test/src/{setname}/task/\n"
        f"    {canonical_set_dir}/\n"
        f"library =\n"
        f"    .test/src/{setname}/library/\n"
        f"    {canonical_set_dir}/\n"
        "\n"
        "[run]\n"
        "branch = True\n"
        "parallel = True\n"
        "concurrency = thread,multiprocessing\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)


def _project_root():
    # appspace == <project_root>/app/ when running in the pyops set.
    # User-set callers don't have appspace, so fall back to the
    # workspace-relative paths the caller already provided.
    try:
        return os.path.dirname(appspace)  # type: ignore[name-defined]
    except NameError:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _subprocess_coverage_pth():
    """Drop coverage_subprocess.pth into the per-set venv's
    site-packages once (idempotent — replaced only if content drifts)
    and point COVERAGE_PROCESS_START at our .coveragerc so test
    subprocesses inherit measurement without author plumbing."""
    venv_base = config["runner"][_VENV_BASE_KEY]
    venv_root = venv_base if os.path.isabs(venv_base) else os.path.join(_project_root(), venv_base)
    venv = os.path.join(venv_root, setname)
    # Best-effort site-packages locate. Pyops's per-set venvs are
    # CPython, so the lib/python*/site-packages glob is reliable.
    candidates = []
    lib_root = os.path.join(venv, "lib")
    if os.path.isdir(lib_root):
        for entry in os.listdir(lib_root):
            if entry.startswith("python"):
                sp = os.path.join(lib_root, entry, "site-packages")
                if os.path.isdir(sp):
                    candidates.append(sp)
    if not candidates:
        return
    pth_payload = "import coverage; coverage.process_startup()\n"
    for sp in candidates:
        pth_path = os.path.join(sp, "coverage_subprocess.pth")
        try:
            existing = ""
            if os.path.isfile(pth_path):
                with open(pth_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            if existing != pth_payload:
                with open(pth_path, "w", encoding="utf-8") as f:
                    f.write(pth_payload)
        except OSError:
            # Read-only site-packages or similar — coverage still
            # works for the parent process, just not for spawned
            # subprocesses. Not worth aborting the test run over.
            pass


def pytest_run(test_sets, args=None):
    """Invoke pytest programmatically on `test_sets`.

    See module docstring for the full contract."""
    import pytest

    if not isinstance(test_sets, (list, tuple)) or not test_sets:
        raise ValueError("test_sets must be a non-empty list of identifiers")

    index = _scan_classes()
    node_ids = _resolve_node_ids(list(test_sets), index)

    extra_args = list(args or [])
    pytest_args = list(node_ids) + ["-p", "no:cacheprovider", "--no-header"]

    if _coverage_available() and not _has_cov_override(extra_args):
        coveragerc_path = os.path.join(".test", ".coveragerc")
        os.makedirs(os.path.dirname(coveragerc_path), exist_ok=True)
        _write_coveragerc(coveragerc_path)
        # Subprocess coverage: dropped once per-venv, idempotent.
        try:
            _subprocess_coverage_pth()
            os.environ["COVERAGE_PROCESS_START"] = os.path.abspath(coveragerc_path)
        except Exception:
            pass
        pytest_args.extend([
            f"--cov=.test/src/{setname}",
            f"--cov-config={coveragerc_path}",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov",
            "--cov-report=xml:coverage.xml",
        ])

    pytest_args.extend(extra_args)
    exit_code = pytest.main(pytest_args)
    if exit_code != 0:
        raise SystemExit(exit_code)
