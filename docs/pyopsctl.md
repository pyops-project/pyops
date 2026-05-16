# pyopsctl — Operator CLI

Remote-admin CLI for the pyops execution platform. Mirrors what the
VSIX extension does inside an IDE, plus surface VSIX cannot reach:
operator-host commands for health, metrics, version, and a flexible
debug-job invocation that supports detached starts and arbitrary
multi-line parameter scripts.

`pyopsctl` is designed to be driven both by a human operator and by
agents (LLM-driven or otherwise) and shell scripts — non-interactive,
no per-call password prompts.

## Install

Two prerequisites (distribution packages, no pip, no venv):

- `python3` (>= 3.11 for stdlib `tomllib`)
- `python3-requests`

On Debian/Ubuntu:

```
sudo apt install python3 python3-requests
```

Then from this repo:

```
./ctl/install.sh
```

Symlinks `pyopsctl/bin/pyopsctl` into `$XDG_BIN_HOME` (defaults to
`~/.local/bin/pyopsctl`). Idempotent — re-run to refresh the symlink.
Warns when the bin dir isn't on `PATH`.

To remove:

```
./ctl/install.sh --uninstall
```

## Configure

`pyopsctl` reads `~/.config/pyops/config.toml`. The file must be mode
0600 — `pyopsctl` refuses to load otherwise (the file holds API keys).

```toml
default_profile = "aiops"

# Production deployment behind nginx Basic Auth.
[profiles.aiops]
url     = "https://aiops.ralph.or.at"
user    = "ralph"           # Basic Auth username (nginx decodes, injects X-User)
api_key = "..."             # Basic Auth password — a dedicated API key, not your login

# Local dev, no proxy, no credentials. X-User is sent directly.
[profiles.local]
url  = "http://localhost:8000"
# no api_key → no Basic Auth
# user omitted → X-User defaults to "pyopsctl"
```

**Auth pathway is implicit.** If `api_key` resolved for the call,
`pyopsctl` sends `Authorization: Basic base64(user:api_key)` and lets
the proxy inject `X-User`. If `api_key` is absent, `pyopsctl` sends
`X-User: <profile.user or "pyopsctl">` directly — which works against
bare pyops (no proxy) and against fresh deployments before credentials
exist.

**Profile selection.** Order: `--profile NAME` > `PYOPSCTL_PROFILE` env
> `default_profile` from the config file. `--server URL`, `--user U`,
`--api-key STRING`, `--api-key-stdin` override their corresponding
profile values for that one call.

**Sending credentials over plain HTTP** (`http://...` + `api_key`)
prints a stderr warning but does not refuse — local dev may legitimately
route through a loopback proxy.

## Verify

```
pyopsctl config check
pyopsctl version
pyopsctl health
```

`config check` hits `GET /version` and reports reachability + which
auth pathway was used. It's the one-line smoke test.

## Commands

Every command accepts the global flags: `--profile NAME`, `--server URL`,
`--user U`, `--api-key STRING`, `--api-key-stdin`, `--json`, `--quiet`,
`--timeout SECS`.

### `scriptlet` — inventory + draft / save workflow

- `pyopsctl scriptlet list [--set NAME]`
- `pyopsctl scriptlet sets`
- `pyopsctl scriptlet status SET NAME`
- `pyopsctl scriptlet repo-status SET`
- `pyopsctl scriptlet get-code SET NAME`
- `pyopsctl scriptlet create SET NAME --kind {task|service|schedule|controller|view|library|markdown|jslibrary}`
- `pyopsctl scriptlet draft SET NAME [--file PATH | --stdin]`
- `pyopsctl scriptlet save  SET NAME [--file PATH | --stdin]`
- `pyopsctl scriptlet delete SET NAME`
- `pyopsctl scriptlet diff SET NAME [--local-file PATH]`

### `job` — full lifecycle

- `pyopsctl job list [--state S] [--owner U] [--set X] [--name Y]`
- `pyopsctl job view ID [--follow] [--stream stdout|stderr|both]`
- `pyopsctl job run SET NAME [--kind task|service] [--param TEXT | --param-file PATH] [--drop-done] [--max-retries N]`
- `pyopsctl job cancel ID`
- `pyopsctl job delete ID`
- `pyopsctl job download ID [--out PATH]`
- `pyopsctl job cancel-all [--owner U] [--set X]`
- `pyopsctl job clean --state done|failed [--owner U]`

