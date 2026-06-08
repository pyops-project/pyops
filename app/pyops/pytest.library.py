#!pytest
# Library: pyops::pytest — programmatic pytest entry point for kind=test jobs.
#
# The `#!pytest` directive at the top of this file installs pytest into
# the consuming set's venv when this library is loaded via
# `#!{pyops::pytest}`. So callers don't need their own `#!pytest` line.
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
# Convention: `pytest_run` must be called directly from the task's
# `if __name__ == "__test__":` block. The implementation reads the
# caller's `f_globals` (one frame up) to obtain the task's
# directive-merged namespace — wrapping the call in a helper function
# would shift that frame to the helper's locals and break the
# namespace bridge.
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
#
# Namespace contract — pyops symbols in test files:
#
# The pyops directive model merges every `#!{set::name}` library's
# top-level symbols into the consuming task's exec globals; the same
# is true of pyops's own injected globals (`setname`, `workspace`,
# `data`, `listener`, `control`, `gitspace`, ...). `pytest_run`
# captures that full namespace via `sys._getframe(1).f_globals` and
# pre-registers each materialised test library file in `sys.modules`
# with the namespace seeded into the module's `__dict__` before the
# file body runs. The test file then exec's into the seeded dict, so
# test classes / fixtures / module-level decorators reference any
# pyops-injected or directive-merged symbol by bare name — the same
# semantics as the task body itself — and never need a classical
# `from X import Y` for pyops-side content.
#
# Classical imports inside test files are still valid and necessary
# for anything reached via Python's import machinery (stdlib, the
# per-set venv, framework packages installed via `#!<pip-pkg>` /
# `#!<git-url>` directives). The bridge applies only to symbols
# carried by the directive merge — which by design do not exist as
# `sys.modules` entries.
#
# Only files referenced by the resolved test-set node IDs are
# pre-registered. Production-library files in the materialised tree
# (e.g. `system::os` materialised as `os.py`) are not touched, so
# they cannot accidentally shadow stdlib or framework modules when a
# test does `import os` for stdlib reasons.
#
# Namespace contract — pyops directive symbols in test files:
#
# `pytest_run` captures the calling task's exec namespace (where every
# `#!{set::name}` directive has already merged its library's top-level
# symbols) and pre-registers each materialised test library file in
# `sys.modules` with those symbols seeded into the module's __dict__
# before the file body runs. The test file then exec's into the seeded
# dict, so test classes / fixtures / module-level decorators reference
# any directive-merged symbol by bare name — same semantics as the
# task body itself — without classical `from X import Y` boilerplate
# and without forcing pyops library names to dodge stdlib / framework
# collisions (`system::os`, `system::causal`, ...).
#
# `pytest.main(node_ids)` then runs pytest collection; pytest's default
# import path machinery returns the pre-registered module from
# `sys.modules` rather than re-importing the file fresh, so the
# already-seeded namespace survives into test execution.

import ast
import os
import sys
import types
import types


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


def _seed_test_modules(test_files, caller_globals):
    """Pre-register each test library file as a Python module in
    `sys.modules`, seeded with the caller task's directive-merged
    namespace (everything non-dunder in `caller_globals`).

    The seeded dict is exec'd against the test file source, so
    module-level references inside the test file (decorators, class
    bodies, fixture factories, helpers) resolve against the task's
    namespace exactly as the task body itself would.

    Only the files in `test_files` — derived from the resolved test-set
    node IDs — are pre-registered. Production-library files that happen
    to share their stem with a stdlib / venv module (e.g. a set whose
    `os` library materialises as `os.py`) are not touched, so a test's
    `import os` continues to resolve to the stdlib module.

    Skips test files whose stem is already in `sys.modules`, keeping
    `pytest_run` idempotent when invoked more than once in the same
    job (re-entry, retries, manual repeat).

    Returns the list of stems pre-registered — primarily for tests
    that need to verify the bridge fired."""
    seed = {k: v for k, v in caller_globals.items() if not k.startswith('__')}
    registered = []
    for file_path in test_files:
        stem = os.path.splitext(os.path.basename(file_path))[0]
        if stem in sys.modules:
            continue
        mod = types.ModuleType(stem)
        mod.__file__ = file_path
        mod.__dict__.update(seed)
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        exec(compile(source, file_path, 'exec'), mod.__dict__)
        sys.modules[stem] = mod
        registered.append(stem)
    return registered


def pytest_run(test_sets, args=None):
    """Invoke pytest programmatically on `test_sets`.

    See module docstring for the full contract — in particular the
    "Namespace contract" section, which spells out why this function
    must be called directly from a task's `if __name__ == "__test__":`
    block and not via a helper indirection."""
    import pytest

    if not isinstance(test_sets, (list, tuple)) or not test_sets:
        raise ValueError("test_sets must be a non-empty list of identifiers")

    index = _scan_classes()
    node_ids = _resolve_node_ids(list(test_sets), index)

    # Seed the test files referenced by the resolved node IDs with the
    # calling task's directive-merged + pyops-injected namespace so
    # test bodies can reference pyops symbols by bare name. See the
    # "Namespace contract" section of this module's docstring.
    caller_globals = sys._getframe(1).f_globals
    test_files = sorted({fp for fp, _methods in (index[entry.split('::')[0]] for entry in test_sets)})
    _seed_test_modules(test_files, caller_globals)

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
