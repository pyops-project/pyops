"""Unit tests for app/pyops/data.task.py — the sets data service behind
/controller/pyops/sets/*.

The service runs as a pyops-set service job; `pyops_job` builds its job
globals through the real `job_runner.setup_job_globals` (so
`validate_set_name` is the runner-injected check) and execs data.task.py
into them. `control` is a recorder, so an enqueue is visible without a
server; `handle_request` is called with the message shape the controller
proxy sends. Sets are real git repositories under the git base."""

import pytest


@pytest.fixture
def service(pyops_job, pyops_install):
    """The loaded data service, with `control` recording enqueues (each
    returns job id "job-1"). A repository next to the git base is what
    `/status/../outside` would report on, were the name not checked."""
    from unittest.mock import MagicMock
    (pyops_install["root"] / "data" / "outside" / ".git").mkdir(parents=True)
    control = MagicMock()
    control.job.create.return_value = "job-1"
    return pyops_job("data", kind="service", control=control)


def _msg(method, path, body=None):
    msg = {"type": method, "path": path, "user": "alice"}
    if body is not None:
        msg.update(data_type="json", data=body)
    return msg


# (method, path, body, task) per set-name-carrying request; "{name}" is
# where the set name goes. /status reads the set's git status directly
# (no task).
REQUESTS = [
    ("GET", "/status/{name}", None, None),
    ("POST", "/create", {"name": "{name}"}, "set_create"),
    ("POST", "/pull/{name}", None, "set_pull"),
    ("POST", "/switch", {"name": "{name}", "branch": "main"}, "set_switch"),
    ("DELETE", "/remove/{name}", None, "set_remove"),
    ("POST", "/update_deps/{name}", None, "set_update_deps"),
    ("POST", "/commit_drafts/{name}", None, "set_commit_drafts"),
    ("POST", "/discard_drafts/{name}", None, "set_discard_drafts"),
]
REQUEST_IDS = [r[1].split("/")[1] for r in REQUESTS]


def _request(spec, name):
    method, path, body, _task = spec
    if body is not None:
        body = {k: (name if v == "{name}" else v) for k, v in body.items()}
    return _msg(method, path.replace("{name}", name), body)


class TestSetNameCheck:
    """The service checks a set name only where it reads a path from it
    itself (/status, 400). A request that starts a job hands the name to
    its set_* task, which refuses an invalid one (see
    test_set_name_check.py)."""

    @pytest.mark.parametrize("name", [".", "..", "../outside", "nested/set", "a.b", "with space"])
    def test_status_of_invalid_name_answered_400(self, service, name):
        from app.lib.git import InvalidSetNameError, validate_set_name
        with pytest.raises(InvalidSetNameError) as check:
            validate_set_name(name)
        assert service["handle_request"](_msg("GET", f"/status/{name}")) == ({"error": str(check.value)}, 400)

    @pytest.mark.parametrize("spec", [r for r in REQUESTS if r[3]], ids=REQUEST_IDS[1:])
    @pytest.mark.parametrize("name", ["myset", "../outside"])
    def test_job_request_enqueues_its_task(self, service, spec, name):
        result, status = service["handle_request"](_request(spec, name))
        assert (result, status) == ({"job": "job-1"}, 200)
        kwargs = service["control"].job.create.call_args.kwargs
        assert (kwargs["set"], kwargs["name"]) == ("pyops", spec[3])
        assert f"name = {name!r}\n" in kwargs["param"]


class TestReadRoutes:

    def test_list_reports_sets(self, service, make_set, pyops_install, hermetic_git):
        """Each set with its branch and whether it has drafts; a directory
        without `.git` is listed as not initialised (so the Sets view can
        remove it). The bundled set, files under the git base (lock files)
        and a directory whose name is no valid set name are not listed."""
        make_set("alpha")
        beta = make_set("beta")
        hermetic_git("checkout", "-b", "feature", cwd=beta)
        (beta / "draft.task.py").write_text("print('draft')\n")
        (pyops_install["git"] / "pyops" / ".git").mkdir(parents=True)
        (pyops_install["git"] / "plain").mkdir()
        (pyops_install["git"] / "alpha.lock").write_text("")
        (pyops_install["git"] / "not a set" / ".git").mkdir(parents=True)
        result, status = service["handle_request"](_msg("GET", "/list"))
        assert (result, status) == ({"sets": [
            {"name": "alpha", "branch": "main", "has_drafts": False, "initialized": True},
            {"name": "beta", "branch": "feature", "has_drafts": True, "initialized": True},
            {"name": "plain", "branch": None, "has_drafts": False, "initialized": False},
        ]}, 200)

    def test_list_reports_unreadable_repository(self, service, pyops_install):
        """A set whose repository git cannot read is still listed, with
        branch "unknown" and no drafts."""
        (pyops_install["git"] / "broken" / ".git").mkdir(parents=True)
        result, status = service["handle_request"](_msg("GET", "/list"))
        assert (result, status) == ({"sets": [
            {"name": "broken", "branch": "unknown", "has_drafts": False, "initialized": True},
        ]}, 200)

    def test_list_without_git_base(self, service, pyops_install):
        pyops_install["git"].rmdir()
        assert service["handle_request"](_msg("GET", "/list")) == ({"sets": []}, 200)

    def test_status_of_set(self, service, make_set):
        target = make_set("myset")
        assert service["handle_request"](_msg("GET", "/status/myset")) == ({"name": "myset", "status": ""}, 200)
        (target / "README.md").write_text("changed\n")
        (target / "new.task.py").write_text("")
        assert service["handle_request"](_msg("GET", "/status/myset")) == (
            {"name": "myset", "status": " M README.md\n?? new.task.py\n"}, 200,
        )

    def test_status_of_unreadable_repository_is_empty(self, service, pyops_install):
        (pyops_install["git"] / "broken" / ".git").mkdir(parents=True)
        assert service["handle_request"](_msg("GET", "/status/broken")) == ({"name": "broken", "status": ""}, 200)

    def test_status_of_uninitialised_set_answered_404(self, service, pyops_install):
        (pyops_install["git"] / "plain").mkdir()
        assert service["handle_request"](_msg("GET", "/status/plain")) == ({"error": "set 'plain' not found"}, 404)


