"""Every set_* task in the pyops set that takes a set name refuses one that
is not a single directory name under the git base, before it touches the
filesystem.

The tasks can be started directly via `control.job.create`, past the sets
data service's own check, so each validates the name itself — with the
`validate_set_name` global the job runner injects for pyops-set jobs.
`pyops_job` builds the job globals through the real
`job_runner.setup_job_globals` (config pointing into tmp_path) and execs
set.library.py + the task into them, as the runner does for `#!{::set}`.
set_clone derives its set name from the URL; it is covered in
test_set_clone.py."""

import os

import pytest


TASKS = [
    "set_create",
    "set_pull",
    "set_switch",
    "set_remove",
    "set_update_deps",
    "set_commit_drafts",
    "set_discard_drafts",
]

# The git base itself, its parent, and paths reaching out of or into
# other directories. `../outside` names an existing directory next to the
# git base (and, through the venv/pkg bases, next to those).
INVALID_NAMES = [".", "..", "../outside", "nested/set"]


@pytest.fixture
def outside(pyops_install):
    """A directory next to the git base holding a file a traversing set
    name would reach."""
    directory = pyops_install["root"] / "data" / "outside"
    directory.mkdir()
    (directory / "keep.txt").write_text("not a set\n")
    return directory


def _tree(root):
    """Every path under `root`, relative — a filesystem snapshot."""
    return sorted(
        os.path.relpath(os.path.join(d, n), root)
        for d, dirs, files in os.walk(root)
        for n in dirs + files
    )


@pytest.mark.parametrize("name", INVALID_NAMES)
@pytest.mark.parametrize("task", TASKS)
def test_invalid_set_name_refused_before_filesystem(pyops_job, pyops_install, outside, task, name):
    """No lock file, directory, git or pip command — and nothing removed
    outside the git base — before the task fails with the check's error."""
    from app.lib.git import InvalidSetNameError
    glob = pyops_job(task, name=name, branch="main")  # branch: set_switch's second param
    commands = []
    glob["run_cmd"] = lambda cmd, cwd=None, check=True: commands.append(list(cmd))
    before = _tree(pyops_install["root"])

    with pytest.raises(InvalidSetNameError):
        glob["main"]()

    assert _tree(pyops_install["root"]) == before
    assert commands == []
