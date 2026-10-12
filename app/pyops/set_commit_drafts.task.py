# Task: stage + commit + push every unstaged scriptlet in a user set.
#
# The Sets view's per-row "commit drafts" button (the `check` icon next
# to pull / update-deps / switch / remove) calls this. Operator intent:
# I have edited several scriptlets in this set as drafts (via the
# Manager's "Write Local to Remote Draft" or pyopsctl's
# `scriptlet draft`); commit them all in one go instead of "Save Remote
# Draft" per file. Mirrors `app.lib.git.batch_commit` but enqueued so
# the live-output modal can tail the git output.
#
# Params:
#   name:    set whose drafts to commit (required)
#   user:    operator name, embedded in the default commit message
#            (optional; defaults to "pyops")
#   message: explicit commit message (optional). When empty/whitespace
#            we fall back to "Commit drafts in <set> by <user>".
#
# Behaviour:
#   - refuse on the pyops set (write-protected)
#   - refuse when HEAD is on a tag (mirrors batch_commit's invariant)
#   - no-op cleanly when nothing is unstaged (print + return)
#   - one `git add .` + `git commit` + `git push` (when a remote exists)
#     under the same `repo_lock` the other set_* tasks take, so this
#     serialises against pull / update-deps / switch on the same set

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

    status(f"commit drafts in set '{name}'")
    with repo_lock(name):
        # Refuse to commit on a tag — matches app.lib.git.batch_commit.
        try:
            tags = _check(["git", "tag", "-l"], target)
            if tags:
                try:
                    _check(["git", "describe", "--tags", "--exact-match", "HEAD"], target)
                    raise RuntimeError("HEAD is on a tag — refusing to commit.")
                except subprocess.CalledProcessError:
                    pass  # not on a tag

        except subprocess.CalledProcessError:
            pass

        dirty = _check(["git", "status", "--porcelain", "--untracked-files=all"], target)
        if not dirty:
            print("No unstaged scriptlets — nothing to commit.", flush=True)
            return

        for line in dirty.splitlines():
            print(line, flush=True)

        commit_msg = message.strip() if message.strip() else f"Commit drafts in {name} by {user}"
        run_cmd(["git", "add", "."], cwd=target)
        run_cmd(["git", "commit", "-m", commit_msg], cwd=target)

        try:
            remotes = _check(["git", "remote"], target).split()
        except subprocess.CalledProcessError:
            remotes = []
        count = len(dirty.splitlines())
        if remotes:
            # Migration: pre-3.3.0 clones may carry embedded HTTPS
            # credentials in origin's URL. Strip them so push relies
            # on .git/credentials via the credential helper, making
            # token rotation effective. No-op for clean / non-HTTPS
            # origins.
            normalize_origin_url(target)
            run_cmd(["git", "push"], cwd=target)
            print(f"\nCommitted and pushed {count} file(s) to {remotes[0]}.", flush=True)
        else:
            print(f"\nCommitted {count} file(s); no remote configured — skipped push.", flush=True)


# Defaults so the task degrades gracefully if the caller didn't supply
# every optional kwarg (every controller-proxy path does, but keeping
# this resilient matches the other set_* tasks).
user = globals().get("user", "") or "pyops"
message = globals().get("message", "") or ""


if __name__ == "__main__":
    main()
