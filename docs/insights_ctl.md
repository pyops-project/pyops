# pyopsctl — Technical Insights

Deep technical reference for the operator CLI. Describes current
state only; release-level notes live in `CHANGELOG.md`, day-to-day
usage in [`docs/pyops_guide.md`](pyops_guide.md) § pyopsctl.

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
    ports.py                # per-profile debug-port lease store
    debug_format.py         # human-readable rendering for `debug *` results
    pdb/
      client.py             # TCP pdb-wire client (PdbClient + parsers)
    session/
      paths.py              # state-dir layout per debug session
      spawn.py              # fork+pipe handshake for the session daemon
      protocol.py           # control-socket envelope shapes
      client.py             # CLI ↔ daemon RPC over the control socket
      daemon.py             # the session daemon: holds pdb TCP, dispatches ops
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

## Debug surface — composing the pdb bootstrap

`pyopsctl debug start` is the load-bearing CLI verb. Since the
server-side debugger-injection refactor, pyops does not start a
debugger by itself — the client writes the bootstrap into the job's
`param`. pyopsctl uses **remote-pdb** (a 50-line pip package wrapping
stdlib pdb for TCP) so the wire is `(Pdb) `-prompt text and the
client-side parser is small.

The composed `param` shape:

```python
#!pip remote-pdb
from remote_pdb import RemotePdb
RemotePdb('0.0.0.0', <leased port>).set_trace()
<optional user --param body, appended after a blank line>
```

The leased port comes from the profile's `debug_ports` pool (see
"Port leasing" below). `--param TEXT` / `--param-file PATH` (mutually
exclusive) supply an optional preamble that runs **after** pdb is
attached and paused at the bootstrap's `set_trace()` — i.e. the
operator sees it only after issuing `continue`.

`_build_overrides(overrides_dir, set_name)` walks
`overrides_dir/<set>/*.py` and returns a `dict[str, str]` keyed by
`"<set>::<name>"` — the same shape the server enforces via
`^[a-zA-Z0-9_-]+::[a-zA-Z0-9_-]+$` in
`app/routers/scriptlet/job.py`. Returns `None` (not `{}`) when no
override dir is given, so the JSON body omits the `overrides` key
entirely and the server reads from the per-set git working tree.

`_poll_until_running(c, job_id, timeout=60.0, interval=0.5)` is the
shared poll loop for `--wait-running` and `debug attach`. Under the
new server semantics, `running` means `exec(param)` has returned —
i.e. `set_trace()` is up and blocking the program waiting for our
TCP connection. That's exactly the moment we want to attach.

`_host_of(url)` strips the scheme + port off the profile URL for the
attach host. `--debug-host` overrides when pyops sits behind a
port-forward whose external IP differs from the API URL's host.

## Port leasing

Per-profile pool, configured in `~/.config/pyops/config.toml`:

```toml
[profiles.local]
url = "https://pyops.example/"
debug_ports = [45679, 45680, 45681]
```

Lease store: `$XDG_RUNTIME_DIR/pyops/debug-ports/<profile>/<port>.lease`
(operator-only, mode 0600). Contents = the job_id that currently holds
the port. Concurrent leasers race on `O_CREAT|O_EXCL`; exactly one
wins per port.

Stale-lease reclaim: `ctl/pyopsctl/ports.py:lease()` accepts an
`is_stale(job_id)` callback, which `commands/debug._start` wires to
`GET /scriptlet/job/item?id=<jid>` — terminal states (done, failed,
cancelled, removed, or 404) free the port for the new lease.

`PYOPS_DEBUG_PORTS_DIR` (env) overrides the lease root, used by tests
to isolate per-tmp_path stores.

`debug stop` calls `ports.release_for_job(profile, job_id)` so the
pool reclaims the port immediately rather than waiting for stale
sweep on the next lease.

## Endpoint coverage

The 3.1.0 scope hits these pyops endpoints:

