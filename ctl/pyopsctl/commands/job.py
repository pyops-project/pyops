"""`pyopsctl job` — list / view / run / cancel / delete / download +
bulk cancel-all / clean.

Endpoints:
  GET    /scriptlet/job/list
  PUT    /scriptlet/job/item           body: {set, name, kind, drop_done, max_retries, param}
  GET    /scriptlet/job/item?id=…
  DELETE /scriptlet/job/item?id=…
  GET    /scriptlet/job/cancel?id=…
  GET    /scriptlet/job/download?id=…
"""
import sys
import time

from pyopsctl.client import Client
from pyopsctl.config import load
from pyopsctl.output import emit, emit_bytes

TERMINAL_STATES = {"done", "failed", "cancelled", "remove", "removed"}


def _list(args):
    with Client(load(args), timeout=args.timeout) as c:
        rows = c.get("/scriptlet/job/list") or []
        if args.state:
            rows = [r for r in rows if r.get("state") == args.state]
        if args.owner:
            rows = [r for r in rows if r.get("owner") == args.owner]
        if args.set:
            rows = [r for r in rows if r.get("set") == args.set]
        if args.name:
            rows = [r for r in rows if r.get("name") == args.name]
        emit(args, rows, id_field="id",
             table=(["id", "set", "kind", "state", "owner"],
                    [[r.get("id", ""), r.get("set", ""), r.get("kind", ""),
                      r.get("state", ""), r.get("owner", "")] for r in rows]))


def _view(args):
    with Client(load(args), timeout=args.timeout) as c:
        job = c.get("/scriptlet/job/item", params={"id": args.id})
        if not args.follow:
            _emit_view(args, job)
            return

        # Follow loop. Tail stdout/stderr deltas so re-runs don't re-print
        # already-shown bytes. Exit when terminal.
        sent_stdout = 0
        sent_stderr = 0
        while True:
            stdout = (job or {}).get("stdout") or ""
            stderr = (job or {}).get("stderr") or ""
            if args.stream in ("stdout", "both") and len(stdout) > sent_stdout:
                sys.stdout.write(stdout[sent_stdout:])
                sys.stdout.flush()
                sent_stdout = len(stdout)
            if args.stream in ("stderr", "both") and len(stderr) > sent_stderr:
                sys.stderr.write(stderr[sent_stderr:])
                sys.stderr.flush()
                sent_stderr = len(stderr)
            if (job or {}).get("state") in TERMINAL_STATES:
                break
            time.sleep(0.5)
            job = c.get("/scriptlet/job/item", params={"id": args.id})

        if args.json_mode:
            emit(args, job)


def _emit_view(args, job):
    if args.json_mode or args.quiet:
        emit(args, job, id_field="id")
        return
    print(f"job {job.get('id')}")
    print(f"  set/name: {job.get('set')}::{job.get('name', '')}")
    print(f"  state:    {job.get('state')}")
    print(f"  owner:    {job.get('owner')}")
    if job.get("detail"):
        print(f"  detail:   {job.get('detail')}")
    stdout = job.get("stdout")
    stderr = job.get("stderr")
    if args.stream in ("stdout", "both") and stdout:
        print("---- stdout ----")
        sys.stdout.write(stdout)
        if not stdout.endswith("\n"):
            sys.stdout.write("\n")
    if args.stream in ("stderr", "both") and stderr:
        print("---- stderr ----")
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")


def _run(args):
    param = ""
    if args.param is not None and args.param_file is not None:
        raise SystemExit("pyopsctl: --param and --param-file are mutually exclusive")
    if args.param is not None:
        param = args.param
    elif args.param_file is not None:
        with open(args.param_file, encoding="utf-8") as f:
            param = f.read()

    body = {
        "set": args.set,
        "name": args.name,
        "kind": args.kind,
        "param": param,
        "drop_done": args.drop_done,
        "max_retries": args.max_retries,
    }
    with Client(load(args), timeout=args.timeout) as c:
        resp = c.put("/scriptlet/job/item", json_body=body)
        # `runner.enqueue` returns the job ID string; the endpoint wraps it as
        # {"job": "<id>"} — see app/lib/runner.py:enqueue and routers/scriptlet/job.py.
        job_id = (resp or {}).get("job")
        emit(args, {"job_id": job_id, "set": args.set, "name": args.name,
                    "kind": args.kind},
             id_field="job_id",
             human=f"{args.kind} job {job_id} enqueued ({args.set}::{args.name})")


