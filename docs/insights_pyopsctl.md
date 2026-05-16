# pyopsctl — Technical Insights

Deep technical reference for the operator CLI. Describes current
state only; release-level notes live in `CHANGELOG.md`, day-to-day
usage in `docs/pyopsctl.md`.

## Layout

```
ctl/                        # outer "tool" dir at the repo root, parallel to app/ and vsix/
  bin/pyopsctl              # Python launcher (#!/usr/bin/env python3)
  install.sh                # symlink-in / symlink-out into $XDG_BIN_HOME
  pyopsctl/                 # Python package — what Python imports as `pyopsctl`
    __init__.py             # __version__ from project-root VERSION
    __main__.py             # argparse dispatch
    config.py               # TOML loading, profile resolution, redaction
    client.py               # requests.Session wrapper, auth header build, HTTPError
    output.py               # human / --json / --quiet formatters, format_table
    commands/
      __init__.py
      scriptlet.py
      job.py
      debug.py
      monitoring.py
      config.py             # `pyopsctl config show / check`
```

The outer dir (`ctl/`) is the CLI distribution; the inner package
(`ctl/pyopsctl/`) is what Python imports as the `pyopsctl` module.
The dir at the repo root is named `ctl/` rather than `pyopsctl/` because
the surrounding project is already `pyops` — re-prefixing the dir would
just repeat that. The binary, the importable module, and the test
directory all stay `pyopsctl` because that's the user-visible name.

Tests under `tests/pyopsctl/` add the outer `ctl/` dir to `sys.path`
in their `conftest.py` so the package is importable without an install.

## Launcher

`bin/pyopsctl` resolves the checkout via `os.path.realpath(__file__)` so
the symlink in `$XDG_BIN_HOME/pyopsctl` works the same as a direct
invocation. It then prepends the outer `ctl/` directory to
`sys.path` and dispatches to `pyopsctl.__main__.main`. This is the
trick that lets `pyopsctl` run **without a venv**: no
`pip install` is required, no PYTHONPATH manipulation by the operator.

## Install / uninstall

`install.sh` is the only install path. By default it:

1. Resolves the user bin dir as `${XDG_BIN_HOME:-$HOME/.local/bin}`.
2. `mkdir -p` the bin dir.
3. `ln -sfn ctl/bin/pyopsctl <bin-dir>/pyopsctl` — symlink, not a
   copy, so updates to the checkout propagate without a reinstall.
4. Warns to stderr if the bin dir isn't on `PATH`.

`./install.sh --uninstall` removes the symlink only. It refuses to
remove non-symlink files at the target path (defensive — a hand-rolled
copy at `~/.local/bin/pyopsctl` would belong to the operator, not to
this script).

`install.sh` does not touch apt. Distribution prerequisites
(`python3 >= 3.11` for stdlib `tomllib`; `python3-requests` for the
HTTP client) are documented in `ctl/README.md` and are the
operator's responsibility.

## CLI framework

Stdlib `argparse`. No Click, no Typer, no plugins. Pattern:

- `__main__._build_parent_flags()` builds an `ArgumentParser` with
  `add_help=False` carrying every global flag (`--profile`,
  `--server`, `--user`, `--api-key`, `--api-key-stdin`, `--json`,
  `--quiet`, `--timeout`).
- Each command module exposes `register(subparsers, parents)`. The
  `parents` list is passed to `parents=[…]` on every subparser so
  global flags appear consistently on every command.
- Each leaf subparser sets `defaults(func=…)`. Dispatch is just
  `args.func(args)`.

Errors fall through to `__main__.main` which maps:

- `ConfigError` → exit 3
- `HTTPError(status=4xx)` → exit 4
- `HTTPError(status=5xx)` → exit 5
- `HTTPError(status=0)` from `requests.Timeout` → handled by the same
  4xx/5xx mapping (status 0 falls into the 5xx branch as a degenerate
  "server problem")
