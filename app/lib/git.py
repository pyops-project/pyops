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

# Files restored into the working tree after a `pull()` soft-reset:
# any path matching one of these predicates AND newly present in HEAD
# but missing in the working directory gets `git checkout HEAD -- <path>`.
# New content types (e.g. yaml/json under data/) are added here as they
# are introduced.
_PULL_RESTORE_PREDICATES = (
    lambda f: f.endswith((".py", ".html", ".js")),
    lambda f: f == "README.md",
    lambda f: f.startswith("docs/") and f.endswith(".md"),
    lambda f: f.startswith("data/") and f.endswith((".yaml", ".yml", ".json")),
)


def _is_on_tag(repo: str) -> bool:
    """True iff HEAD is exactly at a tag (so a pull would clobber the tag pin)."""
    tags_output = subprocess.check_output(["git", "tag", "-l"], cwd=repo).decode().strip()
    if not tags_output:
        return False
    try:
        _check(["git", "describe", "--tags", "--exact-match", "HEAD"], repo)
        return True
    except subprocess.CalledProcessError:
        return False


def _remote_head_matches_local(repo: str, remote: str, branch: str, local_head: str) -> bool:
    """True iff `remote`'s `branch` points at `local_head` — short-circuits
    the fetch/reset when there's nothing to pull. Treats an ls-remote
    failure as 'unknown, proceed to fetch'."""
    try:
        remote_ref = _check(["git", "ls-remote", "--heads", remote, branch], repo).split()[0]
        return remote_ref == local_head
    except subprocess.CalledProcessError:
        logger.debug(f"ls-remote failed for {os.path.basename(repo)}; proceeding to fetch")
        return False


def _restore_new_files_from_head(repo: str) -> None:
    """Checkout files newly present in HEAD but missing in the working
    tree, restricted by `_PULL_RESTORE_PREDICATES`. Soft-reset alone
    doesn't materialise newly-added files; this loop completes that.
    Errors are logged but never raised — restoration is best-effort."""
    try:
        files_in_head = _check(["git", "ls-tree", "-r", "--name-only", "HEAD"], repo).splitlines()
        new_files = [
            f for f in files_in_head
            if not os.path.exists(os.path.join(repo, f))
            and any(pred(f) for pred in _PULL_RESTORE_PREDICATES)
        ]
        if new_files:
            _call(["git", "checkout", "HEAD", "--"] + new_files, repo)
            logger.info(f"Added {len(new_files)} new files to {os.path.basename(repo)} working directory")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to add new files for {os.path.basename(repo)}: {e}")


