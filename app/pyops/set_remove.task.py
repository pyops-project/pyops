# Task: remove a user set (its git repo + venv + pkg directory).
#
# Mirrors app.lib.sets.remove_set + remove_set_environment:
#  - holds the install lock so an in-flight pip install in the same set
#    completes before rmtree
#  - removes the per-set venv and pkg dirs under that lock
#  - then removes the git repo under the per-set repo_lock
#
# Params:
#   name: set to remove (required)

#!{::set}
import os
import shutil


def main():
    if not name:
        raise ValueError("name is required")
    validate_set_name(name)
    if name == "pyops":
        raise ValueError("'pyops' is pyops-managed and cannot be removed")

    target = _set_dir(name)
    if not os.path.exists(target):
        raise ValueError(f"Set '{name}' not found.")

    status(f"remove set '{name}'")

    # Install-lock first so a concurrent ensure_set_environment / job-time
    # pip install completes before we rmtree its venv. Mirrors
    # app.lib.sets.remove_set_environment's flock-then-rmtree order.
    venv = _venv_dir(name)
    pkg = _pkg_dir(name)
    with install_lock(name):
        if os.path.exists(venv):
            shutil.rmtree(venv)
            print(f"Removed venv {venv}", flush=True)
        if os.path.exists(pkg):
            shutil.rmtree(pkg)
            print(f"Removed pkg dir {pkg}", flush=True)

    with repo_lock(name):
        shutil.rmtree(target)
        print(f"Removed git repo {target}", flush=True)

    print(f"\nRemoved set '{name}'.", flush=True)


if __name__ == "__main__":
    main()