class TestRemove:

    def test_uninitialised_set_enqueues_set_remove(self, service, pyops_install):
        """A set directory without `.git` is removed through the same
        route as any set."""
        (pyops_install["git"] / "plain").mkdir()
        assert service["handle_request"](_msg("DELETE", "/remove/plain")) == ({"job": "job-1"}, 200)
        kwargs = service["control"].job.create.call_args.kwargs
        assert (kwargs["set"], kwargs["name"], kwargs["param"]) == ("pyops", "set_remove", "name = 'plain'\n")


class TestRouting:

    @pytest.mark.parametrize("method, path", [
        ("GET", "/nope"),
        ("POST", "/list"),
        ("GET", "/create"),
        ("GET", ""),
    ])
    def test_unknown_request_answered_404(self, service, method, path):
        assert service["handle_request"](_msg(method, path)) == ({"error": f"Not found: {method} {path}"}, 404)
        service["control"].job.create.assert_not_called()

    def test_non_json_body_reads_as_empty(self, service):
        msg = {"type": "POST", "path": "/create", "user": "alice", "data_type": "form", "data": {"name": "x"}}
        assert service["handle_request"](msg) == ({"error": "name is required"}, 400)


class TestServiceLoop:
    """Run as the job runner runs a service (`__name__ == "__main__"`), the
    service answers each request through its listener until TERM; a
    request whose handler raises is answered 500 and the loop goes on."""

    def test_answers_until_term(self, pyops_job, caplog):
        from unittest.mock import MagicMock
        listener = MagicMock()
        listener.listen.side_effect = [
            _msg("GET", "/nope"),
            _msg("POST", "/clone", {"url": "https://example.com/g/repo.git"}),
            _msg("GET", "/list"),
            "TERM",
        ]
        control = MagicMock()
        control.job.create.side_effect = RuntimeError("enqueue refused")
        pyops_job("data", kind="service", run=True, listener=listener, control=control)
        assert [c.args for c in listener.response.call_args_list] == [
            ({"error": "Not found: GET /nope"}, 404),
            ({"error": "enqueue refused"}, 500),
            ({"sets": []}, 200),
        ]
        [failure] = [r for r in caplog.records if r.message == "handler failed"]
        assert failure.levelname == "ERROR" and failure.exc_info

    def test_shutdown_refusal_relayed_as_503(self, pyops_job, caplog):
        """The pyops API refuses an enqueue with 503 while pyops shuts
        down; `control` raises that refusal with its status, and the
        service answers 503 — a retryable answer, logged as a warning
        without a traceback — instead of 500."""
        from unittest.mock import MagicMock
        from app.lib.pyops import ControlRequestError
        refusal = ControlRequestError("PUT scriptlet/job/item: 503 Service Unavailable", 503)
        listener = MagicMock()
        listener.listen.side_effect = [_msg("POST", "/pull/myset"), "TERM"]
        control = MagicMock()
        control.job.create.side_effect = refusal
        pyops_job("data", kind="service", run=True, listener=listener, control=control)
        listener.response.assert_called_once_with({"error": str(refusal)}, 503)
        [refused] = [r for r in caplog.records if r.message.startswith("request refused")]
        assert refused.levelname == "WARNING" and not refused.exc_info

    def test_other_api_refusal_is_a_fault(self, pyops_job):
        """Any other status the API answers the service's own request
        with is a fault of the service (500)."""
        from unittest.mock import MagicMock
        from app.lib.pyops import ControlRequestError
        listener = MagicMock()
        listener.listen.side_effect = [_msg("POST", "/pull/myset"), "TERM"]
        control = MagicMock()
        control.job.create.side_effect = ControlRequestError("PUT scriptlet/job/item: 404 Not Found", 404)
        pyops_job("data", kind="service", run=True, listener=listener, control=control)
        listener.response.assert_called_once_with({"error": "PUT scriptlet/job/item: 404 Not Found"}, 500)
