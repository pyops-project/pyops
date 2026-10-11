# Task: discard every uncommitted draft in a user set.
#
# The Sets view's per-row "discard drafts" button (the `restore` icon next
# to commit-drafts) calls this. Operator intent: I edited/added scriptlets
# in this set as drafts and want to throw them all away, restoring the set
# to its last commit — the bulk counterpart to the editor's per-file
# "revert" (DELETE /scriptlet/inventory/draft).
#
# Params:
#   name: set whose drafts to discard (required)
#
# Behaviour:
#   - refuse on the pyops set (write-protected)
#   - no-op cleanly when the working tree is clean (print + return)
#   - under the same `repo_lock` the other set_* tasks take (serialises
#     against commit / pull / update-deps / switch on the same set):
#       `git reset --hard HEAD`  reverts tracked staged+unstaged changes
#       `git clean -fd`          removes untracked new drafts (files + dirs)
#     Ignored files are kept (no -x). HEAD/branch are untouched.
#
# Destructive: removes unpushed/uncommitted work irreversibly. The UI
# guards it behind a confirm() dialog; this prints what it discarded.

#!{::set}
import os
import subprocess


def _check(cmd, cwd):
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def main():
    if not name:
        raise ValueError("name is required")
    validate_set_name(name)
    if name == "pyops":
        raise ValueError("'pyops' is write-protected")

    target = _set_dir(name)
    if not os.path.exists(target):
        raise ValueError(f"Set '{name}' not found.")

    status(f"discard drafts in set '{name}'")
    with repo_lock(name):
        dirty = _check(["git", "status", "--porcelain", "--untracked-files=all"], target)
        if not dirty:
            print("Working tree clean — nothing to discard.", flush=True)
            return

        for line in dirty.splitlines():
            print(line, flush=True)

        run_cmd(["git", "reset", "--hard", "HEAD"], cwd=target)
        run_cmd(["git", "clean", "-fd"], cwd=target)

        count = len(dirty.splitlines())
        print(f"\nDiscarded {count} draft change(s); set restored to the last commit.", flush=True)


if __name__ == "__main__":
    main()