**scriptlet** — `GET /scriptlet/inventory/list`,
`GET /scriptlet/inventory/sets`, `GET /scriptlet/inventory/status`,
`GET /repo/status`, `GET /scriptlet/inventory/code`,
`PUT /scriptlet/inventory/item` (create, working-tree only),
`POST /scriptlet/inventory/draft` (draft write, no commit),
`DELETE /scriptlet/inventory/item` (working-tree only). Commit + push
is exclusively the internal `set_commit_drafts` task — invoked via the
Sets view's "Commit drafts" button or by enqueuing the task directly.

**job** — `GET /scriptlet/job/list`, `GET /scriptlet/job/item`,
`PUT /scriptlet/job/item`, `DELETE /scriptlet/job/item`,
`GET /scriptlet/job/cancel`, `GET /scriptlet/job/download`.

**debug** — `PUT /scriptlet/job/item` (with `kind=debug`), plus the
job list/item/cancel endpoints above. The debug session runs
client-side against the remote-pdb listener the param's bootstrap
opened; no server endpoint mediates the debugger wire.

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
  test_config.py               # profile resolution + debug_ports validation
  test_client.py
  test_output.py
  test_session_paths.py        # session state dir layout + stale-pid pruning
  test_ports.py                # port-lease store: pool, stale reclaim
  test_pdb_client.py           # PdbClient prompt reader + response parsers
  test_human_output.py         # vertical state-dump renderer
  test_commands_debug.py       # argparse wiring → request body shape
integration/
  test_smoke.py                # version/config check/health/metrics
  test_debug_flow.py           # composed-param shape against a real server
```

The shared server fixture starts a fresh pyops in a new process group
(mirrors `tests/e2e/conftest.py`) on port `18322` so it can run
alongside the main e2e suite without colliding. The fixture also
writes a TOML config into a tmp `HOME`, isolates the port-lease store
under a per-test `PYOPS_DEBUG_PORTS_DIR`, and isolates the session
state dir under `PYOPS_SESSION_DIR`.

There is no e2e directory currently: an end-to-end test that drives
the full attach + step + locals + evaluate cycle requires a job
actually pausing at remote-pdb's `set_trace()` — feasible to add but
not in scope for the pdb-rewrite PR. The unit tests cover the pdb
wire client against a stub TCP server (response-shape parsing) and
the integration tests cover the param-composition contract against a
real pyops.

## CI

The `test:pyopsctl` stage in `.gitlab-ci.yml` runs after `test:`,
installs distribution `python3-requests` (matching the operator's
environment — no pip-installed `requests` for the CLI), and runs
`pytest tests/pyopsctl -v`. The other test stages (`test:`,
`test:e2e`, `test:vsix*`, `test:image_build:`) are untouched.

## Interactive debug — session daemon

`pyopsctl debug attach JOB_ID` spawns a long-lived **session daemon**
that holds the TCP connection to remote-pdb's listener (opened by the
job's bootstrap `param`). Subsequent `debug` verbs (`state`, `stack`,
`locals`, `globals`, `evaluate`, `break set/list/remove`, `step`,
`step-in`, `step-out`, `continue`, `source`, `detach`, `terminate`,
`session info`) talk to the daemon over a per-job Unix domain socket.
Each verb is one round-trip: short-lived CLI → daemon sends a pdb
command → reads until the next `(Pdb) ` prompt → returns the parsed
response → CLI exits.

### Daemon spawn and state dir

`attach` forks once, then the child runs `daemon_main`. The child
`setsid`s, redirects fds 0/1/2 to `/dev/null` + `daemon.log`, opens
TCP to the remote-pdb listener (this releases the program from its
`set_trace()` accept), reads pdb's opening banner + first prompt,
binds its control socket, and signals "ready\n" back to the parent
on a pipe.

State directory layout, resolved via `$PYOPS_SESSION_DIR` (tests) >
`$XDG_RUNTIME_DIR/pyops/sessions/` > `~/.cache/pyops/sessions/`:

```
<root>/<job_id>/
  pid          # daemon PID
  sock         # AF_UNIX control socket, mode 0600
  meta.json    # {job_id, server_url, profile, pdb_host, pdb_port, set, name, attached_at}
  daemon.log   # daemon stdout/stderr (preserved on failure for postmortem)
