"""Set-scoped YAML/JSON data files.

Each set may carry small structured data files under
`data/git/{set}/data/{name}.{yaml,yml,json}`. Tasks and libraries inside
the same set access them through the `data` global injected by
`job_runner.setup_job_globals` — there is no cross-set surface, by
construction (the API is bound to the calling job's set name).

Concurrency model: per-file flock. Reads acquire LOCK_SH (shared),
writes acquire LOCK_EX (exclusive). The lock file is a `.{name}.lock`
sidecar next to the data file. It is persistent — never unlinked —
mirroring the pattern in `lib/git.py:repo_lock` and
`lib/sets.py:set_install_lock`. Different data files in the same set use
different lock files so unrelated I/O does not serialise.

Name uniqueness: a `name` is unique within a set's `data/` regardless of
format. `config.yaml` and `config.json` cannot coexist; the second
create raises `FileExistsError`. This matches operator expectations
(`data.read('config')` should never be ambiguous) and is enforced both
at the library entry points and at HTTP write time.

Atomicity on write: the new content is serialised to
`{name}.{ext}.tmp` then `os.replace`'d into place under the exclusive
lock. Readers either see the previous version or the new one — never a
torn write.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from typing import Any, ContextManager, Iterator

# `yaml` is imported lazily inside `_deserialise` / `_serialise` so that
# scriptlets in sets that don't carry yaml/json data files don't pay the
# pyyaml import cost — and per-set venvs without pyyaml installed only
# fail if they actually try to read/write a yaml file (json works on
# stdlib alone).


_SUPPORTED_EXTS = ("yaml", "yml", "json")


class SetData:
    """`data` global injected into every scriptlet's namespace.

    Bound to the calling job's set name (`setname`) and to the absolute
    path of that set's git repo. The instance never accepts another set
    name — there is no API surface for cross-set access.
    """

    def __init__(self, setname: str, repo_path: str):
        self._setname = setname
        self._dir = os.path.join(repo_path, "data")
        self._gitignore = os.path.join(repo_path, ".gitignore")

    # ------------------------------------------------------------------ public API

    def list(self) -> list[str]:
        """Return the names (no extension) of every data file in the set."""
        if not os.path.isdir(self._dir):
            return []
        names = []
        for filename in os.listdir(self._dir):
            if filename.startswith("."):
                continue
            base, ext = os.path.splitext(filename)
            if ext.lstrip(".").lower() in _SUPPORTED_EXTS:
                names.append(base)
        return sorted(names)

    @contextmanager
    def read(self, name: str) -> Iterator[Any]:
        """Acquire a shared lock and yield the parsed contents of {name}.

        Format is auto-detected by extension. Two concurrent `read` calls
        in different jobs proceed in parallel; a concurrent `write`
        blocks both until it finishes.
        """
        self._validate_name(name)
        file_path = self._resolve_existing(name)
        ext = os.path.splitext(file_path)[1].lstrip(".").lower()
        lock_path = self._lock_path(name)
        self._ensure_dirs()
        with open(lock_path, "a") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_SH)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                yield _deserialise(raw, ext)
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def write(self, name: str, format: str | None = None) -> Iterator[Any]:
        """Acquire an exclusive lock and yield a mutable object.

        On context-manager exit the object is serialised back to the
        file via a `.tmp` + `os.replace` swap. If the file does not yet
        exist, `format` must be supplied (`"yaml"` or `"json"`). If it
        does exist, the existing format is kept and `format`, if
        provided, is checked for consistency (mismatch → ValueError).
        """
        self._validate_name(name)
        self._ensure_dirs()
        ext, existed = self._resolve_for_write(name, format)
        file_path = os.path.join(self._dir, f"{name}.{ext}")
        lock_path = self._lock_path(name)
        with open(lock_path, "a") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                if existed:
                    with open(file_path, "r", encoding="utf-8") as f:
                        obj = _deserialise(f.read(), ext)
                else:
                    obj = {}
                holder = _Holder(obj)
                yield holder.obj
                tmp_path = f"{file_path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(_serialise(holder.obj, ext))
                os.replace(tmp_path, file_path)
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------ internals

    def _validate_name(self, name: str) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("data name must be a non-empty string")
        if any(c in name for c in ("/", "\\")) or ".." in name:
            raise ValueError(f"invalid data name (no path separators or '..'): {name!r}")
        if name.startswith("."):
            raise ValueError(f"data name must not start with '.': {name!r}")

    def _ensure_dirs(self) -> None:
        os.makedirs(self._dir, exist_ok=True)
        self._ensure_gitignore()

    def _ensure_gitignore(self) -> None:
        """Make sure the set's `.gitignore` excludes lock sidecars under data/.

        Idempotent: appends the entry only if missing. The `.gitignore`
        is itself committed (the bootstrap path in git.py adds it), so
        the append is durable across runs.
        """
        entry = "data/.*.lock\n"
        try:
            existing = ""
            if os.path.exists(self._gitignore):
                with open(self._gitignore, "r", encoding="utf-8") as f:
                    existing = f.read()
            if entry.rstrip() not in (line.rstrip() for line in existing.splitlines()):
                with open(self._gitignore, "a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(entry)
        except OSError:
            # Best-effort: a missing parent dir or read-only mount
            # shouldn't break data reads/writes. The lock files still
            # work; they just won't be gitignored.
            pass

    def _lock_path(self, name: str) -> str:
        return os.path.join(self._dir, f".{name}.lock")

    def _resolve_existing(self, name: str) -> str:
        candidates = self._find_candidates(name)
        if not candidates:
            raise FileNotFoundError(f"data '{name}' does not exist in set '{self._setname}'")
        if len(candidates) > 1:
            extensions = sorted(os.path.splitext(p)[1] for p in candidates)
            raise FileExistsError(
                f"data '{name}' exists in multiple formats {extensions} — refuse to pick one"
            )
        return candidates[0]

    def _find_candidates(self, name: str) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        out = []
        for ext in _SUPPORTED_EXTS:
            path = os.path.join(self._dir, f"{name}.{ext}")
            if os.path.isfile(path):
                out.append(path)
        return out

    def _resolve_for_write(self, name: str, format: str | None) -> tuple[str, bool]:
        """Pick the on-disk extension and tell the caller whether the
        file exists already.

        Cross-format collisions raise: a single `name` is unique within
        the set's `data/` regardless of format, so writing `name` as
        JSON when `name.yaml` exists (or vice versa) is rejected.
        """
        candidates = self._find_candidates(name)
        if format is not None:
            normalised = format.lower()
            if normalised == "yml":
                normalised = "yaml"
            if normalised not in ("yaml", "json"):
                raise ValueError(f"unsupported format: {format!r}")
            requested_ext = "yaml" if normalised == "yaml" else "json"
        else:
            requested_ext = None
        if candidates:
            existing_path = candidates[0]
            existing_ext = os.path.splitext(existing_path)[1].lstrip(".").lower()
            if len(candidates) > 1:
                extensions = sorted(os.path.splitext(p)[1] for p in candidates)
                raise FileExistsError(
                    f"data '{name}' exists in multiple formats {extensions} — refuse to pick one"
                )
            if requested_ext is not None and not _formats_match(existing_ext, requested_ext):
                raise FileExistsError(
                    f"data '{name}' already exists as .{existing_ext}; refusing to write as .{requested_ext}"
                )
            return existing_ext, True
        if requested_ext is None:
            raise ValueError(
                f"data '{name}' does not exist; `format` is required (yaml or json) to create it"
            )
        return requested_ext, False


# ---------------------------------------------------------------- module helpers


def check_data_name_unique(repo: str, name: str) -> bool:
    """Cross-format uniqueness check used by HTTP-side creates.

    Returns False if any `name.{yaml,yml,json}` exists in the set's
    `data/` subdir. Mirrors `git.check_name_unique` for scriptlets so
    inventory-router creates fail at the right boundary."""
    data_dir = os.path.join(repo, "data")
    if not os.path.isdir(data_dir):
        return True
    for ext in _SUPPORTED_EXTS:
        if os.path.isfile(os.path.join(data_dir, f"{name}.{ext}")):
            return False
    return True


class _Holder:
    """Wrapper that lets `data.write(...)` yield the same dict identity
    twice (once on entry, once on exit) so authors can do
    `with data.write(...) as obj: obj['k'] = v` and the mutation is
    visible on close."""

    __slots__ = ("obj",)

    def __init__(self, obj: Any):
        self.obj = obj


def _deserialise(raw: str, ext: str) -> Any:
    ext = ext.lower()
    if ext == "json":
        return json.loads(raw) if raw.strip() else {}
    if ext in ("yaml", "yml"):
        import yaml  # lazy: see top of module
        return yaml.safe_load(raw) if raw.strip() else {}
    raise ValueError(f"unsupported data extension: {ext!r}")


def _serialise(obj: Any, ext: str) -> str:
    ext = ext.lower()
    if ext == "json":
        return json.dumps(obj, indent=2, sort_keys=True) + "\n"
    if ext in ("yaml", "yml"):
        import yaml  # lazy: see top of module
        return yaml.safe_dump(obj, sort_keys=True, default_flow_style=False)
    raise ValueError(f"unsupported data extension: {ext!r}")


def _formats_match(a: str, b: str) -> bool:
    """yaml and yml are the same on-disk format from the writer's POV."""
    norm = {"yaml": "yaml", "yml": "yaml", "json": "json"}
    return norm.get(a) == norm.get(b)
