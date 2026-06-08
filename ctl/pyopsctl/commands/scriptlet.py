"""`pyopsctl scriptlet` — read / create / draft / delete / diff / inspect scriptlets.

Endpoints:
  GET    /scriptlet/inventory/list
  GET    /scriptlet/inventory/sets
  GET    /scriptlet/inventory/status?set=&name=
  GET    /scriptlet/inventory/code?set=&name=
  PUT    /scriptlet/inventory/item    body: {set, name, type}   (creates)
  POST   /scriptlet/inventory/draft   body: {set, name, code}   (no commit)
  DELETE /scriptlet/inventory/item?set=&name=
  GET    /repo/status?set=

The per-scriptlet commit verb is gone — commit drafts set-wide via the
sets view's "commit drafts" button (or `pyopsctl job run internal
set_commit_drafts --param 'name="<set>"; message="..."'`).
"""
import difflib
import sys

from pyopsctl.client import Client
from pyopsctl.config import load
from pyopsctl.output import emit


def _list(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/scriptlet/inventory/list")
        rows = data if isinstance(data, list) else []
        if args.set:
            rows = [r for r in rows if r.get("set") == args.set]
        emit(args, rows, id_field="name",
             table=(["set", "name", "type"],
                    [[r.get("set", ""), r.get("name", ""), r.get("type", "")] for r in rows]))


def _sets(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/scriptlet/inventory/sets") or {}
        sets = data.get("sets") or []
        emit(args, sets, table=(["set"], [[s] for s in sets]))


def _status(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/scriptlet/inventory/status",
                     params={"set": args.set, "name": args.name})
        emit(args, data,
             human=f"{args.set}::{args.name}: unstaged={data.get('has_unstaged')}")


def _repo_status(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/repo/status", params={"set": args.set})
        emit(args, data,
             human=f"{args.set}: branch={data.get('branch')} tag={data.get('tag')}")


def _get_code(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/scriptlet/inventory/code",
                     params={"set": args.set, "name": args.name})
        code = (data or {}).get("code", "")
        if args.json_mode:
            emit(args, data)
        else:
            sys.stdout.write(code)
            if not code.endswith("\n"):
                sys.stdout.write("\n")


def _create(args):
    with Client(load(args), timeout=args.timeout) as c:
        c.put("/scriptlet/inventory/item",
              json_body={"set": args.set, "name": args.name, "type": args.kind})
        emit(args, {"set": args.set, "name": args.name, "type": args.kind},
             human=f"created {args.set}::{args.name} ({args.kind})")


def _draft(args):
    code = _read_code_source(args)
    with Client(load(args), timeout=args.timeout) as c:
        c.post("/scriptlet/inventory/draft",
               json_body={"set": args.set, "name": args.name, "code": code})
        emit(args, {"set": args.set, "name": args.name, "bytes": len(code)},
             human=f"draft written for {args.set}::{args.name} ({len(code)} bytes)")


def _delete(args):
    with Client(load(args), timeout=args.timeout) as c:
        c.delete("/scriptlet/inventory/item",
                 params={"set": args.set, "name": args.name})
        emit(args, {"set": args.set, "name": args.name},
             human=f"deleted {args.set}::{args.name}")


def _diff(args):
    """Local file vs remote draft (default), or local-vs-server output.
    Falls back to a no-op diff when contents match."""
    with Client(load(args), timeout=args.timeout) as c:
        remote = c.get("/scriptlet/inventory/code",
                       params={"set": args.set, "name": args.name})
        remote_code = (remote or {}).get("code", "")

    if args.local_file:
        with open(args.local_file, encoding="utf-8") as f:
            local_code = f.read()
        a_label, b_label = args.local_file, f"{args.set}::{args.name}@remote"
        a_text, b_text = local_code, remote_code
    else:
        # Without --local-file we just print the remote code (no diff).
        sys.stdout.write(remote_code)
        if not remote_code.endswith("\n"):
            sys.stdout.write("\n")
        return

    diff = difflib.unified_diff(
        a_text.splitlines(keepends=True),
        b_text.splitlines(keepends=True),
        fromfile=a_label, tofile=b_label,
    )
    sys.stdout.writelines(diff)


def _read_code_source(args) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    raise SystemExit("pyopsctl: provide --file PATH or --stdin to source the scriptlet body")


def register(subparsers, parents):
    p = subparsers.add_parser("scriptlet", help="scriptlet inventory and draft/save workflow",
                              parents=parents)
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    lst = sub.add_parser("list", help="list scriptlets (optionally filter by --set)",
                         parents=parents)
    lst.add_argument("--set", default=None)
    lst.set_defaults(func=_list)

    sub.add_parser("sets", help="list configured sets", parents=parents).set_defaults(func=_sets)

    st = sub.add_parser("status", help="check unstaged changes for a scriptlet", parents=parents)
    st.add_argument("set"); st.add_argument("name"); st.set_defaults(func=_status)

    rs = sub.add_parser("repo-status", help="show set repo branch/tag", parents=parents)
    rs.add_argument("set"); rs.set_defaults(func=_repo_status)

    gc = sub.add_parser("get-code", help="print scriptlet source (currently unstaged from server)",
                        parents=parents)
    gc.add_argument("set"); gc.add_argument("name"); gc.set_defaults(func=_get_code)

    cr = sub.add_parser("create", help="create a new (empty) scriptlet", parents=parents)
    cr.add_argument("set"); cr.add_argument("name")
    cr.add_argument("--kind", required=True,
                    choices=["task", "service", "schedule", "controller", "view",
                             "library", "markdown", "jslibrary", "data"],
                    help="scriptlet type (passed as `type` to the API)")
    cr.set_defaults(func=_create)

    dr = sub.add_parser("draft", help="write a draft (unstaged, no commit)", parents=parents)
    dr.add_argument("set"); dr.add_argument("name")
    dr.add_argument("--file", metavar="PATH", default=None,
                    help="read draft body from PATH")
    dr.add_argument("--stdin", action="store_true",
                    help="read draft body from stdin")
    dr.set_defaults(func=_draft)

    dl = sub.add_parser("delete", help="delete a scriptlet (remote + commits the deletion)",
                        parents=parents)
    dl.add_argument("set"); dl.add_argument("name"); dl.set_defaults(func=_delete)

    df = sub.add_parser("diff", help="unified diff of local file vs remote scriptlet",
                        parents=parents)
    df.add_argument("set"); df.add_argument("name")
    df.add_argument("--local-file", metavar="PATH", default=None,
                    help="path of the local file to compare against the remote")
    df.set_defaults(func=_diff)
