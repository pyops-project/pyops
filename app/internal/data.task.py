# Service: REST API for pyops-managed data operations.
#
# Routes the controller-proxy traffic for the Sets view. Mutating
# operations (clone / create / pull / switch / remove / update_deps) are
# enqueued as set_*.task.py jobs in the internal set with drop_done=True;
# the response carries the job id so the UI can poll
# /scriptlet/job/item?id=<id> for live output. Read-only operations
# (/list) walk the filesystem directly.
#
# Scope: pyops internal operations on the data layer (data/git/, venvs,
# pkg dirs). Future internal services can sit next to this one for other
# domains.
import json
import logging
import os
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _git_base():
    base = config["data"]["git"]
    return base if os.path.isabs(base) else os.path.join(os.path.dirname(appspace), base)


def _list_sets():
    base = _git_base()
    if not os.path.isdir(base):
        return []
    out = []
    for entry in sorted(os.listdir(base)):
        if entry == "internal":
            continue
        full = os.path.join(base, entry)
        if not (os.path.isdir(full) and os.path.exists(os.path.join(full, ".git"))):
            continue
        try:
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=full, text=True
            ).strip()
        except subprocess.CalledProcessError:
            branch = "unknown"
        out.append({"name": entry, "branch": branch})
    return out


def _format_param(kwargs):
    """Render a param string of Python literal assignments so the target
    task picks the vars up via the runner's `exec(param, glob)` step
    (job_runner.execute_job_code)."""
    return "\n".join(f"{k} = {repr(v)}" for k, v in kwargs.items()) + "\n"


def _enqueue(scriptlet, params, user):
    job_id = control.job.create(
        set="internal",
        name=scriptlet,
        drop_done=True,
        param=_format_param(params),
        user=user,
    )
    return {"job": job_id}, 200


def _payload_dict(msg):
    """The controller proxy stashes the request body under msg['data'] and
    notes the type in msg['data_type']. For JSON requests we get a dict
    already; for everything else we fall back to {} so the caller can
    still pull query-param fields from `msg`."""
    if msg.get("data_type") == "json" and isinstance(msg.get("data"), dict):
        return msg["data"]
    return {}


def handle_request(msg):
    type_ = msg.get("type")
    path = msg.get("path", "")
    user = msg.get("user", "anonymous")
    body = _payload_dict(msg)

    if path == "/list" and type_ == "GET":
        return {"sets": _list_sets()}, 200

    if path == "/clone" and type_ == "POST":
        url = body.get("url")
        if not url:
            return {"error": "url is required"}, 400
        return _enqueue("set_clone", {
            "url": url,
            "username": body.get("username", ""),
            "password": body.get("password", ""),
            "git_name": body.get("git_name", ""),
            "git_email": body.get("git_email", ""),
        }, user)

    if path == "/create" and type_ == "POST":
        name = body.get("name")
        if not name:
            return {"error": "name is required"}, 400
        return _enqueue("set_create", {"name": name}, user)

    if path.startswith("/pull/") and type_ == "POST":
        name = path[len("/pull/"):]
        if not name:
            return {"error": "name is required in path"}, 400
        return _enqueue("set_pull", {"name": name}, user)

    if path == "/switch" and type_ == "POST":
        name = body.get("name")
        branch = body.get("branch")
        if not name or not branch:
            return {"error": "name and branch are required"}, 400
        return _enqueue("set_switch", {"name": name, "branch": branch}, user)

    if path.startswith("/remove/") and type_ == "DELETE":
        name = path[len("/remove/"):]
        if not name:
            return {"error": "name is required in path"}, 400
        return _enqueue("set_remove", {"name": name}, user)

    if path.startswith("/update_deps/") and type_ == "POST":
        name = path[len("/update_deps/"):]
        if not name:
            return {"error": "name is required in path"}, 400
        return _enqueue("set_update_deps", {"name": name}, user)

    return {"error": f"Not found: {type_} {path}"}, 404


def main():
    while True:
        msg = listener.listen()
        if msg == "TERM":
            break
        try:
            result, status_code = handle_request(msg)
        except Exception as e:
            logger.exception("handler failed")
            result, status_code = {"error": str(e)}, 500
        listener.response(result, status_code)


if __name__ == "__main__":
    logger.info("data service started")
    main()
    logger.info("data service stopped")