### `debug` — detached by default, arbitrary multi-line param

The marquee feature. VSIX hard-codes `param='#>AWAIT_DEBUG'`. `pyopsctl`:

- defaults to **detached** debug — debugpy listens, the runner does not
  wait for a client;
- takes `--await` to inject the `#>AWAIT_DEBUG` directive on demand;
- takes `--param TEXT` / `--param-file PATH` for an arbitrary parameter
  script. `job.param` is treated as multi-line Python; embedded
  `#`-prefixed directives (e.g. `#!pip somepkg`) survive verbatim.

Merge rule for `--await` + `--param`:

| `--await` | `--param`        | result                                 |
|-----------|------------------|----------------------------------------|
| yes       | (none)           | `"#>AWAIT_DEBUG"`                      |
| yes       | `"x = 1"`        | `"#>AWAIT_DEBUG\nx = 1"`               |
| yes       | `"#!pip foo\n…"` | `"#>AWAIT_DEBUG\n#!pip foo\n…"`        |
| no        | `"x = 1"`        | `"x = 1"` (verbatim)                   |
| no        | (none)           | `""`                                   |

Commands:

- `pyopsctl debug start SET NAME [--await] [--param TEXT | --param-file PATH] [--wait-running] [--overrides-dir DIR]`
- `pyopsctl debug attach JOBID [--wait-running]`
- `pyopsctl debug port`
- `pyopsctl debug stop JOBID`

`--overrides-dir DIR` walks `DIR/<set>/*.py` and submits the contents
as the job's `overrides` dict — same shape as VSIX
`DebugService.buildOverrides`, so in-flight debugging of local edits
works the same way the IDE does it.

`--wait-running` polls until the job reaches `state=running`, then
prints the debug port. Without it, the command returns immediately
after the enqueue.

### `health`, `metrics`, `version`

- `pyopsctl health [--full]` — summary; `--full` for the entire payload
- `pyopsctl health failed` — every failed job in detail
- `pyopsctl metrics` — raw Prometheus exposition
- `pyopsctl version` — server version + CLI version

### `config`

- `pyopsctl config show [--profile NAME]` — prints the resolved profile, `api_key` redacted
- `pyopsctl config check [--profile NAME]` — reachability + auth pathway smoke test

## Output modes

Three modes, controlled by `--json` and `--quiet`:

- **Default (human)** — aligned columns, one-line summary for mutating
  commands.
- **`--json`** — server response payloads pass through verbatim for
  single-call commands; multi-call composites are wrapped as
  `{"data": …, "meta": {...}}`. Errors go to stderr as
  `{"error": "...", "status": N}`.
- **`--quiet` / `-q`** — IDs only on stdout, one per line. Built for
  shell piping: `pyopsctl scriptlet list -q --set foo | xargs -n1 ...`.

Exit codes: `0` ok, `2` usage, `3` config/auth, `4` HTTP 4xx, `5` HTTP
5xx, `6` timeout, `7` server-unreachable.

## Worked examples

### Operator: save a scriptlet from a local edit

```
pyopsctl scriptlet draft myset myscript --file ./myscript.task.py
pyopsctl scriptlet save  myset myscript
```

`draft` writes the working-tree copy without committing; `save` commits
the current draft. Use `diff` first to see what's about to change:

```
pyopsctl scriptlet diff myset myscript --local-file ./myscript.task.py
```

### Agent: enqueue a detached debug job, attach later

```
# 1) Start it detached; debugpy listens but doesn't wait.
JOB=$(pyopsctl debug start myset myscript -q)

# 2) Later, when ready to attach, fetch the port and connect.
pyopsctl debug attach "$JOB" --wait-running --json
# {"data": {"job_id": "...", "port": 45678, "host": "..."}, "meta": {...}}
```

For an attended start that blocks until a client connects, add `--await`:

```
pyopsctl debug start myset myscript --await --param-file ./preamble.py
```

The preamble file can carry its own header directives (`#!pip somepkg`,
`#>OTHER_FLAG`) — they end up on their own lines below
`#>AWAIT_DEBUG\n` and the runner sees both.
