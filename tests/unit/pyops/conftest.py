"""Fixtures shared by the pyops-set scriptlet tests.

A pyops-set job runs a scriptlet of app/pyops/ with the globals the job
runner builds (`job_runner.setup_job_globals`, including the
`setname == "pyops"` extras `appspace`, `config`, `validate_set_name` and
`CORE_DEPS`), the job parameter's variables, and set.library.py exec'd
for its `#!{::set}` include. `pyops_job` builds exactly that — the shared
factory of tests/conftest.py on the `pyops_install` data layout.
`hermetic_git` keeps the git commands real tasks run independent of the
host's git configuration."""

import pytest


@pytest.fixture
def pyops_install(tmp_path):
    """Data layout of a pyops install under tmp_path: the git, jobs, venv
    and pkg bases, as the config sections the job runner hands to
    pyops-set jobs."""
    data = tmp_path / "data"
    for sub in ("git", "jobs", "venv", "pkg"):
        (data / sub).mkdir(parents=True)
    return {
        "root": tmp_path,
        "git": data / "git",
        "jobs": data / "jobs",
        "venv": data / "venv",
        "pkg": data / "pkg",
        "data": {"git": str(data / "git"), "jobs": str(data / "jobs")},
        "runner": {"venv": str(data / "venv"), "pkg": str(data / "pkg")},
    }


@pytest.fixture
def pyops_job(pyops_job, pyops_install):
    """The shared `pyops_job` factory (tests/conftest.py) on the
    `pyops_install` layout: `pyops_job(script, kind="task", run=False,
    **bound)` returns the globals of a pyops-set job running
    `<script>.task.py`."""
    def build(script, **kwargs):
        return pyops_job(script, pyops_install, **kwargs)

    return build


@pytest.fixture
def hermetic_git(tmp_path, monkeypatch):
    """Git commands — the test's and the tasks' — read no host or user
    configuration: an empty global config under tmp_path (the test may
    add to it, e.g. `url.<base>.insteadOf`), no system config, and a
    fixed default branch `main`. Returns a `git(*args, cwd=None)` helper
    that runs git and returns its stripped stdout."""
    import subprocess
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git.config_path = global_config
    return git


@pytest.fixture
def make_set(pyops_install, hermetic_git):
    """Factory: `make_set(name, files=None, remote=None)` creates the user
    set `name` — a git repository under the git base with a committer
    identity and one commit holding `files` ({relative path: content};
    default a README.md). With `remote` (a bare repository path), the set
    is a clone of it whose commit was pushed, tracking `origin/main`.
    Returns the set's directory."""
    git = hermetic_git

    def build(name, files=None, remote=None):
        target = pyops_install["git"] / name
        if remote is not None:
            git("init", "--bare", str(remote))
            git("clone", str(remote), str(target))
        else:
            git("init", str(target))
        git("config", "user.name", "Set Owner", cwd=target)
        git("config", "user.email", "owner@example.com", cwd=target)
        for rel, content in (files or {"README.md": f"# {name}\n"}).items():
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git("add", ".", cwd=target)
        git("commit", "-m", "initial", cwd=target)
        if remote is not None:
            git("push", "-u", "origin", "main", cwd=target)
        return target

    return build
