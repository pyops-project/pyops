# Task: create a new empty user set (git init under data/git/{name}/).
#
# Params:
#   name: set name (required)

#!{::set}
import os


def main():
    if not name:
        raise ValueError("name is required")
    if name == "internal":
        raise ValueError("'internal' is reserved for pyops-managed scriptlets")

    git_base = _git_base()
    target = os.path.join(git_base, name)
    if os.path.exists(target):
        raise ValueError(f"Set '{name}' already exists.")

    status(f"create set '{name}'")
    with repo_lock(name):
        os.makedirs(target, exist_ok=False)
        run_cmd(["git", "init"], cwd=target)

        # Minimal .gitignore + README so the repo is committable.
        with open(os.path.join(target, ".gitignore"), "w") as f:
            f.write("*.bck\n")
        with open(os.path.join(target, "README.md"), "w") as f:
            f.write(f"# {name}\n")
        run_cmd(["git", "add", ".gitignore", "README.md"], cwd=target)
        run_cmd(["git", "commit", "-m", f"Initial commit for {name}"], cwd=target)

    print(f"\nCreated set '{name}'", flush=True)


if __name__ == "__main__":
    main()