def pull(repo: str) -> bool:
    """Syncs to origin/HEAD via soft reset if clean; skips if dirty to
    preserve unstaged changes.

    Args:
        repo (str): Repo path.

    Returns:
        bool: True if the pull was attempted (whether or not it caused a
            head change); False if skipped (no .git, no remote, on tag).

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
                logger.info(f"No remote for {set_name}; skipping reset")
                return False

            if _is_on_tag(repo):
                logger.info(f"On tag in {set_name}; skipping reset")
                return False

            branch = _check(["git", "branch", "--show-current"], repo)
            pre_head = _check(["git", "rev-parse", "HEAD"], repo)

            if _remote_head_matches_local(repo, remotes[0], branch, pre_head):
                logger.debug(f"Up-to-date: local HEAD matches origin/{branch} for {set_name}")
                return True

            _retry_call(["git", "fetch"], repo)
            _call(["git", "reset", "--soft", f"origin/{branch}"], repo)

            post_head = _check(["git", "rev-parse", "HEAD"], repo)
            if pre_head != post_head:
                changes = _check(["git", "log", "--oneline", "-1"], repo)
                logger.info(f"Reset {set_name} to origin/HEAD: {changes}")
                _restore_new_files_from_head(repo)
            else:
                logger.debug(f"No changes reset for {set_name}")

            return True

        except subprocess.CalledProcessError as e:
            # `e.stdout` / `e.stderr` are None when the subprocess wasn't
            # run with stdout/stderr capture — guard against `.strip()`
            # on None.
            out = e.stdout.strip() if getattr(e, "stdout", None) else "N/A"
            err = e.stderr.strip() if getattr(e, "stderr", None) else "N/A"
            logger.error(f"Reset failed for {set_name} (rc={e.returncode}): stdout='{out}', stderr='{err}'")
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
    # The pyops set ships with the application source — it's checked
    # into the pyops repo under app/pyops/. No per-set .git, no init,
    # no commit dance: scriptlets there are versioned with pyops itself.
    if set == files.BUNDLED_SET_NAME:
        if not os.path.isdir(files.bundled_set_path):
            raise GitNotFoundError(f"Set '{set}' does not exist")
        return files.bundled_set_path

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
    Markdown: README at the repo root (README.md); any other markdown
        name lives under docs/{name}.md so multi-page documentation can
        ship with a set without colliding with scriptlet filenames.
    Data: {name}.{yaml|json} under data/, picked by which file exists.
        For brand-new data files, `type_="data"` defaults the extension
        to `.yaml`. The on-disk extension is the single source of
        truth — `name` is unique within data/ regardless of format.

    Args:
        repo (str): Repo path.
        name (str): Scriptlet name.
        type_ (str): Scriptlet type.

    Returns:
        str: File path.

    Raises:
        ValueError: Invalid type.
    """
    if type_ == "markdown":
        if name == "README":
            return os.path.join(repo, "README.md")
        return os.path.join(repo, "docs", f"{name}.md")
    if type_ == "view":
        return os.path.join(repo, f"{name}.html")
    if type_ == "jslibrary":
        return os.path.join(repo, f"{name}.js")
    if type_ == "data":
        data_dir = os.path.join(repo, "data")
        for ext in ("yaml", "yml", "json"):
            candidate = os.path.join(data_dir, f"{name}.{ext}")
            if os.path.isfile(candidate):
                return candidate
        # Brand-new data file: default to yaml. Callers that need json
        # use `data.write(name, format="json")` from the scriptlet side,
        # which goes through SetData (not get_file_path).
        return os.path.join(data_dir, f"{name}.yaml")
    if type_ not in ["task", "library", "controller"]:
        raise ValueError(f"Invalid type: {type_}")
    return os.path.join(repo, f"{name}.{type_}.py")

