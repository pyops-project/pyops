# Task: create a new empty user set (git init under data/git/{name}/).
#
# Params (param-injected globals):
#   name:      set name (required)
#   git_name:  optional, set as local user.name
#   git_email: optional, set as local user.email
#
# git_name / git_email mirror the clone dialog's two identity fields.
# They matter more here than for clone, because create makes the initial
# commit itself: with no committer identity configured, `git commit`
# aborts with "Please tell me who you are". In the container pyops runs
# unprivileged with no global git config, so there is nothing to fall
# back to — hence a local identity is always set (from the supplied
# fields, or a default) before the initial commit. The identity is
# persisted in the repo so a later commit-drafts on this locally-created
# set works too.

#!{::set}
import os

# Fallback identity used when the operator leaves the dialog's Git Name /
# Git Email blank — enough to make `git commit` succeed on a set that is
# never pushed. Matches the "pyops" default the other set_* tasks use.
DEFAULT_GIT_NAME = "pyops"
DEFAULT_GIT_EMAIL = "pyops@localhost"


def main():
    if not name:
        raise ValueError("name is required")
    if name == "pyops":
        raise ValueError("'pyops' is reserved for pyops-managed scriptlets")

    git_base = _git_base()
    target = os.path.join(git_base, name)
    if os.path.exists(target):
        raise ValueError(f"Set '{name}' already exists.")

    status(f"create set '{name}'")
    with repo_lock(name):
        os.makedirs(target, exist_ok=False)
        run_cmd(["git", "init"], cwd=target)

        # Persist a committer identity before the initial commit so it
        # never fails for want of a global git config (the container case).
        run_cmd(["git", "config", "user.name", git_name or DEFAULT_GIT_NAME], cwd=target)
        run_cmd(["git", "config", "user.email", git_email or DEFAULT_GIT_EMAIL], cwd=target)

        # Minimal .gitignore + README so the repo is committable.
        with open(os.path.join(target, ".gitignore"), "w") as f:
            f.write("*.bck\n")
        with open(os.path.join(target, "README.md"), "w") as f:
            f.write(f"# {name}\n")
        run_cmd(["git", "add", ".gitignore", "README.md"], cwd=target)
        run_cmd(["git", "commit", "-m", f"Initial commit for {name}"], cwd=target)

    print(f"\nCreated set '{name}'", flush=True)


# Defaults so the task degrades gracefully if param omits an optional var.
git_name = globals().get("git_name", "") or ""
git_email = globals().get("git_email", "") or ""

if __name__ == "__main__":
    main()
