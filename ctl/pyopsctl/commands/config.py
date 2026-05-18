"""`pyopsctl config show` / `config check`.

Local-only commands that inspect the resolved profile and probe the
server for reachability. `show` prints the profile with `api_key`
redacted. `check` hits `GET /version` and reports whether the auth
pathway used was Basic (via `api_key`) or `X-User` direct.
"""
from pyopsctl.client import Client, HTTPError
from pyopsctl.config import load, redact
from pyopsctl.output import emit


def _show(args):
    profile = load(args)
    emit(args, redact(profile),
         human="\n".join(f"{k:>14}: {v}" for k, v in redact(profile).items() if v is not None))


def _check(args):
    profile = load(args)
    pathway = "basic" if profile.api_key else "x-user"
    try:
        with Client(profile, timeout=args.timeout) as c:
            server = c.get("/version") or {}
    except HTTPError as ex:
        emit(args, {
            "profile": profile.name, "url": profile.url, "auth_pathway": pathway,
            "reachable": False, "error": ex.message, "status": ex.status,
        }, human=f"{profile.name} ({profile.url}) UNREACHABLE — {ex.message}")
        return 7  # server-unreachable
    emit(args, {
        "profile": profile.name, "url": profile.url, "auth_pathway": pathway,
        "reachable": True, "server_version": server.get("version"),
    }, human=f"{profile.name} ({profile.url}) ok — server {server.get('version')} via {pathway}")


def register(subparsers, parents):
    p = subparsers.add_parser("config", help="inspect resolved profile and probe the server",
                              parents=parents)
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    sw = sub.add_parser("show", help="print resolved profile (api_key redacted)",
                        parents=parents)
    sw.set_defaults(func=_show)

    ck = sub.add_parser("check", help="reachability + auth-pathway smoke test",
                        parents=parents)
    ck.set_defaults(func=_check)