- argparse's own validation → exit 2
- `KeyboardInterrupt` → exit 130

## Config loading

`config.load(args)` resolves the active `Profile` from a precedence
chain:

1. `--profile NAME` flag
2. `PYOPSCTL_PROFILE` env var
3. `default_profile` key in the config file

The config file path is `~/.config/pyops/config.toml` by default,
overridable via `PYOPSCTL_CONFIG` env var (used by tests).

**Mode enforcement.** `config.py` `_ensure_mode_0600` rejects files
with any group/other bits set. The file holds API keys; permissive
modes get a `ConfigError` with the corrective `chmod 600` command in
the message.

**Per-call overrides.** `--server URL`, `--user U`, `--api-key STRING`,
and `--api-key-stdin` override the corresponding profile values for a
single call. `--api-key-stdin` reads one line from stdin and strips
the trailing newline.

**HTTP + api_key warning.** Sending Basic-Auth credentials over plain
HTTP gets a stderr warning but does not refuse the call (loopback
proxies in dev legitimately use `http://localhost:...`).

`config.redact(profile)` returns a dict with `api_key` masked as
`"***"` for `pyopsctl config show`.

## Auth pathways

Two pathways, selected implicitly by whether `api_key` resolved for
the call:

1. **`api_key` present.** `Client.__init__` sets
   `Authorization: Basic base64(user:api_key)` on the `requests.Session`.
   No `X-User` header is sent — the deployment is expected to be
   behind an nginx reverse proxy that decodes the Basic Auth and
   injects `X-User: $remote_user` into the upstream request. Matches
   the README compose example and `vsix/src/apiClient.ts`.
2. **`api_key` absent.** `Authorization` is not set. `X-User` is sent
   directly, falling back to `"pyopsctl"` when `profile.user` is also
   empty. This works against bare pyops (no proxy), against fresh
   deployments before credentials exist, and against test fixtures.

The auth pathway is per-call, not per-profile config: an operator
can run `pyopsctl --server http://localhost:8000 health` with the
api_key-bearing default profile and `--user` overridden, and the
client will still send Basic Auth — pathway is determined by the
**resolved** values, not by what's in the file.

## HTTP client

`client.Client` is a thin wrapper over `requests.Session`:

- One session per CLI invocation, used as a context manager so the
  connection pool is closed cleanly on exit. Multi-call commands
  (e.g. `job view --follow`, `debug start --wait-running`) re-use
  the same TCP connection.
- All requests carry the resolved auth headers.
- `request()` normalizes responses: 2xx → returns the underlying
  `Response`; non-2xx → raises `HTTPError(status, message, body)`.
  Timeout and connection errors raise `HTTPError(status=0, …)` so
  callers have a single exception type to handle.
- `get_bytes()` and `get_text()` for binary payloads (job workspace
  zips) and `text/plain` payloads (Prometheus metrics) respectively;
  `get/post/put/delete` return JSON-decoded payloads.
- `quote_path_segment()` URL-encodes a single segment for paths like
  `/scriptlet/sets/remove/{name}`.

## Output formatting (`output.py`)

Three modes, mutually exclusive in their effect on stdout but
independent flags:

- **Default (human).** Mutating commands print one human line
  (`debug job <id> enqueued (set::name, detached)`). List commands
  use `format_table()` (stdlib-only column alignment). Composite
  commands use a multi-line layout (`job view`).
- **`--json`.** Single-call commands pass the server response through
  verbatim — clients can rely on the same shape the API documents.
  Multi-call composites (e.g. `debug start --wait-running` which hits
  three endpoints) use `emit_composite` to wrap as
  `{"data": …, "meta": {server, profile, endpoints}}`. Errors go to
  stderr as `{"error": "...", "status": int}`.
- **`--quiet` / `-q`.** Prints only the `id_field` value from the
  response, one per line for list payloads. Designed for shell
  pipelines:
  ```
  pyopsctl scriptlet list -q --set foo | xargs -n1 pyopsctl scriptlet diff foo
  ```

