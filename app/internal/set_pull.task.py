# Task: pull a user set from its remote.
#
# Mirrors the safety semantics of app.lib.git.pull:
#  - no-op if no remote, on a tag, or working tree dirty (preserves unstaged)
#  - soft-reset to origin/{branch} when clean
#  - fetches new files committed to HEAD that are missing in working dir
#
# Params:
#   name: set to pull (required)

#!{::set}
import os
import subprocess


def _check(cmd, cwd):
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def main():
    if not name:
        raise ValueError("name is required")
    if name == "internal":
        raise ValueError("'internal' is not pullable")

    target = _set_dir(name)
    if not os.path.exists(target):
        raise ValueError(f"Set '{name}' not found.")

    status(f"pull set '{name}'")
    with repo_lock(name):
        try:
            remotes = _check(["git", "remote"], target).split()
        except subprocess.CalledProcessError:
            remotes = []
        if not remotes:
            print("No remote configured — nothing to pull.", flush=True)
            return

        # Refuse to reset if currently on a tag — preserves the pinned state.
        try:
            tags = _check(["git", "tag", "-l"], target)
            if tags:
                try:
                    _check(["git", "describe", "--tags", "--exact-match", "HEAD"], target)
                    print("HEAD is on a tag — skipping reset.", flush=True)
                    return
                except subprocess.CalledProcessError:
                    pass
        except subprocess.CalledProcessError:
            pass

        # Skip if working tree dirty — pyops convention: dirty trees keep
        # their unstaged edits, the upgrade flow handles them separately.
        dirty = _check(["git", "status", "--porcelain"], target)
        if dirty:
            print("Working tree has unstaged changes — skipping reset.", flush=True)
            return

        branch = _check(["git", "branch", "--show-current"], target)
        pre_head = _check(["git", "rev-parse", "HEAD"], target)

        try:
            remote_ref = _check(["git", "ls-remote", "--heads", remotes[0], branch], target).split()[0]
            if remote_ref == pre_head:
                print(f"Already up-to-date with {remotes[0]}/{branch}.", flush=True)
                return
        except (subprocess.CalledProcessError, IndexError):
            pass

        run_cmd(["git", "fetch"], cwd=target)
        run_cmd(["git", "reset", "--soft", f"origin/{branch}"], cwd=target)

        post_head = _check(["git", "rev-parse", "HEAD"], target)
        if pre_head != post_head:
            try:
                files_in_head = _check(["git", "ls-tree", "-r", "--name-only", "HEAD"], target).splitlines()
                new_files = [
                    f for f in files_in_head
                    if not os.path.exists(os.path.join(target, f))
                    and (f.endswith((".py", ".html", ".js")) or f == "README.md")
                ]
                if new_files:
                    run_cmd(["git", "checkout", "HEAD", "--"] + new_files, cwd=target)
                    print(f"Restored {len(new_files)} file(s) from HEAD.", flush=True)
            except subprocess.CalledProcessError as e:
                print(f"Warning: failed to add new files after reset: {e}", flush=True)
            print(f"\nReset '{name}' to {remotes[0]}/{branch}.", flush=True)
        else:
            print("No changes after reset.", flush=True)


if __name__ == "__main__":
    main()
