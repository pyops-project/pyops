"""Tests for the operator-triggered "Update Dependencies" flow.

Covers:
  - `_parse_directives`: extracts pip / git directives from scriptlet text,
    skips library imports (`#!{set::name}`), tolerates blank/garbage lines.
  - `_resolve_pip_directives`: aggregates by canonical pkg name, no-conflict
    case unifies, disagreement triggers newest-mtime-wins.
  - `_resolve_git_directives`: same for git, branch disagreement included
    (None vs explicit counts as disagreement).
  - `update_set_environment` end-to-end with mocked subprocess: walks
    scriptlets, runs core-deps upgrade, runs synthetic directive install
    via `process_install_lines`, writes `.update_warnings.json`.
"""

import json
import os
import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect `conf_data`/`conf_runner` to a per-test temp tree so the
    update flow writes its log files / venv stubs under `tmp_path`."""
    git_base = tmp_path / "git"
    venv_base = tmp_path / "venv"
    pkg_base = tmp_path / "pkg"
    git_base.mkdir()
    venv_base.mkdir()
    pkg_base.mkdir()

    monkeypatch.setattr("app.lib.sets.conf_data", lambda: {
        "git": str(git_base),
        "jobs": str(tmp_path / "jobs"),
    })
    monkeypatch.setattr("app.lib.sets.conf_runner", lambda: {
        "venv": str(venv_base),
        "pkg": str(pkg_base),
    })
    return {"git": git_base, "venv": venv_base, "pkg": pkg_base, "tmp": tmp_path}


class TestParseDirectives:
    def test_pip_simple(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!requests\n")
        assert result == [
            {"kind": "pip", "pkg": "requests", "spec": "", "extra": [], "raw": ["requests"]}
        ]

    def test_pip_with_version_pin(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!requests==2.32.0\n")
        assert result[0]["pkg"] == "requests"
        assert result[0]["spec"] == "==2.32.0"

    def test_pip_with_specifier_range(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!flask>=2.0,<3.0\n")
        assert result[0]["pkg"] == "flask"
        assert result[0]["spec"] == ">=2.0,<3.0"

    def test_pip_with_index_url(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!torch --index-url https://example.com/cpu\n")
        assert result[0]["pkg"] == "torch"
        assert result[0]["extra"] == ["--index-url", "https://example.com/cpu"]

    def test_canonicalize_package_name(self):
        """PEP 503 normalization: underscores → hyphens, lowercase. Two
        scriptlets writing `#!My_Package` and `#!my-package` must aggregate
        as the same package — otherwise conflict resolution misses them."""
        from app.lib.sets import _parse_directives
        a = _parse_directives("#!My_Package==1.0\n")
        b = _parse_directives("#!my-package==2.0\n")
        assert a[0]["pkg"] == b[0]["pkg"] == "my-package"

    def test_git_simple(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!https://github.com/x/y.git\n")
        assert result == [
            {"kind": "git", "url": "https://github.com/x/y.git", "branch": None}
        ]

    def test_git_with_branch(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!https://github.com/x/y.git develop\n")
        assert result[0]["branch"] == "develop"

    def test_ssh_git_url(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!git@github.com:x/y.git\n")
        assert result[0]["kind"] == "git"
        assert result[0]["url"] == "git@github.com:x/y.git"

    def test_library_import_skipped(self):
        """`#!{set::name}` is a library import (process_imports), not an
        install directive — must not appear in the output."""
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!{otherset::shared_lib}\n")
        assert result == []

    def test_namespaced_library_skipped(self):
        from app.lib.sets import _parse_directives
        result = _parse_directives("#!otherset::shared_lib\n")
        assert result == []

    def test_blank_and_garbage_lines_ignored(self):
        from app.lib.sets import _parse_directives
        text = (
            "import os\n"
            "\n"
            "#!\n"
            "#! \n"
            "x = 1\n"
        )
        assert _parse_directives(text) == []

    def test_multiple_directives_in_one_text(self):
        from app.lib.sets import _parse_directives
        text = (
            "#!requests\n"
            "#!https://github.com/a/b.git main\n"
            "code = 1\n"
            "#!numpy>=1.20\n"
        )
        result = _parse_directives(text)
        assert len(result) == 3
        assert [d["kind"] for d in result] == ["pip", "git", "pip"]


class TestResolvePipDirectives:
    def _entry(self, pkg, spec="", extra=(), source="a.task.py", mtime=100.0):
        return {"kind": "pip", "pkg": pkg, "spec": spec, "extra": list(extra),
                "raw": [pkg + spec, *extra], "source": source, "mtime": mtime}

    def test_no_conflict_single_source(self):
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([self._entry("requests", "==2.32.0")])
        assert len(resolved) == 1
        assert conflicts == []

    def test_no_conflict_agreement(self):
        """Two scriptlets pinning the same version → no conflict, single resolved."""
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([
            self._entry("requests", "==2.32.0", source="a.py", mtime=100),
            self._entry("requests", "==2.32.0", source="b.py", mtime=200),
        ])
        assert len(resolved) == 1
        assert conflicts == []

    def test_conflict_pinned_versions_newer_wins(self):
        """Two scriptlets pin different versions → conflict, newer mtime wins."""
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([
            self._entry("requests", "==2.32.0", source="old.py", mtime=100),
            self._entry("requests", "==2.30.0", source="new.py", mtime=200),
        ])
        assert len(resolved) == 1
        assert resolved[0]["spec"] == "==2.30.0"
        assert resolved[0]["source"] == "new.py"
        assert len(conflicts) == 1
        assert conflicts[0]["pkg"] == "requests"
        assert conflicts[0]["winner"]["source"] == "new.py"
        assert conflicts[0]["losers"][0]["source"] == "old.py"

    def test_conflict_specifier_vs_pin(self):
        """Conflict trigger is *any* disagreement, including `>=X` vs `==Y`."""
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([
            self._entry("flask", ">=2.0", source="a.py", mtime=100),
            self._entry("flask", "==1.5", source="b.py", mtime=200),
        ])
        assert len(conflicts) == 1
        assert resolved[0]["spec"] == "==1.5"

    def test_conflict_disjoint_ranges_resolved_not_passed_to_pip(self):
        """`>=2.0` AND `<1.5` is an impossible constraint that would fail
        pip resolution. The mtime rule pre-empts this — only the newer
        specifier is passed to pip, so no resolve error mid-update."""
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([
            self._entry("torch", ">=2.0", source="a.py", mtime=100),
            self._entry("torch", "<1.5", source="b.py", mtime=200),
        ])
        assert len(resolved) == 1
        assert resolved[0]["spec"] == "<1.5"
        assert len(conflicts) == 1

    def test_independent_packages_no_conflict(self):
        from app.lib.sets import _resolve_pip_directives
        resolved, conflicts = _resolve_pip_directives([
            self._entry("requests", "==1.0", source="a.py"),
            self._entry("flask", "==2.0", source="b.py"),
        ])
        assert len(resolved) == 2
        assert conflicts == []


class TestResolveGitDirectives:
    def _entry(self, url, branch=None, source="a.py", mtime=100.0):
        return {"kind": "git", "url": url, "branch": branch,
                "source": source, "mtime": mtime}

    def test_no_conflict_agreement(self):
        from app.lib.sets import _resolve_git_directives
        resolved, conflicts = _resolve_git_directives([
            self._entry("https://repo.git", "main", mtime=100),
            self._entry("https://repo.git", "main", mtime=200),
        ])
        assert len(resolved) == 1
        assert conflicts == []

    def test_branch_conflict_newer_wins(self):
        from app.lib.sets import _resolve_git_directives
        resolved, conflicts = _resolve_git_directives([
            self._entry("https://repo.git", "old-branch", source="a.py", mtime=100),
            self._entry("https://repo.git", "new-branch", source="b.py", mtime=200),
        ])
        assert resolved[0]["branch"] == "new-branch"
        assert conflicts[0]["url"] == "https://repo.git"
        assert conflicts[0]["winner"]["branch"] == "new-branch"

    def test_none_vs_explicit_branch_is_conflict(self):
        """One scriptlet specifies a branch, another doesn't → different,
        mtime wins. The "default branch" semantics differ from a named
        branch, so this is a real disagreement."""
        from app.lib.sets import _resolve_git_directives
        resolved, conflicts = _resolve_git_directives([
            self._entry("https://repo.git", None, source="a.py", mtime=100),
            self._entry("https://repo.git", "develop", source="b.py", mtime=200),
        ])
        assert resolved[0]["branch"] == "develop"
        assert len(conflicts) == 1


class TestScanSetDirectives:
    def test_walks_set_dir_and_extracts_per_file(self, isolated_dirs):
        """Each scriptlet's directives are annotated with `source` (basename)
        and `mtime` (so conflict resolution can compare ages)."""
        from app.lib.sets import _scan_set_directives
        set_dir = isolated_dirs["git"] / "myset"
        set_dir.mkdir()
        (set_dir / "alpha.task.py").write_text("#!requests==2.32.0\n")
        (set_dir / "beta.service.py").write_text("#!https://repo.git main\n")
        # Hidden file should be ignored
        (set_dir / ".bootstrap.json").write_text("{}")
        # Non-python file ignored
        (set_dir / "readme.md").write_text("#!requests==0.1.0\n")

        pip, git, skipped = _scan_set_directives("myset")
        assert len(pip) == 1
        assert pip[0]["pkg"] == "requests"
        assert pip[0]["source"] == "alpha.task.py"
        assert pip[0]["mtime"] > 0
        assert len(git) == 1
        assert git[0]["url"] == "https://repo.git"

    def test_nonexistent_set_returns_empty(self, isolated_dirs):
        from app.lib.sets import _scan_set_directives
        assert _scan_set_directives("nonexistent") == ([], [], [])


class TestUpdateSetEnvironmentEndToEnd:
    @patch("app.lib.sets.subprocess.check_call")
    @patch("app.lib.job.subprocess.run")
    def test_full_flow_writes_warnings_file_and_runs_pip(
        self, mock_job_run, mock_check_call, isolated_dirs,
    ):
        """End-to-end: scriptlets with one conflict → warnings file written,
        pip subprocess invoked with --upgrade for core deps and per-package."""
        from app.lib import sets

        set_dir = isolated_dirs["git"] / "myset"
        set_dir.mkdir()
        # Create venv stub so ensure_set_environment doesn't try to run python -m venv
        venv_bin = isolated_dirs["venv"] / "myset" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("")  # exists → skip venv create
        # Two scriptlets pinning conflicting versions
        old = set_dir / "old.task.py"
        new = set_dir / "new.task.py"
        old.write_text("#!requests==2.0.0\n")
        new.write_text("#!requests==3.0.0\n")
        # Make `new` clearly newer
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        mock_job_run.return_value = MagicMock(returncode=0)

        summary = sets.update_set_environment("myset")

        # Conflict was recorded and `new` won
        pip_conflicts = summary["conflicts"]["pip"]
        assert len(pip_conflicts) == 1
        assert pip_conflicts[0]["pkg"] == "requests"
        assert pip_conflicts[0]["winner"]["source"] == "new.task.py"

        # warnings file written
        warnings_path = set_dir / ".update_warnings.json"
        assert warnings_path.exists()
        data = json.loads(warnings_path.read_text())
        assert data["set"] == "myset"
        assert len(data["conflicts"]["pip"]) == 1

        # Core deps upgrade invoked (two-step: pip+setuptools+wheel, then CORE_DEPS)
        upgrade_calls = [c for c in mock_check_call.call_args_list if '--upgrade' in (c.args[0] or [])]
        assert len(upgrade_calls) >= 2

        # Per-package pip install invoked in upgrade mode (via process_install_lines)
        pip_install_calls = [c for c in mock_job_run.call_args_list
                             if isinstance(c.args[0], list) and 'install' in c.args[0] and '--upgrade' in c.args[0]]
        assert any('requests==3.0.0' in c.args[0] for c in pip_install_calls), \
            f"expected pip install --upgrade requests==3.0.0; saw {[c.args[0] for c in pip_install_calls]}"

    @patch("app.lib.sets.subprocess.check_call")
    @patch("app.lib.job.subprocess.run")
    def test_clean_set_writes_empty_warnings(self, mock_job_run, mock_check_call, isolated_dirs):
        from app.lib import sets

        set_dir = isolated_dirs["git"] / "cleanset"
        set_dir.mkdir()
        venv_bin = isolated_dirs["venv"] / "cleanset" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("")
        (set_dir / "one.task.py").write_text("#!requests==2.32.0\n")
        (set_dir / "two.task.py").write_text("#!requests==2.32.0\n")  # same version

        mock_job_run.return_value = MagicMock(returncode=0)

        summary = sets.update_set_environment("cleanset")
        assert summary["conflicts"]["pip"] == []
        assert summary["conflicts"]["git"] == []
        warnings = json.loads((set_dir / ".update_warnings.json").read_text())
        assert warnings["conflicts"]["pip"] == []
