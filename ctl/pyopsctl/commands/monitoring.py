"""`pyopsctl health` / `health failed` / `metrics` / `version`.

All four endpoints are unauthenticated on the server side; pyopsctl still
sends `Authorization` / `X-User` because the proxy in front of pyops
typically requires it. The deployed model is documented in the README's
compose example.
"""
import sys

from pyopsctl import __version__ as _CLI_VERSION
from pyopsctl.client import Client
from pyopsctl.config import load
from pyopsctl.output import emit


def _health(args):
    with Client(load(args), timeout=args.timeout) as c:
        data = c.get("/health") or {}
        if args.full or args.json_mode:
            emit(args, data)
            return
        status = data.get("status", "unknown")
        subs = data.get("subsystems") or {}
        sub_summary = ", ".join(f"{k}={v}" for k, v in sorted(subs.items()))
        emit(args, data, human=f"status: {status}  ({sub_summary})" if sub_summary else f"status: {status}")


def _health_failed(args):
    with Client(load(args), timeout=args.timeout) as c:
        rows = c.get("/health/failed") or []
        emit(args, rows, id_field="id",
             table=(["id", "set", "name", "state", "detail"],
                    [[r.get("id", ""), r.get("set", ""), r.get("name", ""),
                      r.get("state", ""), (r.get("detail") or "")[:80]] for r in rows]))


def _metrics(args):
    with Client(load(args), timeout=args.timeout) as c:
        text = c.get_text("/metrics")
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def _version(args):
    with Client(load(args), timeout=args.timeout) as c:
        server = c.get("/version") or {}
    payload = {"server": server.get("version"), "cli": _CLI_VERSION}
    emit(args, payload,
         human=f"server: {payload['server']}\ncli:    {payload['cli']}")


def register(subparsers, parents):
    h = subparsers.add_parser("health", help="server health: aggregated status + per-subsystem",
                              parents=parents)
    hsub = h.add_subparsers(dest="action", required=False, metavar="ACTION")

    # Bare `health` → summary
    h.add_argument("--full", action="store_true",
                   help="print the full payload instead of the one-line summary")
    h.set_defaults(func=_health)

    failed = hsub.add_parser("failed", help="list every failed job in detail",
                             parents=parents)
    failed.set_defaults(func=_health_failed)

    m = subparsers.add_parser("metrics", help="raw Prometheus exposition",
                              parents=parents)
    m.set_defaults(func=_metrics)

    v = subparsers.add_parser("version", help="print server version and CLI version",
                              parents=parents)
    v.set_defaults(func=_version)