def _cancel(args):
    with Client(load(args), timeout=args.timeout) as c:
        c.get("/scriptlet/job/cancel", params={"id": args.id})
        emit(args, {"job_id": args.id, "state": "cancel"},
             id_field="job_id",
             human=f"job {args.id} cancel requested")


def _delete(args):
    with Client(load(args), timeout=args.timeout) as c:
        c.delete("/scriptlet/job/item", params={"id": args.id})
        emit(args, {"job_id": args.id, "state": "removed"},
             id_field="job_id",
             human=f"job {args.id} deleted")


def _download(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get_bytes("/scriptlet/job/download", params={"id": args.id})
        emit_bytes(args, data, out_path=args.out)


def _cancel_all(args):
    with Client(load(args), timeout=args.timeout) as c:
        rows = c.get("/scriptlet/job/list") or []
        targets = [r for r in rows if r.get("state") not in TERMINAL_STATES]
        if args.owner:
            targets = [r for r in targets if r.get("owner") == args.owner]
        if args.set:
            targets = [r for r in targets if r.get("set") == args.set]
        cancelled = []
        for r in targets:
            c.get("/scriptlet/job/cancel", params={"id": r["id"]})
            cancelled.append(r["id"])
        emit(args, cancelled, id_field=None,
             human=f"cancelled {len(cancelled)} job(s)")


def _clean(args):
    states = {"done": ["done"], "failed": ["failed"]}[args.state]
    with Client(load(args), timeout=args.timeout) as c:
        rows = c.get("/scriptlet/job/list") or []
        targets = [r for r in rows if r.get("state") in states]
        if args.owner:
            targets = [r for r in targets if r.get("owner") == args.owner]
        removed = []
        for r in targets:
            c.delete("/scriptlet/job/item", params={"id": r["id"]})
            removed.append(r["id"])
        emit(args, removed, id_field=None,
             human=f"deleted {len(removed)} {args.state} job(s)")


def register(subparsers, parents):
    p = subparsers.add_parser("job", help="job lifecycle: list / view / run / cancel / delete / download / bulk",
                              parents=parents)
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    lst = sub.add_parser("list", help="list jobs (optionally filter by --state/--owner/--set/--name)",
                         parents=parents)
    for f in ("state", "owner", "set", "name"):
        lst.add_argument(f"--{f}", default=None)
    lst.set_defaults(func=_list)

    vw = sub.add_parser("view", help="show a job (optionally --follow to stream until terminal)",
                        parents=parents)
    vw.add_argument("id")
    vw.add_argument("--follow", "-f", action="store_true",
                    help="poll stdout/stderr deltas until the job reaches a terminal state")
    vw.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    vw.set_defaults(func=_view)

    rn = sub.add_parser("run", help="enqueue a job (task/service/debug/draft/test)", parents=parents)
    rn.add_argument("set"); rn.add_argument("name")
    rn.add_argument("--kind", choices=["task", "service", "debug", "draft", "test"], default="task")
    rn.add_argument("--param", metavar="TEXT", default=None,
                    help="param to pass to the scriptlet")
    rn.add_argument("--param-file", metavar="PATH", default=None,
                    help="read --param from a file")
    rn.add_argument("--drop-done", action="store_true",
                    help="auto-delete the row when it reaches `done` (default: keep)")
    rn.add_argument("--max-retries", type=int, default=0)
    rn.set_defaults(func=_run)

    cn = sub.add_parser("cancel", help="cancel a running job", parents=parents)
    cn.add_argument("id"); cn.set_defaults(func=_cancel)

    dl = sub.add_parser("delete", help="delete a job row (only when terminal)", parents=parents)
    dl.add_argument("id"); dl.set_defaults(func=_delete)

    dn = sub.add_parser("download", help="download a job's workspace as a zip",
                        parents=parents)
    dn.add_argument("id"); dn.add_argument("--out", metavar="PATH", default=None,
                                            help="write to PATH (default: stdout, binary)")
    dn.set_defaults(func=_download)

    cna = sub.add_parser("cancel-all", help="cancel every non-terminal job (optionally scoped)",
                         parents=parents)
    cna.add_argument("--owner", default=None); cna.add_argument("--set", default=None)
    cna.set_defaults(func=_cancel_all)

    cl = sub.add_parser("clean", help="delete jobs in a terminal state (done or failed)",
                        parents=parents)
    cl.add_argument("--state", choices=["done", "failed"], required=True)
    cl.add_argument("--owner", default=None)
    cl.set_defaults(func=_clean)
