# Task: switch a user set's working branch.
#
# Mirrors app.lib.sets.switch_branch:
#  - checkout existing local branch when present
#  - else fetch + checkout if branch exists on origin
#  - else create branch from current HEAD; push if a remote exists
#
# Params:
#   name:   set name (required)
#   branch: target branch (required)

#!{::set}
import os
import subprocess


def _check(cmd, cwd):
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def main():
    if not name:
        raise ValueError("name is required")
    validate_set_name(name)
    if not branch:
        raise ValueError("branch is required")
    if name == "pyops":
        raise ValueError("'pyops' is pyops-managed; branch switching is not supported")

    target = _set_dir(name)
    if not os.path.exists(target):
        raise ValueError(f"Set '{name}' not found.")

    status(f"switch '{name}' to '{branch}'")
    with repo_lock(name):
        # Local branch exists → straight checkout.
        try:
            _check(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], target)
            run_cmd(["git", "checkout", branch], cwd=target)
            print(f"\nSwitched to existing local branch '{branch}'.", flush=True)
            return
        except subprocess.CalledProcessError:
            pass

        # Remote branch exists → fetch + track.
        try:
            remotes = _check(["git", "remote"], target).split()
            if "origin" in remotes:
                remote_refs = _check(["git", "ls-remote", "--heads", "origin", branch], target)
                if remote_refs.strip():
                    run_cmd(["git", "fetch", "origin"], cwd=target)
                    run_cmd(["git", "checkout", "-b", branch, f"origin/{branch}"], cwd=target)
                    print(f"\nChecked out '{branch}' from origin.", flush=True)
                    return
        except subprocess.CalledProcessError:
            pass

        # New branch from current HEAD; push if remote exists.
        run_cmd(["git", "checkout", "-b", branch], cwd=target)
        try:
            _check(["git", "remote", "get-url", "origin"], target)
            try:
                run_cmd(["git", "push", "-u", "origin", branch], cwd=target)
                print(f"\nCreated and pushed new branch '{branch}'.", flush=True)
                return
            except subprocess.CalledProcessError as e:
                print(f"Created '{branch}' locally; push failed: {e}", flush=True)
                return
        except subprocess.CalledProcessError:
            pass
        print(f"\nCreated new branch '{branch}' (no remote to push to).", flush=True)


if __name__ == "__main__":
    main()