`emit_bytes(args, data, out_path=PATH or None)` is the binary blob
sink: `--out PATH` writes to file; absence writes to stdout
(`sys.stdout.buffer`).

## Debug surface — the merge

`pyopsctl debug start` is the load-bearing feature pyopsctl ships that
VSIX cannot reach.

VSIX hard-codes `param='#>AWAIT_DEBUG'` and offers no detached debug
flow. `pyopsctl` defaults to **detached** (empty `param`, debugpy
listens but doesn't wait) and adds two flag families that compose:

- `--await` injects the `#>AWAIT_DEBUG` directive into `param`. The
  runner (`app/lib/runner.py:enqueue` + the job-runtime debug bootstrap)
  treats `"#>AWAIT_DEBUG" in job.param` as the trigger for
  `debugpy.wait_for_client()`.
- `--param TEXT` / `--param-file PATH` (mutually exclusive) supply an
  arbitrary multi-line parameter. `job.param` is treated as a Python
  script that can carry other `#`-prefixed directives (e.g.
  `#!pip somepkg`, `#>OTHER_FLAG`).

`commands/debug._merge_debug_param(*, await_flag, user_param)` is the
single source of truth for how the two combine:

| `await_flag` | `user_param`            | result                              |
|--------------|-------------------------|-------------------------------------|
| `True`       | `None` or `""`          | `"#>AWAIT_DEBUG"`                   |
| `True`       | `"x = 1"`               | `"#>AWAIT_DEBUG\nx = 1"`            |
| `True`       | `"#!pip foo\nx = 1\n"`  | `"#>AWAIT_DEBUG\n#!pip foo\nx = 1\n"` |
| `False`      | `"x = 1"`               | `"x = 1"` (verbatim, multi-line OK) |
| `False`      | `None` or `""`          | `""`                                |

The **newline separator** is load-bearing: the user's body may itself
begin with a `#`-prefixed directive line, and splicing the
`#>AWAIT_DEBUG` token into the same line would either invalidate the
user's directive or hide it from the runner's substring check. With
the newline, every directive sits on its own line, the runner's `in`
check finds `#>AWAIT_DEBUG` regardless, and the user's preamble is
still a valid Python script.

`_build_overrides(overrides_dir, set_name)` walks
`overrides_dir/<set>/*.py` and returns a `dict[str, str]` keyed by
`"<set>::<name>"` — the same shape the server enforces via
`^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$` in
`app/routers/scriptlet/job.py`. Returns `None` (not `{}`) when no
override dir is given, so the JSON body omits the `overrides` key
entirely and the server reads from the per-set git working tree.

`_poll_until_running(c, job_id, timeout=30.0, interval=0.5)` is the
shared poll loop for `--wait-running` and `debug attach`. Bails with
a clear error on terminal-without-running so the operator doesn't
print a port for a job that already failed.

`_host_of(url)` strips the scheme + port off the profile URL for the
"attach to host:port" human line — VS Code's `attach` debug config
needs host and port as separate fields.

## Endpoint coverage

The 3.1.0 scope hits these pyops endpoints:

**scriptlet** — `GET /scriptlet/inventory/list`,
`GET /scriptlet/inventory/sets`, `GET /scriptlet/inventory/status`,
`GET /repo/status`, `GET /scriptlet/inventory/code`,
`PUT /scriptlet/inventory/item`, `POST /scriptlet/inventory/item`,
`POST /scriptlet/inventory/draft`, `DELETE /scriptlet/inventory/item`.

**job** — `GET /scriptlet/job/list`, `GET /scriptlet/job/item`,
`PUT /scriptlet/job/item`, `DELETE /scriptlet/job/item`,
`GET /scriptlet/job/cancel`, `GET /scriptlet/job/download`.

**debug** — `PUT /scriptlet/job/item` (with `kind=debug`),
`GET /debug/port`, plus the job list/item/cancel endpoints above.

**monitoring** — `GET /health`, `GET /health/failed`,
`GET /metrics`, `GET /version`.

Not yet covered (additive scope for 3.1.x or later): sets management
(`/scriptlet/sets/*`), schedule CRUD (`/scriptlet/schedule/*`),
controller execution (`/controller/{set}/{name}/...`).

## Versioning

`ctl/pyopsctl/__init__.py` reads the project-root `VERSION` file
at import time. The CLI's version string is identical to the server's
`/version` response when run from a fresh checkout, which is why
`pyopsctl version` reports both side by side: drift between them
means the operator's CLI is talking to a different release.

`vsix/build.sh:17` already syncs `vsix/package.json` from the same
`VERSION` file, so all three artifacts (Python app, VSIX, CLI) stay
locked to one source of truth.

## Test layout

`tests/pyopsctl/` splits along the kind-of-test axis: **unit** for
pure-function and argparse-wiring checks (no I/O), **integration** for
"does pyopsctl ↔ pyops API match the documented contract" against a
real pyops server, and **e2e** for *system interplay* — pyopsctl,
pyops, and a third actor (a debugger TCP client today; could be VSIX
or a browser as the suite grows) cooperating on the same state.

```
conftest.py                # sys.path + shared fixtures inherited by both subdirs:
                           #   pyopsctl_env, pyops_server_for_ctl, pyops_set,
                           #   pyopsctl_bin, run_ctl (callable fixture)
unit/
  test_config.py
  test_client.py
  test_output.py
  test_debug_param_merge.py    # the merge truth-table — VSIX can't do this
  test_commands_debug.py       # argparse wiring → request body shape
integration/
  test_smoke.py                # version/config check/health/metrics
  test_debug_flow.py           # merge semantics against a real server
e2e/
  test_debug_client_attach.py  # pyopsctl + pyops + debugger client interplay
```

The shared server fixture starts a fresh pyops in a new process group
(mirrors `tests/e2e/conftest.py`) on port `18322` so it can run
alongside the main e2e suite without colliding. The fixture also
writes a TOML config into a tmp `HOME` so the resolved profile is
reproducible and isolated per test.

**`integration/test_debug_flow.py`** asserts the merge semantics:
- creates a scriptlet via the CLI,
- runs `debug start` with each flag combo,
- pulls the job back via `job list --json`,
- verifies the resulting `job.param` against `_merge_debug_param`'s
  contract — including the embedded `#`-directive case where the
  user's preamble starts with `#!pip somepkg` and must survive
  verbatim below `#>AWAIT_DEBUG\n`.

**`e2e/test_debug_client_attach.py`** verifies the cross-system
contract: pyopsctl says `--await --wait-running` → port; the runner
side actually has `debugpy.listen()` open on that port; a raw TCP
client can connect; `debug stop` tears the listener back down so a
follow-up debug job can re-bind the same port. Without these tests
the merge could be perfect on the wire and still fail in the field
because the runner never opened the listener.

## CI

The `test:pyopsctl` stage in `.gitlab-ci.yml` runs after `test:`,
installs distribution `python3-requests` (matching the operator's
environment — no pip-installed `requests` for the CLI), and runs
`pytest tests/pyopsctl -v`. The other test stages (`test:`,
`test:e2e`, `test:vsix*`, `test:image_build:`) are untouched.

## Operator vs agent flows

The CLI is engineered for both. Two patterns dominate the design:

1. **Non-interactive** for agents (LLM-driven or otherwise) and
   scripts. No password prompts; credentials come from the config
   file or env. `--quiet` emits IDs only on stdout so
   `pyopsctl debug start … -q` is composable with `xargs`, `read`,
   `jq`.
2. **Human-readable by default** for the operator. The same commands
   without `--json`/`--quiet` print aligned tables and one-line
   summaries.

The same binary serves both. The flags are the only difference.
