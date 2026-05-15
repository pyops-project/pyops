# Task: clone a user set from a remote URL.
#
# Triggered by the data service when the operator clicks "Clone" in the
# sets view. The service has already validated input and enqueued this
# task with drop_done=True; param injected the variables we consume here.
#
# Params (param-injected globals):
#   url:       repo URL (required)
#   username:  optional, embedded in HTTPS credentials
#   password:  optional, embedded in HTTPS credentials
#   git_name:  optional, set as local user.name
#   git_email: optional, set as local user.email
#
# Output to stdout/stderr lands in the job workspace logs; the dialog
# tails it via /scriptlet/job/item polling.

#!{::set}
import os
from urllib.parse import urlparse


def main():
    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts:
        raise ValueError(f"Cannot determine repo name from URL: {url}")
    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise ValueError(f"Empty repo name inferred from URL: {url}")
    if name == "internal":
        raise ValueError("'internal' is reserved for pyops-managed scriptlets")

    git_base = _git_base()
    target = os.path.join(git_base, name)
    if os.path.exists(target):
        raise ValueError(f"Set '{name}' already exists.")

    clone_url = url
    if username and password and parsed.scheme == "https":
        clone_url = parsed._replace(
            netloc=f"{username}:{password}@{parsed.netloc}"
        ).geturl()

    status(f"clone {url} as set '{name}'")
    with repo_lock(name):
        os.makedirs(git_base, exist_ok=True)
        run_cmd(["git", "clone", clone_url, name], cwd=git_base)

        if username and password:
            cred_path = os.path.join(target, ".git", "credentials")
            with open(cred_path, "w") as f:
                f.write(f"https://{username}:{password}@{parsed.netloc}\n")
            run_cmd(
                ["git", "config", "credential.helper", "store --file .git/credentials"],
                cwd=target,
            )
        if git_name:
            run_cmd(["git", "config", "user.name", git_name], cwd=target)
        if git_email:
            run_cmd(["git", "config", "user.email", git_email], cwd=target)

    print(f"\nCloned set '{name}'", flush=True)


# Defaults so the task degrades gracefully if param omits an optional var.
username = globals().get("username", "") or ""
password = globals().get("password", "") or ""
git_name = globals().get("git_name", "") or ""
git_email = globals().get("git_email", "") or ""

if __name__ == "__main__":
    main()
