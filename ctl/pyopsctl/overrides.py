"""Local-source override map for `kind in (debug, test)` job submissions.

Both `pyopsctl debug start` and `pyopsctl job run --kind test` need to
ship the operator's *unpushed* working copy of a scriptlet to the
server, so the runner executes that body instead of `git.read_content`
against the committed tree. The server stores the map verbatim on the
`Job.overrides` column and `app/runner.py loadJob()` consumes
`override_dict["{set}::{name}"]` regardless of kind — see
`app/routers/scriptlet/job.py` and `app/runner.py:loadJob`.

`build_overrides()` is the source of that map on the client side.
"""
import os


def build_overrides(overrides_dir: str | None, set_name: str) -> dict[str, str] | None:
    """Walk `overrides_dir/<set>/*.py` and return `{f"{set}::{name}": body}`.

    Returns None when no `overrides_dir` was supplied (caller omits the
    `overrides` body key entirely), and an empty dict when the directory
    exists but holds no scriptlets for `set_name` (signals "explicit
    opt-in, just nothing local yet" — distinct from None so a future
    server-side strict mode can tell the two apart).
    """
    if not overrides_dir:
        return None
    set_dir = os.path.join(overrides_dir, set_name)
    if not os.path.isdir(set_dir):
        return {}
    result: dict[str, str] = {}
    for entry in sorted(os.listdir(set_dir)):
        if not entry.endswith(".py"):
            continue
        stem = entry.split(".", 1)[0]
        path = os.path.join(set_dir, entry)
        with open(path, encoding="utf-8") as f:
            result[f"{set_name}::{stem}"] = f.read()
    return result
