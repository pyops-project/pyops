import os
import time
import os.path
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from . import files
from fastapi import HTTPException

import logging
logger = logging.getLogger(__name__)

import fcntl
from contextlib import contextmanager

def _retry_call(cmd: list, cwd, max_retries: int = 3) -> None:
    for attempt in range(max_retries):
        try:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.warning(f"Retry {attempt+1}/{max_retries} for {cmd} failed (rc={e.returncode}): stdout='{e.stdout.strip()}', stderr='{e.stderr.strip()}'")
            if attempt == max_retries - 1:
                logger.error(f"Final fail for {cmd} (rc={e.returncode}): stdout='{e.stdout.strip()}', stderr='{e.stderr.strip()}'")
                raise
            time.sleep(0.5)

def _call(cmd: list, cwd) -> None:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Call fail for {cmd} (rc={e.returncode}): stdout='{e.stdout.strip()}', stderr='{e.stderr.strip()}'")
        raise

def _check(cmd: list, cwd) -> None:
    try:
        return subprocess.check_output(cmd, cwd=cwd).decode().strip()
    except subprocess.CalledProcessError as e:
        logger.debug(f"Check Output fail for {cmd} (rc={e.returncode})")  # Downgraded to DEBUG
        raise

@contextmanager
def repo_lock(git_base: str, set_name: str):
    """Advisory lock holding fd across context for explicit release."""
    lockfile = os.path.join(git_base, f"{set_name}.lock")
    os.makedirs(git_base, exist_ok=True)
    fd = open(lockfile, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        logger.debug(f"Acquired lock for {set_name} in {git_base}")
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        if os.path.exists(lockfile):
            try:
                os.unlink(lockfile)
            except FileNotFoundError:
                pass  # Safe if race-deleted
        logger.debug(f"Released lock for {set_name} in {git_base}")

# Git base path from config.
def get_git_base() -> str:
    """Returns the configured Git base directory.
    
    Returns:
        str: Path to Git repos root.
    """
    return files.conf_data()["git"]


def get_git_pull_interval() -> int:
    """Returns the configured Git pull interval in seconds.
    
    Returns:
        int: Pull interval from config.
    """
    return files.conf_runner()["git_pull_interval"]

def pull_if_needed(repo: str) -> bool:
    """Syncs to origin/HEAD via soft reset if clean; skips if dirty to preserve unstaged changes.
    
    Args:
        repo (str): Repo path.
    
    Returns:
        bool: True if reset performed.
    
    Raises:
        subprocess.CalledProcessError: Reset failures (retried).
    """
    if not os.path.exists(os.path.join(repo, ".git")):
        return False
    
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        try:
            remotes = _check(["git", "remote"], repo).split()
            if not remotes:
                logger.info(f"No remote for {os.path.basename(repo)}; skipping reset")
                return False
            
            # Check if on tag only if tags exist
            tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
            if tags_output:
                try:
                    _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
                    logger.info(f"On tag in {os.path.basename(repo)}; skipping reset")
                    return False
                except subprocess.CalledProcessError:
                    pass  # Not on tag
            else:
                # No tags, assume not on tag
                pass
            
            status_out = _check(["git", "status", "--porcelain"], repo)
            
            branch = _check(["git", "branch", "--show-current"], repo)
            pre_head = _check(["git", "rev-parse", "HEAD"], repo)
            
            _retry_call(["git", "fetch"], repo)
            _call(["git", "reset", "--soft", f"origin/{branch}"], repo)
            
            post_head = _check(["git", "rev-parse", "HEAD"], repo)
            if pre_head != post_head:
                changes = _check(["git", "log", "--oneline", "-1"], repo)
                logger.info(f"Reset {os.path.basename(repo)} to origin/HEAD: {changes}")

                # Add new files from HEAD not in working directory
                try:
                    files_in_head = _check(["git", "ls-tree", "-r", "--name-only", "HEAD"], repo).splitlines()
                    added_count = 0
                    for f in files_in_head:
                        full_path = os.path.join(repo, f)
                        if not os.path.exists(full_path):
                            _call(["git", "checkout", "HEAD", "--", f], repo)
                            added_count += 1
                    if added_count > 0:
                        logger.info(f"Added {added_count} new files to {os.path.basename(repo)} working directory")
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to add new files for {os.path.basename(repo)}: {e}")

            else:
                logger.debug(f"No changes reset for {os.path.basename(repo)}")
            
            return True
        
        except subprocess.CalledProcessError as e:
            logger.error(f"Reset failed for {os.path.basename(repo)} (rc={e.returncode}): stdout='{e.stdout.strip() if hasattr(e, 'stdout') else 'N/A'}', stderr='{e.stderr.strip() if hasattr(e, 'stderr') else 'N/A'}'")
            raise

def get_repo_path(set: str) -> str:
    """Ensures repo exists, initializes if needed, sets up .gitignore and README.md.
    
    Args:
        set (str): Repo directory name.
    
    Returns:
        str: Absolute path to repo directory.
    
    Raises:
        HTTPException: If directory creation fails (500).
    """
    git_base = get_git_base()
    repo = os.path.abspath(os.path.join(git_base, set))
    os.makedirs(repo, exist_ok=True)
    
    with repo_lock(git_base, set):
        git_dir = os.path.join(repo, ".git")
        if not os.path.exists(git_dir):
            _call(["git", "init"], repo)
        
        # Ensure .gitignore includes *.bck.
        gitignore = os.path.join(repo, ".gitignore")
        lines = []
        if os.path.exists(gitignore):
            with open(gitignore, 'r') as f:
                lines = f.readlines()
        if "*.bck\n" not in lines:
            with open(gitignore, "a") as f:
                f.write("*.bck\n")
            _call(["git", "add", ".gitignore"], repo)
            _call(["git", "commit", "-m", f"Initial .gitignore for {set}"], repo)
        
        # Ensure README.md.
        readme = os.path.join(repo, "README.md")
        if not os.path.exists(readme):
            with open(readme, "w") as f:
                f.write(f"# {set}\n")
            _call(["git", "add", "README.md"], repo)
            _call(["git", "commit", "-m", f"Initial README for {set}"], repo)
    
    return repo

def get_file_path(repo: str, name: str, type_: str) -> str:
    """Returns the full path to the scriptlet file.
    
    Views: {name}.html (raw HTML).
    Others: {name}.{type_}.py (executable Python).
    Markdown: README.md only.
    
    Args:
        repo (str): Repo path.
        name (str): Scriptlet name.
        type_ (str): Scriptlet type.
    
    Returns:
        str: File path.
    
    Raises:
        ValueError: Invalid type.
    """
    if type_ == "markdown" and name == "README":
        return os.path.join(repo, "README.md")
    if type_ == "view":
        return os.path.join(repo, f"{name}.html")
    if type_ not in ["task", "library", "controller"]:
        raise ValueError(f"Invalid type: {type_}")
    return os.path.join(repo, f"{name}.{type_}.py")

def get_name_from_file(filename: str) -> str:
    """Extracts name from filename.
    
    Views/Markdown: Basename without extension (allows hyphens/multiples).
    Python: {name}.{type} → name; raise if no type dot.
    
    Args:
        filename (str): e.g., 'myscript.task.py', 'SimpleDog-Head.html', or 'README.md'.
    
    Returns:
        str: Name.
    
    Raises:
        ValueError: Invalid .py format.
    """
    if filename == "README.md":
        return "README"
    base = os.path.splitext(filename)[0]
    if filename.endswith('.html'):
        return base
    if '.' not in base:
        raise ValueError(f"Invalid filename: {filename}")
    return base.rsplit('.', 1)[0]

def infer_type_from_filename(filename: str) -> str:
    """Infers type from filename.
    
    Views: .html → "view".
    Python: {name}.{type}.py → type (task/library/controller).
    Markdown: README.md → "markdown".
    
    Args:
        filename (str): e.g., 'myscript.task.py' or 'myview.html'.
    
    Returns:
        str: Type.
    
    Raises:
        ValueError: Invalid format.
    """
    if filename == "README.md":
        return "markdown"
    if filename.endswith('.html'):
        return "view"
    if not filename.endswith('.py'):
        raise ValueError(f"Not a Python/HTML file: {filename}")
    base = os.path.splitext(filename)[0]
    parts = base.rsplit('.', 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid Python filename: {filename}")
    type_ = parts[1]
    if type_ not in ["task", "library", "controller"]:
        raise ValueError(f"Unknown type in filename: {type_}")
    return type_

def list_scriptlets() -> List[Dict[str, str]]:
    """Lists all scriptlets across repos.
    
    Scans Git repos, globs *.py, *.html, README.md; derives set/name/type.
    Views: .html as {"type": "view"}.
    
    Returns:
        List[Dict]: [{"set": str, "name": str, "type": str}, ...] sorted.
    """
    git_base = get_git_base()
    sets = [d for d in os.listdir(git_base)
              if os.path.isdir(os.path.join(git_base, d))
              and os.path.exists(os.path.join(git_base, d, ".git"))]
    
    scriptlets = []
    for set in sorted(sets):
        repo = get_repo_path(set)
        for filename in os.listdir(repo):
            if filename.endswith(('.py', '.html')) or filename == "README.md":
                try:
                    name = get_name_from_file(filename)
                    type_ = infer_type_from_filename(filename)
                    scriptlets.append({"set": set, "name": name, "type": type_})
                except ValueError:
                    continue  # Skip invalid files.
    
    return sorted(scriptlets, key=lambda x: (x["set"], x["name"]))

def check_name_unique(repo: str, name: str) -> bool:
    """Checks if name is unique (no existing {name}.*.py or {name}.html).
    
    Args:
        repo (str): Repo path.
        name (str): Name to check.
    
    Returns:
        bool: True if unique.
    """
    files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and (f.endswith(('.py', '.html')) or f == "README.md")]
    return len(files) == 0

def read_content(repo: str, name: str, type_: str, committed: bool = False, strict: bool = False) -> str:
    """Reads content preferring unstaged (working dir) or committed (git show HEAD:file).
    
    Views: Raw HTML read.
    Others: Python code read.
    
    Args:
        repo (str): Repo path.
        name (str): Name.
        type_ (str): Type.
        committed (bool): Force committed read (fallback to disk if missing).
        strict (bool): When committed=True, raise if no committed version (no fallback).
    
    Returns:
        str: Content.
    
    Raises:
        FileNotFoundError: File missing in both, or strict committed missing.
        subprocess.CalledProcessError: Git error.
    """
    file_path = get_file_path(repo, name, type_)
    rel_path = os.path.relpath(file_path, repo)
    
    if os.path.exists(file_path) and not committed:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    
    # For committed=True: Try git show, fallback to disk if exists unless strict.
    try:
        out = _check(["git", "show", f"HEAD:{rel_path}"], repo)
        return out
    except subprocess.CalledProcessError:
        if committed and strict:
            raise FileNotFoundError(f"No committed version for {rel_path}")
        if os.path.exists(file_path):
            logger.warning(f"Committed content missing for {rel_path}; falling back to unstaged: {file_path}")
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            raise FileNotFoundError(f"File not found: {file_path}") from None

def write_content(repo: str, name: str, type_: str, content: str) -> None:  # Renamed from write_code
    """Writes content to file.
    
    Views: Writes HTML.
    Others: Writes Python.
    
    Args:
        repo (str): Repo path.
        name (str): Name.
        type_ (str): Type.
        content (str): Content to write.
    
    Raises:
        IOError: Write failure.
    """
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        file_path = get_file_path(repo, name, type_)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

def perform_git_add_commit_push(repo: str, file_path: str, msg: str) -> None:
    """Adds file, commits, pushes (checks remote and not tag).
    
    Args:
        repo (str): Repo path.
        file_path (str): Relative file path.
        msg (str): Commit message.
    
    Raises:
        HTTPException: 409 if on tag.
        subprocess.CalledProcessError: Git failure.
    """    
    pull_if_needed(repo)
    rel_path = os.path.relpath(file_path, repo)
    
    # Check not on tag.
    tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
    if tags_output:
        try:
            _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
            raise HTTPException(409, "Cannot commit on a tag.")
        except subprocess.CalledProcessError:
            pass  # Not a tag.
    # No tags, assume not on tag
    
    # Add, commit, push.
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        _call(["git", "add", rel_path], repo)
        _call(["git", "commit", "-m", msg], repo)

        remotes = _check(["git", "remote"], repo)
        if remotes:
            _call(["git", "push"], repo)

def batch_commit(repo: str, msg: str, rel_files: List[str] = None) -> None:
    """Batch adds files/all, commits, pushes (checks remote and not tag).
    
    Args:
        repo (str): Repo path.
        msg (str): Commit message.
        rel_files (List[str], optional): Relative file paths to add; None=add all.
    
    Raises:
        HTTPException: 409 if on tag.
        subprocess.CalledProcessError: Git failure.
    """
    pull_if_needed(repo)
    
    # Check not on tag.
    tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
    if tags_output:
        try:
            _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
            raise HTTPException(409, "Cannot commit on a tag.")
        except subprocess.CalledProcessError:
            pass  # Not a tag.
    # No tags, assume not on tag
    
    # Add files or all.
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        if rel_files:
            for rel_path in rel_files:
                _call(["git", "add", rel_path], repo)
        else:
            _call(["git", "add", "."], repo)
        
        # Commit, push.
        _call(["git", "commit", "-m", msg], repo)
        remotes = _check(["git", "remote"], repo)
        if remotes:
            _call(["git", "push"], repo)

def delete_file_and_commit(repo: str, file_path: str, msg: str) -> None:
    """Removes file, commits, pushes.
    
    Args:
        repo (str): Repo path.
        file_path (str): Relative file path.
        msg (str): Commit message.
    
    Raises:
        HTTPException: As in perform_git_add_commit_push.
        subprocess.CalledProcessError: Git failure.
    """
    pull_if_needed(repo)
    rel_path = os.path.relpath(file_path, repo)
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        _call(["git", "rm", "-f", rel_path], repo)
        _call(["git", "commit", "-m", msg], repo)
        remotes = _check(["git", "remote"], repo)
        if remotes:
            _call(["git", "push"], repo)

def get_repo_status(set: str) -> Dict[str, any]:
    """Gets repo branch and tag status.
    
    Args:
        set (str): Set name.
    
    Returns:
        Dict: {"branch": str, "is_tag": bool, "flag": str|None}
    
    Raises:
        subprocess.CalledProcessError: Git failure.
    """
    repo = get_repo_path(set)
    git_base = get_git_base()
    with repo_lock(git_base, set):
        branch = _check(["git", "branch", "--show-current"], repo)
        tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
        if tags_output:
            try:
                _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
                is_tag = True
                flag = "TAG"
            except subprocess.CalledProcessError:
                is_tag = False
                flag = None
        else:
            is_tag = False
            flag = None
    return {"branch": branch, "is_tag": is_tag, "flag": flag}

def rollback_staged(set: str, name: str, type_: str) -> None:
    """Backs up unstaged changes to .bck, then checks out HEAD.
    
    Args:
        set (str): Set.
        name (str): Name.
        type_ (str): Type.
    
    Raises:
        subprocess.CalledProcessError: Git failure.
    """
    repo = get_repo_path(set)
    file_path = get_file_path(repo, name, type_)
    bck_path = file_path + ".bck"
    if os.path.exists(file_path):
        shutil.copy2(file_path, bck_path)  # Overwrite if exists.
    rel_path = os.path.relpath(file_path, repo)
    git_base = get_git_base()
    with repo_lock(git_base, set):
        _call(["git", "checkout", "HEAD", "--", rel_path], repo)

def rollback_committed(set: str, name: str, type_: str) -> None:
    """Directly checks out HEAD (discards unstaged).
    
    Args:
        set (str): Set.
        name (str): Name.
        type_ (str): Type.
    
    Raises:
        subprocess.CalledProcessError: Git failure.
    """
    repo = get_repo_path(set)
    file_path = get_file_path(repo, name, type_)
    rel_path = os.path.relpath(file_path, repo)
    git_base = get_git_base()
    with repo_lock(git_base, set):
        _call(["git", "checkout", "HEAD", "--", rel_path], repo)


def has_unstaged_changes(repo: str, name: str, type_: str) -> bool:
    """Checks if the scriptlet file has unstaged changes vs committed HEAD.
    
    Args:
        repo (str): Repo path.
        name (str): Name.
        type_ (str): Type.
    
    Returns:
        bool: True if unstaged changes exist.
    
    Raises:
        subprocess.CalledProcessError: Git failure.
    """
    file_path = get_file_path(repo, name, type_)
    rel_path = os.path.relpath(file_path, repo)
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        # Check if committed (in HEAD)
        try:
            _check(["git", "show", f"HEAD:{rel_path}"], repo)
            # Committed, check for unstaged changes
            try:
                result = subprocess.run(
                    ["git", "diff", "--quiet", "HEAD", "--", rel_path],
                    cwd=repo,
                    capture_output=True,
                    text=True
                )
                logger.debug(f"Diff returncode for {rel_path}: {result.returncode}, stderr: '{result.stderr.strip()}'")
                if result.returncode == 0:
                    return False
                elif result.returncode == 1 and not result.stderr.strip():
                    return True
                else:
                    logger.warning(f"Unexpected git diff result for {rel_path}: rc={result.returncode}, stderr='{result.stderr.strip()}'; treating as no changes")
                    return False
            except subprocess.CalledProcessError as e:
                logger.error(f"Git diff failed for {rel_path}: {e}")
                raise
        except subprocess.CalledProcessError:
            # Not committed (untracked or deleted): treat as unstaged changes if file exists
            if os.path.exists(file_path):
                logger.debug(f"File {rel_path} is not committed, considered as having unstaged changes")
                return True
            else:
                # File doesn't exist on disk, no changes
                return False