```

`AF_UNIX` paths cap at ~108 bytes on Linux; the daemon and CLI both
`chdir` into the session dir and bind/connect using the relative
name `sock` to keep the sockaddr short under deep test tmp_path
hierarchies.

### Control protocol

Newline-delimited JSON envelopes:

```
{"op": "<verb>", "args": {...}}  →
{"ok": true,  "result": {...}}                     # success
{"ok": false, "error": "...", "code": "<sym>"}     # failure
```

Error codes: `BAD_REQUEST`, `NO_SESSION`, `AMBIGUOUS_SESSION`,
`PDB_ERROR`, `NO_STOP`, `UNKNOWN_REF`, `TIMEOUT`. Ops are serialised
per session (the daemon holds one lock for the entire `_lock` block
per request) so the pdb wire stays well-framed.

### Pdb wire — prompt-based read loop

`PdbClient` (`ctl/pyopsctl/pdb/client.py`) is the wire layer. The
protocol is trivial: pdb writes its output, then emits `(Pdb) ` to
signal "ready for the next command". Read-until-prompt = read until
that 6-byte substring appears. Send a command + newline; the next
read returns everything pdb wrote in response.

`*** <ErrorClass>: <message>` lines are pdb's non-fatal error output
(e.g. `*** NameError: name 'undef' is not defined`). The connection
stays at a prompt — `PdbClient` raises `PdbError` for the caller
but the session is immediately usable for the next command. Op
handlers in `session/daemon.py` catch `PdbError` and translate to
`PDB_ERROR` envelopes.

### Source-path resolution

`break set <set>::<name>:<line>` translates to `b <abs_path>:<line>`.
The absolute path is derived from the materialised source layout
the server already maintains: `data/jobs/<set>/<job_id>/.debug/src/
<set>/{task,library}/<name>.py`. The daemon learns the workspace
root from the most recent pdb stop's `> /abs/path(line)func()` cursor
(every stop carries the `.debug/src/...` prefix); for the first
`break set` before any stop, it issues `w` (where) to pull a frame.

The reverse mapping (path → `<set>::<name>`) is one regex against the
`/.debug/src/<set>/<kind>/<name>.py` tail, used to surface friendly
names in the breakpoint list and the state-dump renderer.

### Why attach lands at the bootstrap's set_trace()

Unlike the previous DAP setup, pdb has no race here. The job's
bootstrap calls `RemotePdb(host, port).set_trace()` which:

  1. Opens a TCP listener on `host:port`.
  2. accept()s exactly one connection. The program is suspended in
     `accept()` — no Python instructions execute past `set_trace()`
     until the client connects.
  3. Once connected, pdb's prompt is the first thing the client
     reads. The state is "stopped at the line just after `set_trace()`".

So `debug attach` lands at a known, useful stop every time:
immediately after the bootstrap, before the user's `--param` body
runs (which is appended below the bootstrap and only executes once
the operator issues `continue`).

### What's not supported in v1

- **Pause-mid-run.** Once `continue` runs and execution leaves the
  prompt, there's no way to interrupt it without server-side signal
  forwarding (the job process lives in pyops's container, pyopsctl
  on the operator's host). Operator sets breakpoints upfront.
- **Re-attach after detach.** `set_trace()` accepts one connection; on
  EOF, the program proceeds. The bootstrap could install a SIGUSR1
  handler that re-arms `set_trace()`, but that needs the same
  server-side signal channel as pause. Operator runs `debug start`
  again to get a fresh session.
- **`var` (variablesReference drill-down).** Pdb has no equivalent;
  the operator drills with `evaluate 'obj.attr'` instead.
- **`threads`.** Pdb is single-threaded by design (it traces the
  thread that called `set_trace`); for multi-thread Python jobs the
  operator picks one thread to debug in the bootstrap.

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
