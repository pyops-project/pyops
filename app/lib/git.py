import os
import time
from typing import Dict

import os.path
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from . import files

import logging
logger = logging.getLogger(__name__)

import fcntl
from contextlib import contextmanager

class GitNotFoundError(FileNotFoundError):
    """Raised when a Git set or repository is not found."""

class GitConflictError(RuntimeError):
    """Raised for Git operation conflicts, e.g., committing on a tag."""

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
    """Advisory lock holding fd across context for explicit release.

    The lockfile is persistent: unlinking after release would allow a
    late-arriving caller to open a *new* inode while another caller still
    holds flock on the unlinked one, defeating mutual exclusion.
    """
    lockfile = os.path.join(git_base, f"{set_name}.lock")
    os.makedirs(git_base, exist_ok=True)
    fd = open(lockfile, "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        logger.debug(f"Acquired lock for {set_name} in {git_base}")
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
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

def pull(repo: str) -> bool:
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

            if remotes:
                try:
                    remote_ref = _check(["git", "ls-remote", "--heads", remotes[0], branch], repo).split()[0]
                    if remote_ref == pre_head:
                        logger.debug(f"Up-to-date: local HEAD matches origin/{branch} for {os.path.basename(repo)}")
                        return True
                except subprocess.CalledProcessError:
                    logger.debug(f"ls-remote failed for {os.path.basename(repo)}; proceeding to fetch")

            _retry_call(["git", "fetch"], repo)
            _call(["git", "reset", "--soft", f"origin/{branch}"], repo)
            
            post_head = _check(["git", "rev-parse", "HEAD"], repo)
            if pre_head != post_head:
                changes = _check(["git", "log", "--oneline", "-1"], repo)
                logger.info(f"Reset {os.path.basename(repo)} to origin/HEAD: {changes}")

                # Add new files from HEAD not in working directory
                try:
                    files_in_head = _check(["git", "ls-tree", "-r", "--name-only", "HEAD"], repo).splitlines()
                    new_files = [f for f in files_in_head
                                 if not os.path.exists(os.path.join(repo, f))
                                 and (f.endswith(('.py', '.html', '.js')) or f == 'README.md')]
                    added_count = len(new_files)
                    if new_files:
                        _call(["git", "checkout", "HEAD", "--"] + new_files, repo)
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
    """Ensures repo exists and is initialized, sets up .gitignore and README.md.
    
    Args:
        set (str): Repo directory name.
    
    Returns:
        str: Absolute path to repo directory.
    
    Raises:
        HTTPException: 404 if set directory does not exist.
    """
    git_base = get_git_base()
    repo = os.path.abspath(os.path.join(git_base, set))
    if not os.path.exists(repo):
        raise GitNotFoundError(f"Set '{set}' does not exist")
    
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
    if type_ == "jslibrary":
        return os.path.join(repo, f"{name}.js")
    if type_ not in ["task", "library", "controller"]:
        raise ValueError(f"Invalid type: {type_}")
    return os.path.join(repo, f"{name}.{type_}.py")

def get_name_from_file(filename: str) -> str:
    """Extracts name from filename.

    Views/Markdown/JS: Basename without extension (allows hyphens/multiples).
    Python: {name}.{type} → name; raise if no type dot.

    Args:
        filename (str): e.g., 'myscript.task.py', 'SimpleDog-Head.html', 'mylib.js', or 'README.md'.

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
    if filename.endswith('.js'):
        return base
    if '.' not in base:
        raise ValueError(f"Invalid filename: {filename}")
    return base.rsplit('.', 1)[0]

def infer_type_from_filename(filename: str) -> str:
    """Infers type from filename.
    
    Views: .html → "view".
    Python: {name}.{type}.py → type (task/library/controller).
    JS Libraries: .js → "jslibrary".
    Markdown: README.md → "markdown".
    
    Args:
        filename (str): e.g., 'myscript.task.py', 'myview.html', or 'mylib.js'.
    
    Returns:
        str: Type.
    
    Raises:
        ValueError: Invalid format.
    """
    if filename == "README.md":
        return "markdown"
    if filename.endswith('.html'):
        return "view"
    if filename.endswith('.js'):
        return "jslibrary"
    if not filename.endswith('.py'):
        raise ValueError(f"Not a Python/HTML/JS file: {filename}")
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
            if filename.endswith(('.py', '.html', '.js')) or filename == "README.md":
                try:
                    name = get_name_from_file(filename)
                    type_ = infer_type_from_filename(filename)
                    scriptlets.append({"set": set, "name": name, "type": type_})
                except ValueError:
                    continue  # Skip invalid files.
    
    return sorted(scriptlets, key=lambda x: (x["set"], x["name"]))

def list_scriptlet_files(repo: str, name: str) -> List[str]:
    """Lists scriptlet files for a given name in repo.
    
    Args:
        repo (str): Repo path.
        name (str): Scriptlet name.
    
    Returns:
        List[str]: Matching filenames.
    """
    files = []
    for filename in os.listdir(repo):
        try:
            if get_name_from_file(filename) == name:
                files.append(filename)
        except ValueError:
            continue
    return files



def check_name_unique(repo: str, name: str) -> bool:
    """Checks if name is unique (no existing {name}.*.py or {name}.html).
    
    Args:
        repo (str): Repo path.
        name (str): Name to check.
    
    Returns:
        bool: True if unique.
    """
    files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and (f.endswith(('.py', '.html', '.js')) or f == "README.md")]
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

def _add_commit_push_locked(repo: str, rel_path: str, msg: str) -> None:
    """Tag check + git add/commit/push. Caller MUST hold repo_lock."""
    tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
    if tags_output:
        try:
            _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
            raise GitConflictError("Cannot commit on a tag.")
        except subprocess.CalledProcessError:
            pass  # Not a tag.

    _call(["git", "add", rel_path], repo)
    _call(["git", "commit", "-m", msg], repo)
    remotes = _check(["git", "remote"], repo)
    if remotes:
        _call(["git", "push"], repo)

def perform_git_add_commit_push(repo: str, file_path: str, msg: str) -> None:
    """Adds file, commits, pushes (checks remote and not tag).

    Args:
        repo (str): Repo path.
        file_path (str): Relative file path.
        msg (str): Commit message.

    Raises:
        GitConflictError: If on tag.
        subprocess.CalledProcessError: Git failure.
    """
    rel_path = os.path.relpath(file_path, repo)
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        _add_commit_push_locked(repo, rel_path, msg)

def create_scriptlet(set_name: str, name: str, type_: str, content: str, commit_msg: str) -> str:
    """Atomic scriptlet creation: unique-name check + write + commit under one lock.

    Holding the lock across the whole sequence prevents a same-name race where
    two concurrent callers both pass check_name_unique before either writes.

    Args:
        set_name (str): Set.
        name (str): Scriptlet name.
        type_ (str): Scriptlet type.
        content (str): Initial file content.
        commit_msg (str): Git commit message.

    Returns:
        str: Absolute path of the created file.

    Raises:
        GitNotFoundError: Set does not exist.
        FileExistsError: Name already taken in the set.
        GitConflictError: Repo is on a tag.
        subprocess.CalledProcessError: Git failure.
    """
    repo = get_repo_path(set_name)  # ensures .git/.gitignore/README; takes+releases lock
    git_base = get_git_base()
    with repo_lock(git_base, set_name):
        if not check_name_unique(repo, name):
            raise FileExistsError(f"Name '{name}' already exists in set '{set_name}'")
        file_path = get_file_path(repo, name, type_)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        rel_path = os.path.relpath(file_path, repo)
        _add_commit_push_locked(repo, rel_path, commit_msg)
    return file_path

def batch_commit(repo: str, msg: str, rel_files: List[str] = None) -> None:
    """Batch adds files/all, commits, pushes (checks remote and not tag).

    Args:
        repo (str): Repo path.
        msg (str): Commit message.
        rel_files (List[str], optional): Relative file paths to add; None=add all.

    Raises:
        GitConflictError: If on tag.
        subprocess.CalledProcessError: Git failure.
    """
    # Check not on tag.
    tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
    if tags_output:
        try:
            _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
            raise GitConflictError("Cannot commit on a tag.")
        except subprocess.CalledProcessError:
            pass  # Not a tag.
    # No tags, assume not on tag
    
    # Add files or all.
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        if rel_files:
            _call(["git", "add"] + rel_files, repo)
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
    file_path = get_file_path(repo, name, type_)
    rel_path = os.path.relpath(file_path, repo)
    for attempt in range(3):
        try:
            status_out = subprocess.check_output(
                ['git', 'status', '--porcelain', '--untracked-files=all', '--', rel_path],
                cwd=repo
            ).decode('utf-8', errors='ignore').strip()
            return bool(status_out)
        except subprocess.CalledProcessError:
            if attempt < 2:
                time.sleep(2)
                continue
            return False