def get_name_from_file(filename: str) -> str:
    """Extracts name from filename.

    Views/Markdown/JS: Basename without extension (allows hyphens/multiples).
    Python: {name}.{type} → name; raise if no type dot.
    Markdown under docs/: pass the path relative to the repo (e.g.
        'docs/notes.md') → 'notes'. Top-level 'README.md' → 'README'.
    Data under data/: 'data/config.yaml' / 'data/state.json' → 'config' /
        'state'. Name is the basename without extension.

    Args:
        filename (str): e.g., 'myscript.task.py', 'SimpleDog-Head.html',
            'mylib.js', 'README.md', 'docs/notes.md', or 'data/config.yaml'.

    Returns:
        str: Name.

    Raises:
        ValueError: Invalid .py format.
    """
    if filename == "README.md":
        return "README"
    if filename.startswith("docs/") and filename.endswith(".md"):
        return os.path.splitext(os.path.basename(filename))[0]
    if filename.startswith("data/") and filename.endswith((".yaml", ".yml", ".json")):
        return os.path.splitext(os.path.basename(filename))[0]
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
    Markdown: top-level 'README.md' or any 'docs/*.md' → "markdown".
    Data: any 'data/*.{yaml,yml,json}' → "data".

    Args:
        filename (str): e.g., 'myscript.task.py', 'myview.html', 'mylib.js',
            'README.md', 'docs/notes.md', or 'data/config.yaml' (path
            relative to the repo).

    Returns:
        str: Type.

    Raises:
        ValueError: Invalid format.
    """
    if filename == "README.md":
        return "markdown"
    if filename.startswith("docs/") and filename.endswith(".md"):
        return "markdown"
    if filename.startswith("data/") and filename.endswith((".yaml", ".yml", ".json")):
        return "data"
    if filename.endswith('.html'):
        return "view"
    if filename.endswith('.js'):
        return "jslibrary"
    if not filename.endswith('.py'):
        raise ValueError(f"Not a Python/HTML/JS/markdown/data file: {filename}")
    base = os.path.splitext(filename)[0]
    parts = base.rsplit('.', 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid Python filename: {filename}")
    type_ = parts[1]
    if type_ not in ["task", "library", "controller"]:
        raise ValueError(f"Unknown type in filename: {type_}")
    return type_

def _scan_set_top_level(set_name: str, repo: str) -> List[Dict[str, str]]:
    """Scan the repo root for `.py` / `.html` / `.js` scriptlets and
    the top-level `README.md`. Type inferred via `infer_type_from_filename`."""
    out: List[Dict[str, str]] = []
    for filename in os.listdir(repo):
        if filename.endswith(('.py', '.html', '.js')) or filename == "README.md":
            try:
                out.append({
                    "set": set_name,
                    "name": get_name_from_file(filename),
                    "type": infer_type_from_filename(filename),
                })
            except ValueError:
                continue  # Skip filenames that don't match a known shape.
    return out


def _scan_set_subdir(set_name: str, repo: str, subdir: str,
                     extensions: tuple[str, ...],
                     include_ext: bool = False) -> List[Dict[str, str]]:
    """Scan `repo/<subdir>/` for files matching any of `extensions`.

    `include_ext=True` adds the actual on-disk extension as the `ext`
    field on each entry (used for `data/*.{yaml,yml,json}` so clients
    can mirror the format locally — yaml vs json affects both filename
    and syntax mode).
    """
    out: List[Dict[str, str]] = []
    dir_path = os.path.join(repo, subdir)
    if not os.path.isdir(dir_path):
        return out
    for filename in os.listdir(dir_path):
        if not filename.endswith(extensions):
            continue
        rel = f"{subdir}/{filename}"
        try:
            entry: Dict[str, str] = {
                "set": set_name,
                "name": get_name_from_file(rel),
                "type": infer_type_from_filename(rel),
            }
            if include_ext:
                entry["ext"] = os.path.splitext(filename)[1].lstrip(".")
            out.append(entry)
        except ValueError:
            continue
    return out


def list_scriptlets() -> List[Dict[str, str]]:
    """Lists all scriptlets across repos.

    Per set, three scanners run in sequence:
      - top-level: `*.py` / `*.html` / `*.js` / `README.md`
      - `docs/*.md`
      - `data/*.{yaml,yml,json}` (with `ext` field)

    Returns:
        List[Dict]: [{"set", "name", "type"[, "ext"]}, ...] sorted by
            (set, name).
    """
    git_base = get_git_base()
    sets = [d for d in os.listdir(git_base)
              if os.path.isdir(os.path.join(git_base, d))
              and os.path.exists(os.path.join(git_base, d, ".git"))]

    scriptlets: List[Dict[str, str]] = []
    for set_name in sorted(sets):
        repo = get_repo_path(set_name)
        scriptlets += _scan_set_top_level(set_name, repo)
        scriptlets += _scan_set_subdir(set_name, repo, "docs", (".md",))
        scriptlets += _scan_set_subdir(set_name, repo, "data",
                                       (".yaml", ".yml", ".json"),
                                       include_ext=True)

    return sorted(scriptlets, key=lambda x: (x["set"], x["name"]))

def list_scriptlet_files(repo: str, name: str) -> List[str]:
    """Lists scriptlet files for a given name in repo.

    Returned paths are relative to `repo`. Top-level scriptlet files
    stay bare (e.g. 'my_task.task.py'); a markdown file under `docs/`
    or a data file under `data/` is returned as 'docs/{name}.md' /
    'data/{name}.yaml' so callers can resolve the location without
    inspecting type separately.

    Matching uses filename-prefix-plus-extension rather than calling
    `get_name_from_file` per entry, so callers don't depend on every
    file in the repo being a well-formed scriptlet filename.

    Args:
        repo (str): Repo path.
        name (str): Scriptlet name.

    Returns:
        List[str]: Matching filenames (repo-relative).
    """
    files = []
    for filename in os.listdir(repo):
        # Top-level scriptlet shapes: {name}.{type}.py, {name}.html, {name}.js
        if filename.startswith(f"{name}.") and filename.endswith(('.py', '.html', '.js')):
            files.append(filename)
        # README.md at the top level is the only zero-extension match
        elif filename == "README.md" and name == "README":
            files.append(filename)
    docs_dir = os.path.join(repo, "docs")
    if os.path.isdir(docs_dir):
        for filename in os.listdir(docs_dir):
            if filename == f"{name}.md":
                files.append(f"docs/{filename}")
    data_dir = os.path.join(repo, "data")
    if os.path.isdir(data_dir):
        for ext in (".yaml", ".yml", ".json"):
            candidate = f"{name}{ext}"
            if os.path.isfile(os.path.join(data_dir, candidate)):
                files.append(f"data/{candidate}")
    return files



def check_name_unique(repo: str, name: str) -> bool:
    """Checks if name is unique across the set.

    Matches scriptlet filenames at the repo root ({name}.*.py / .html /
    .js / README.md), markdown files under docs/{name}.md, and data
    files under data/{name}.{yaml,yml,json}. Names share one namespace
    so the editor can resolve `{set}::{name}` unambiguously.

    Args:
        repo (str): Repo path.
        name (str): Name to check.

    Returns:
        bool: True if unique.
    """
    files = [f for f in os.listdir(repo) if f.startswith(f"{name}.") and (f.endswith(('.py', '.html', '.js')) or f == "README.md")]
    if files:
        return False
    docs_path = os.path.join(repo, "docs", f"{name}.md")
    if os.path.exists(docs_path):
        return False
    data_dir = os.path.join(repo, "data")
    if os.path.isdir(data_dir):
        for ext in ("yaml", "yml", "json"):
            if os.path.isfile(os.path.join(data_dir, f"{name}.{ext}")):
                return False
    return True


def check_data_name_unique(repo: str, name: str) -> bool:
    """Cross-format uniqueness inside a set's data/ subdir.

    True iff no `name.{yaml,yml,json}` exists in `data/git/{set}/data/`.
    Pyops-internal inventory creates use this in addition to
    `check_name_unique`; the SetData write path uses it through its own
    file-by-file resolution (see app/lib/data.py)."""
    from app.lib.data import check_data_name_unique as _impl
    return _impl(repo, name)

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

    # Pyops set has no per-set .git — read directly from disk, ignore
    # the committed/strict flags (they don't apply to pyops-shipped code).
    if os.path.abspath(repo) == files.bundled_set_path:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

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
    Markdown under docs/: creates the docs/ directory on demand.

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
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

def create_scriptlet(set_name: str, name: str, type_: str, content: str) -> str:
    """Atomic scriptlet creation: unique-name check + write under one lock.

    No commit, no push. The new file shows up as an untracked entry in
    `git status --porcelain` and is folded into the next set-wide
    `commit_drafts` batch alongside any subsequent edits. Holding the
    lock across the whole sequence prevents a same-name race where two
    concurrent callers both pass `check_name_unique` before either writes.

    Args:
        set_name (str): Set.
        name (str): Scriptlet name.
        type_ (str): Scriptlet type.
        content (str): Initial file content.

    Returns:
        str: Absolute path of the created file.

    Raises:
        GitNotFoundError: Set does not exist.
        FileExistsError: Name already taken in the set.
    """
    repo = get_repo_path(set_name)
    git_base = get_git_base()
    with repo_lock(git_base, set_name):
        if not check_name_unique(repo, name):
            raise FileExistsError(f"Name '{name}' already exists in set '{set_name}'")
        file_path = get_file_path(repo, name, type_)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    return file_path

def delete_file_unstaged(repo: str, file_path: str) -> None:
    """Remove a tracked scriptlet from the working tree.

    No `git rm`, no commit, no push: the deletion shows up as an
    unstaged change in `git status --porcelain` and gets folded into
    the next set-wide `commit_drafts` batch alongside any edits. Same
    methodology as the inventory's per-file edits (which write to the
    working tree via `/inventory/draft` without committing).

    Args:
        repo (str): Repo path.
        file_path (str): Absolute path of the file to remove.
    """
    git_base = get_git_base()
    set_name = os.path.basename(repo)
    with repo_lock(git_base, set_name):
        if os.path.exists(file_path):
            os.remove(file_path)

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
