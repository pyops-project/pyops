# Task: refresh the per-set venv and re-install scriptlet dependencies in
# upgrade mode.
#
# Mirrors the semantics of the (now-retired) app.lib.sets.update_set_environment:
#  - ensures the per-set venv exists (creates with python -m venv if not)
#  - upgrades pip / setuptools / wheel, then pyops CORE_DEPS
#  - scans every *.py scriptlet in the set for #! pip / git directives
#  - resolves spec/branch conflicts via newest-mtime-wins
#  - runs pip install --upgrade --upgrade-strategy eager per resolved pip
#    directive, and clone-or-pull-then-pip-install-e per resolved git
#    directive
#  - writes <venv>/<set>/.update_warnings.json with the conflict list
#    (kept out of <git>/<set>/ on purpose — that tree is user content;
#    the warnings file is pyops runtime state); the /health endpoint
#    reads it and surfaces non-empty conflicts as degraded
#
# Holds install_lock(name) for the whole flow, so running jobs in the
# same set queue behind the upgrade. Other sets are unaffected.
#
# Params:
#   name: set to upgrade (required)

#!{::set}
import json
import os
import re
import subprocess
import sys

CORE_DEPS = ("pyzmq", "msgpack", "psutil", "requests")
_PIP_PKG_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(.*)$")


def _ensure_venv(name):
    """Create the per-set venv if missing and ensure CORE_DEPS are present.
    Mirrors app.lib.sets.ensure_set_environment but inlined so this task
    has no app.lib imports."""
    venv = _venv_dir(name)
    pkg = _pkg_dir(name)
    os.makedirs(venv, exist_ok=True)
    os.makedirs(pkg, exist_ok=True)
    py_bin = os.path.join(venv, "bin", "python")
    if not os.path.exists(py_bin):
        run_cmd([sys.executable, "-m", "venv", venv])
    pip_bin = os.path.join(venv, "bin", "pip")
    run_cmd([pip_bin, "install", *CORE_DEPS])
    return pip_bin


def _scriptlet_files(name):
    set_dir = _set_dir(name)
    if not os.path.isdir(set_dir):
        return []
    out = []
    for entry in sorted(os.listdir(set_dir)):
        if entry.startswith("."):
            continue
        full = os.path.join(set_dir, entry)
        if os.path.isfile(full) and entry.endswith(".py"):
            out.append(full)
    return out


def _parse_directives(text):
    """Yield {'kind': 'pip'|'git', ...} dicts for each #! line, skipping
    library refs (`#!{...}` / `::`)."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#!"):
            continue
        rest = stripped[2:].lstrip()
        if not rest or rest.startswith("{") or "::" in rest:
            continue
        args = rest.split()
        arg1 = args[0]
        if "://" in arg1 or arg1.startswith("git@"):
            out.append({"kind": "git", "url": arg1, "branch": args[1] if len(args) > 1 else None})
        else:
            m = _PIP_PKG_RE.match(arg1)
            if not m:
                continue
            pkg = m.group(1).lower().replace("_", "-")
            out.append({"kind": "pip", "pkg": pkg, "spec": m.group(2), "raw": args})
    return out


def _scan(name):
    pips, gits = [], []
    for path in _scriptlet_files(name):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        mtime = os.path.getmtime(path)
        source = os.path.basename(path)
        for d in _parse_directives(text):
            d["source"] = source
            d["mtime"] = mtime
            (pips if d["kind"] == "pip" else gits).append(d)
    return pips, gits


def _resolve(directives, key):
    """Group by `key(d)`, return (winners, conflicts). When a group has
    multiple distinct specs/branches the newest-mtime wins; the others go
    into the conflict list."""
    by_key = {}
    for d in directives:
        by_key.setdefault(key(d), []).append(d)
    winners, conflicts = [], []
    for k, group in by_key.items():
        if len(group) == 1:
            winners.append(group[0])
            continue
        # All agree on the spec/branch? Use the first.
        distinct = {d.get("spec", d.get("branch")) for d in group}
        if len(distinct) == 1:
            winners.append(group[0])
            continue
        ordered = sorted(group, key=lambda d: d["mtime"], reverse=True)
        winner = ordered[0]
        winners.append(winner)
        conflicts.append({
            "winner": {k_: winner[k_] for k_ in ("source", "mtime", "spec", "branch") if k_ in winner},
            "losers": [{k_: l[k_] for k_ in ("source", "mtime", "spec", "branch") if k_ in l} for l in ordered[1:]],
            **({"pkg": winner["pkg"]} if "pkg" in winner else {"url": winner["url"]}),
        })
    return winners, conflicts


def _install_pip(pip_bin, directive):
    args = directive["raw"]
    run_cmd([pip_bin, "install", "--no-cache-dir", "--upgrade", "--upgrade-strategy", "eager", *args])


def _install_git(pip_bin, directive, name):
    url = directive["url"]
    branch = directive.get("branch")
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    repo_dir = os.path.join(_pkg_dir(name), repo_name)
    if os.path.exists(os.path.join(repo_dir, ".git")):
        if branch:
            run_cmd(["git", "checkout", branch], cwd=repo_dir)
        run_cmd(["git", "pull"], cwd=repo_dir)
    else:
        clone_args = ["git", "clone"]
        if branch:
            clone_args += ["--branch", branch]
        clone_args += [url, repo_dir]
        run_cmd(clone_args)
    run_cmd([pip_bin, "install", "--no-cache-dir", "--upgrade", "-e", repo_dir])


def main():
    if not name:
        raise ValueError("name is required")
    if name == "pyops":
        raise ValueError("'pyops' is pyops-managed; updates flow via pyops deployment, not this task")

    set_dir = _set_dir(name)
    if not os.path.exists(set_dir):
        raise ValueError(f"Set '{name}' not found.")

    status(f"update dependencies for set '{name}'")

    with install_lock(name):
        pip_bin = _ensure_venv(name)
        run_cmd([pip_bin, "install", "--upgrade", "pip", "setuptools", "wheel"])
        run_cmd([pip_bin, "install", "--upgrade", *CORE_DEPS])

        pips, gits = _scan(name)
        pip_winners, pip_conflicts = _resolve(pips, lambda d: d["pkg"])
        git_winners, git_conflicts = _resolve(gits, lambda d: d["url"])

        for c in pip_conflicts:
            print(f"pip conflict: {c['pkg']} → chose {c['winner'].get('spec') or '(any)'} from {c['winner']['source']}", flush=True)
        for c in git_conflicts:
            print(f"git conflict: {c['url']} → chose branch={c['winner'].get('branch') or '(default)'} from {c['winner']['source']}", flush=True)

        for d in pip_winners:
            _install_pip(pip_bin, d)
        for d in git_winners:
            _install_git(pip_bin, d, name)

        summary = {
            "set": name,
            "conflicts": {"pip": pip_conflicts, "git": git_conflicts},
            "installed": {
                "pip": [{"pkg": d["pkg"], "spec": d["spec"], "source": d["source"]} for d in pip_winners],
                "git": [{"url": d["url"], "branch": d.get("branch"), "source": d["source"]} for d in git_winners],
            },
        }
        warnings_path = os.path.join(_venv_dir(name), ".update_warnings.json")
        os.makedirs(os.path.dirname(warnings_path), exist_ok=True)
        with open(warnings_path, "w") as f:
            json.dump(summary, f, indent=2)

        n = len(pip_conflicts) + len(git_conflicts)
        print(f"\nUpdate completed for '{name}' — {n} conflict(s).", flush=True)


if __name__ == "__main__":
    main()
