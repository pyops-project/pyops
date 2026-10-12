"""Typed errors of the pyops lib layer.

Every condition a client can cause, or that depends on the state of a
resource rather than on a server fault, is raised as a `PyopsError`
subclass at the place it is detected. Each class carries the HTTP status
it answers with (`status_code`); `app/web.py` maps them in one exception
handler, so the routes carry no per-error `except` clauses and a route's
catch-all only ever sees real server faults.

Classes also derive from the builtin exception their condition used to be
reported as (`ValueError`, `FileNotFoundError`, `FileExistsError`,
`RuntimeError`, `TimeoutError`), so code that runs outside the web layer —
the runner, the job processes, the pyops-set scriptlets that receive
`validate_set_name` as a job global — can keep catching the builtin.

No imports beyond the standard library: the job processes import this
module from per-set venvs.
"""


class PyopsError(Exception):
    """Base of every typed pyops error; `status_code` is its HTTP status."""

    status_code = 500


# -- 400: the request names something that cannot be valid --------------------

class InvalidNameError(PyopsError, ValueError):
    """A name outside the identifier charset."""

    status_code = 400


class InvalidSetNameError(InvalidNameError):
    """A set name outside the identifier charset."""


class InvalidScriptletNameError(InvalidNameError):
    """A scriptlet name outside the identifier charset."""


class BundledSetError(PyopsError):
    """A write or git operation on the bundled set, which ships with the
    application and has neither a repository nor editable files."""

    status_code = 400


# -- 404: the named resource does not exist --------------------------------------

class NotFoundError(PyopsError, FileNotFoundError):
    """The named resource does not exist."""

    status_code = 404


class SetNotFoundError(NotFoundError):
    """No set of that name: `<git base>/<set>` is not a directory with `.git`."""


class ScriptletNotFoundError(NotFoundError):
    """No scriptlet of that name in the set."""


class WrongScriptletTypeError(ScriptletNotFoundError):
    """The name exists in the set, but not as the requested type — there is
    no scriptlet of that name and type."""


# -- 409: the request conflicts with the resource's current state -------------------

class ConflictError(PyopsError):
    """The request conflicts with the current state of the resource."""

    status_code = 409


class NameTakenError(ConflictError, FileExistsError):
    """The scriptlet name is already taken in the set."""


class NoCommittedVersionError(ConflictError, FileNotFoundError):
    """The scriptlet has no committed version (it was never committed)."""


class JobStateConflict(ConflictError):
    """The job's current state does not allow the requested transition."""


# -- 503 / 504: the server cannot serve the request right now -------------------------

class ShutdownPending(PyopsError, RuntimeError):
    """The runner has been signalled to shut down; no job may be enqueued."""

    status_code = 503


class ServiceTimeout(PyopsError, TimeoutError):
    """A service job did not answer a proxied request in time."""

    status_code = 504
