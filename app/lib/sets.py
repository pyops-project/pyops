import os
import subprocess
from pathlib import Path
import shutil
from urllib.parse import urlparse

from .git import repo_lock, _call, _check

def list_sets(git_dir):
    """
    List all sets (Git repos) in data/git/.
    Returns list of dicts: [{"name": str, "branch": str}, ...]
    """
    sets_dir = Path(git_dir)
    if not sets_dir.exists():
        return []
    
    sets = []
    for item in sets_dir.iterdir():
        if item.is_dir():
            git_subdir = item / ".git"
            if git_subdir.exists():
                try:
                    with repo_lock(git_dir, item.name):
                        branch = _check(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(item))
                    sets.append({"name": item.name, "branch": branch})
                except subprocess.CalledProcessError:
                    branch = "unknown"
                    sets.append({"name": item.name, "branch": branch})
    return sets

def clone_set(git_dir, name, url, username=None, password=None, git_name=None, git_email=None):
    """
    Clone a new set repo into data/git/{name}, set local git config and credentials if provided.
    Raises ValueError if name exists or clone fails.
    """
    sets_dir = Path(git_dir) / name
    if sets_dir.exists():
        raise ValueError(f"Set '{name}' already exists.")

    # Embed credentials in URL if provided
    clone_url = url
    if username and password:
        parsed = urlparse(url)
        if parsed.scheme == "https":
            clone_url = parsed._replace(netloc=f"{username}:{password}@{parsed.netloc}").geturl()
    
    # Clone and configure under lock
    with repo_lock(git_dir, name):
        _call(["git", "clone", clone_url, name], git_dir)
        sets_dir = Path(git_dir) / name  # Recompute after clone
        
        # Store credentials if provided
        if username and password:
            parsed = urlparse(url)
            cred_line = f"https://{username}:{password}@{parsed.netloc}"
            credentials_path = sets_dir / ".git" / "credentials"
            with open(credentials_path, "w") as f:
                f.write(cred_line + "\n")
            _call(["git", "config", "credential.helper", "store --file .git/credentials"], str(sets_dir))
        
        # Set local git config
        if git_name:
            _call(["git", "config", "user.name", git_name], str(sets_dir))
        if git_email:
            _call(["git", "config", "user.email", git_email], str(sets_dir))
    
    return True

def remove_set(git_dir, name):
    """
    Remove the set directory data/git/{name}.
    Raises ValueError if not found.
    """
    sets_dir = Path(git_dir) / name
    if not sets_dir.exists():
        raise ValueError(f"Set '{name}' not found.")
    
    with repo_lock(git_dir, name):
        shutil.rmtree(sets_dir)
    return True

def switch_branch(git_dir, name, branch):
    """
    Switch the checked-out branch of the set.
    Raises ValueError if set not found or checkout fails.
    """
    sets_dir = Path(git_dir) / name
    if not sets_dir.exists():
        raise ValueError(f"Set '{name}' not found.")
    
    with repo_lock(git_dir, name):
        _call(["git", "checkout", branch], str(sets_dir))
    return True
