# PyOps User Guide

## Introduction

PyOps is an execution platform for Python operations scriptlets, licensed under GNU AGPLv3. It allows set developers to build reusable operations: scriptlets for tasks/services, controllers for UI routing, views for HTML fragments. Code is stored in remote sets (logical groups, e.g., "myops"). Use the PyOps VSIX extension in VSCode for tree views, commands, and local editing mirrors (pyops/ dir). Integrate Cline (or similar LLM extension) for one-shot code generation into files based on prompts.

### VSIX Installation and Configuration

- **Install**: In VSCode, go to Extensions view > ... > Install from VSIX > select `vsix/pyops.vsix`.
- **Configure** workspace settings (Ctrl+, > Workspace tab, or `Preferences: Open Workspace Settings (JSON)`):
  ```json
  {
    "pyops.serverUrl": "http://localhost:8000",
    "pyops.localPyopsDir": "pyops",
    "pyops.autoRefreshInterval": 10,
    "pyops.username": "anonymous",  // Optional for Basic Auth (X-User header; password prompted & stored securely in workspace state)
    "pyops.excludedSets": []        // Sets to hide from the manager tree view
  }
  ```
- **Auth**: Set `pyops.username`; extension prompts for password on first API call (stored securely in workspace state).
- **Status**: Status bar shows connection (requires valid `pyops.serverUrl`).

VSIX provides pyops-container in sidebar/activitybar:
- **pyops-manager**: Sets > type folders (task/service/library/controller/view/markdown/jslibrary) > scriptlets; icons: ✓ synced, ⚠ unsynced/draft/local-only; contextValues pyopsItem-{type}(-local).
  Right-click: Reset Local from Remote, Write Local to Remote Draft, Diff Local Remote, Delete Scriptlet; Start/Stop Debug Job (tasks). Per-file commit/push is gone — commit the whole set's draft changeset at once via the Sets view.
  Titlebar: Refresh Manager, Create Scriptlet, Attach to Debug Job.
- **pyops-monitor**: Jobs by state; right-click: Copy Job Param, Cancel/Download/Delete/View Job.
  Titlebar: Refresh Monitor, Cancel All Jobs, Delete All Done/Canceled, Delete All Failed/Killed.
- Sets Manager: Open http://{pyops.serverUrl}/controller/internal/sets for clone/switch/remove.

Local `pyops/` mirrors remote files for editing (create opens file; upload/save syncs changes). Local-only scriptlets (created locally without remote counterpart) can be synced to remote via "Write Local to Remote Draft", which auto-creates the remote scriptlet if it doesn't exist.

### Coding with Cline

Use Cline in VSCode to generate code into existing scriptlet files via prompts. Open file in VSIX (e.g., via create command), then prompt Cline with context + instructions for one-shot replacement.

Examples:
- Task: "Generate a task scriptlet to fetch data from https://api.example.com using requests, process JSON, write result to job output file, and log completion. Use directives for deps. Ensure error handling."
- Service: "In this service file, add a handler for POST requests with JSON data: validate payload, call external API via control, return {'status': 'ok', 'value': result}. Include setup loop with listener."
- Controller: "Update this controller to route to 'myservice', use views 'head', 'menu', 'body', set debug=False, name='My UI'."
- View: "Enhance body.html with Bootstrap table for job list, AJAX GET to /jobs via apiCall, populate rows with Material Icons."
- Library: "Generate a Python library scriptlet with shared functions, e.g., data processing utilities, for import in other scriptlets."
- JS Library: "Create a JavaScript library scriptlet with utility functions, e.g., `function apiCall(type, path, params, callback) { /* REST AJAX implementation */ }` for use in views."
- Markdown: "Write a markdown scriptlet for documentation or README content."

Cline outputs full file content; paste/replace in editor, then save via VSIX. Iterate prompts for refinements (e.g., "Add validation to previous code").

## VSIX Workflow

- **Create Scriptlet**: pyops-manager titlebar > Create Scriptlet (prompts for set/type/name); opens local `pyops/{set}/{type}/{name}.{ext}` (.py for task/service/library/controller, .html for view, .md for markdown, .js for jslibrary).
- **Edit/Generate**: Use VSCode/Cline to code. Edit local file.
- **Sync**: Right-click > Reset Local from Remote (download draft), Write Local to Remote Draft (upload — unstaged). To commit + push, open the Sets view and click "Commit drafts" on the set; a modal previews the porcelain status and accepts an optional commit message.
- **Diff**: Right-click > Diff Local Remote.
- **Debug**: Right-click task(-local) > Start/Stop Debug Job.
- **Delete**: Right-click > Delete Scriptlet (remote; local-only ok).
- **Enqueue/Monitor**: pyops-monitor right-click jobs; titlebar bulk ops.
- No auto-sync: Manual control.

Manager view (`/scriptlet/manager.html`) also carries a per-task
**Run Draft** action (`play_arrow` icon, green) that enqueues a
`kind=draft` job against the working-tree version of the scriptlet
with no `param`. Skips the editor entirely; view output in the Jobs
view or `pyopsctl job view <id>`.

## Advanced Debugging

**PyOps Server**:
- Runner kind="debug": no debugger injection. The client's `param` carries the bootstrap (e.g. `import debugpy; debugpy.listen(("0.0.0.0", port)); debugpy.wait_for_client(); debugpy.breakpoint()`). The runner flips `running` after `exec(param)` returns and otherwise runs the job exactly like task/service/draft — minus retries and execution timeout.
- Materialises scriptlet sources under `.debug/src/<set>/{task,library}/<name>.py` so any source-path-mapping debugger maps `{set}::{name}` ↔ file without per-debugger plumbing.
- Parses `overrides` JSON: uses local code for `{set}::{name}` keys (main/libs).
- Kills previous owner debug job on enqueue.

**VSIX**:
- Right-click task(-local) > Start Debug Job: recursive pyops/set/*.py → overrides, composes a debugpy bootstrap into the job's `param` (`#!pip debugpy` + `debugpy.listen(..., in_process_debug_adapter=True)` + `wait_for_client()` + `breakpoint()`), enqueues with a port leased from `pyops.debugPorts`, then attaches VSCode's debugger.
- pyops-manager titlebar > Attach to Debug Job: lists kind="debug" running/dequeued (1 only), vscode.debug attach localhost:{port} justMyCode=false.
- Local-only tasks: full debug cycle (overrides skip remote check).

Controllers: `debug=True` uses unstaged/draft code (preview).

## Full Workflow Example

1. **Clone Set**: Browser to http://localhost:8000/controller/internal/sets > Add (name="myops", URL=git@github.com/user/myops.git, branch=main).
2. **Create Components**:
   - pyops-manager titlebar > Create Scriptlet (set="myops", type="service", name="myservice"); opens pyops/myops/service/myservice.py; use Cline to generate code.
   - Create controller/mycontroller.py (globals: set="myops" service="myservice" ...).
   - Create view/my_head.html, my_menu.html, my_body.html (Bootstrap + apiCall).
3. **Edit/Generate**: Cline prompts for code/logic refinements.
4. **Sync**: Right-click > Write Local to Remote Draft for each edited file; then in the Sets view click "Commit drafts" on the set to batch-commit + push.
5. **Enqueue Service**: Enqueue via web UI (/scriptlet/manager.html POST kind="service", with optional `max_retries` to auto-restart on failure up to N times) or API /scriptlet/job/item.
6. **Access UI**: http://localhost:8000/controller/myops/mycontroller.
7. **Debug**: Right-click task > Start Debug Job; titlebar Attach to Debug Job for VSCode debugger.
8. **Monitor/Schedule**: pyops-monitor bulk ops; /scriptlet/schedule (create schedules with optional `max_retries` to auto-restart on failure).

## pyopsctl — operator / agent CLI

Remote-admin CLI for pyops. Drives pyops from a terminal or a script
without VSCode — same actions VSIX's manager/monitor surface, plus
interactive pdb-based debugging without an IDE. Designed to be driven
both by a human operator and by agents (LLM-driven or otherwise) and
shell scripts: non-interactive, no per-call password prompts. Technical
deep-dive: [`docs/insights_ctl.md`](insights_ctl.md).

### Install

Two prerequisites (distribution packages, no pip, no venv):

- `python3` (>= 3.11 for stdlib `tomllib`)
- `python3-requests`

Optional:

- `python3-argcomplete` — enables zsh tab completion. `install.sh`
  detects it; without it the install step prints a one-line hint and
  skips the completion wiring (the CLI still works unchanged).

On Debian/Ubuntu:

```
sudo apt install python3 python3-requests python3-argcomplete
```

Then from the repo:

```
./ctl/install.sh
```

Symlinks `ctl/bin/pyopsctl` into `$XDG_BIN_HOME` (defaults to
`~/.local/bin/pyopsctl`). Idempotent — re-run to refresh the symlink.
Warns when the bin dir isn't on `PATH`. Remove with `./ctl/install.sh
--uninstall`.

When `python3-argcomplete` is present, `install.sh` also drops
`~/.local/share/zsh/site-functions/_pyopsctl` (the autoload file
zsh's `compinit` picks up) and appends a marker-tagged `fpath=(…)`
entry to `~/.zshrc` so the user-share directory is on `$fpath`. Both
are idempotent; `--uninstall` removes both. Tab completion is live
on the next interactive zsh (`exec zsh` or new terminal).

### Install via .deb (Ubuntu 24.04+ / Debian 13+)

A Debian binary package ships with every release as a Generic Package
on the GitLab project (`pyops-pyopsctl/<version>/`). Download the
`pyopsctl_<version>_all.deb` from the release assets, then:

```
sudo apt install ./pyopsctl_<version>_all.deb
```

apt resolves `python3-requests` and recommends `python3-argcomplete`
automatically. The deb installs to apt-managed system paths
(`/usr/bin/pyopsctl`, `/usr/lib/python3/dist-packages/pyopsctl/`) and
drops zsh + bash completion files at the system-wide paths zsh's
`compinit` and `bash-completion` autoload from — so tab completion
works out of the box with no rc edit. Use the deb path on operator
boxes that don't have the pyops checkout cloned. To build the deb
locally (rare; CI does this for releases): `./ctl/build.sh` (needs
`dpkg-dev` + `python3-argcomplete`).

### Configure

`pyopsctl` reads `~/.config/pyops/config.toml`. The file must be mode
0600 — `pyopsctl` refuses to load otherwise (it holds API keys).

```toml
default_profile = "aiops"

# Production deployment behind nginx Basic Auth.
[profiles.aiops]
url          = "https://aiops.ralph.or.at"
user         = "ralph"            # Basic Auth username (nginx decodes, injects X-User)
api_key      = "..."              # Basic Auth password — a dedicated API key, not your login
debug_ports  = [55678, 55679, 55680]

# Local dev, no proxy, no credentials. X-User is sent directly.
[profiles.local]
url          = "http://localhost:8000"
debug_ports  = [5678, 5679]
# no api_key → no Basic Auth
# user omitted → X-User defaults to "pyopsctl"
```

**Auth pathway is implicit.** If `api_key` is resolved for the call,
`pyopsctl` sends `Authorization: Basic base64(user:api_key)` and lets
the proxy inject `X-User`. If `api_key` is absent, `pyopsctl` sends
`X-User: <profile.user or "pyopsctl">` directly — which works against
bare pyops (no proxy) and against fresh deployments before credentials
exist.

**Profile selection.** Order: `--profile NAME` > `PYOPSCTL_PROFILE` env
> `default_profile` from the config file. `--server URL`, `--user U`,
`--api-key STRING`, `--api-key-stdin` override the corresponding
profile values for that one call.

**`debug_ports`** is the per-profile pool used by `debug start` to
lease one TCP port per concurrent debug session. Size it to the
maximum number of debug jobs you want against this profile in parallel.

**Sending credentials over plain HTTP** (`http://...` + `api_key`)
prints a stderr warning but does not refuse — local dev may legitimately
route through a loopback proxy.

### Verify

```
pyopsctl config check
pyopsctl version
pyopsctl health
```

`config check` hits `GET /version` and reports reachability + which
auth pathway was used. It's the one-line smoke test.

### Commands

Every command accepts the global flags: `--profile NAME`, `--server URL`,
`--user U`, `--api-key STRING`, `--api-key-stdin`, `--json`, `--quiet`,
`--timeout SECS`.

#### `scriptlet` — inventory + draft / save workflow

- `pyopsctl scriptlet list [--set NAME]`
- `pyopsctl scriptlet sets`
- `pyopsctl scriptlet status SET NAME`
- `pyopsctl scriptlet repo-status SET`
- `pyopsctl scriptlet get-code SET NAME`
- `pyopsctl scriptlet create SET NAME --kind {task|service|schedule|controller|view|library|markdown|jslibrary}`
- `pyopsctl scriptlet draft SET NAME [--file PATH | --stdin]`
- `pyopsctl scriptlet delete SET NAME`
- `pyopsctl scriptlet diff SET NAME [--local-file PATH]`

#### `job` — full lifecycle

- `pyopsctl job list [--state S] [--owner U] [--set X] [--name Y]`
- `pyopsctl job view ID [--follow] [--stream stdout|stderr|both]`
- `pyopsctl job run SET NAME [--kind task|service] [--param TEXT | --param-file PATH] [--drop-done] [--max-retries N]`
- `pyopsctl job cancel ID`
- `pyopsctl job delete ID`
- `pyopsctl job download ID [--out PATH]`
- `pyopsctl job cancel-all [--owner U] [--set X]`
- `pyopsctl job clean --state done|failed [--owner U]`

#### `debug` — interactive pdb sessions

`debug start` composes a stdlib-pdb bootstrap into the job's `param`,
leases a TCP port from the profile's `debug_ports` pool, and enqueues
the debug job. The job's `exec(param)` lands at `RemotePdb(...).set_trace()`
and **waits** for `debug attach`. There's no detached mode — under the
new server contract a debug job has no separate "ready" signal; it's
ready when the pdb listener accepts the first connection, and the
session daemon does that wait at exactly the right moment.

Lifecycle:

- `pyopsctl debug start SET NAME [--param TEXT | --param-file PATH] [--overrides-dir DIR]`
  — leases a port, composes `#!pip remote-pdb` + `RemotePdb(host, port).set_trace()`
  into `param`, enqueues. Optional `--param` / `--param-file` is
  appended *after* the bootstrap so the user's preamble runs once
  attached. `--overrides-dir DIR` walks `DIR/<set>/*.py` and submits
  the contents as the job's `overrides` dict — local-edit debugging
  without committing, same shape as VSIX's `DebugService.buildOverrides`.
  Returns `{job_id, set, name, port, host}`.
- `pyopsctl debug attach JOBID` — spawns the per-job session daemon,
  connects to the remote-pdb listener (retry-with-timeout — first-time
  per-set venv installs can be slow), prints the initial state dump.
  The daemon holds the connection for the lifetime of the session;
  subsequent inspect/control verbs route through it via a per-job
  Unix-domain socket.
- `pyopsctl debug stop JOBID` — cancels the job and releases the port
  lease for the next `debug start` against this profile.

Inspect (daemon-routed):

- `state [--job-id]` — current state (cached, no pdb traffic)
- `stack [--job-id]` — refresh + full state dump
- `locals [--frame ID] [--job-id]` / `globals [--frame ID] [--job-id]`
- `evaluate "EXPR" [--frame ID] [--job-id]`
- `source <set>::<name> [--lines START:END] [--job-id]`

Control (daemon-routed):

- `step [--job-id]` / `step-in [--job-id]` / `step-out [--job-id]` — pdb's `n`/`s`/`r`
- `continue [--job-id]`
- `break set <set>::<name>:<line> [--condition E] [--job-id]`
- `break list [--job-id]`
- `break remove <id> [--job-id]` / `break remove --all [--job-id]`

Teardown (daemon-routed):

- `detach [--job-id]` — close the pdb session; the job continues from
  `set_trace()` (user code appended after the bootstrap runs to
  completion).
- `terminate [--job-id]` — send pdb `q` (BdbQuit); the job ends.

Bookkeeping:

- `pyopsctl debug session list`
- `pyopsctl debug session info [--job-id]`

State-dump output (default human mode) is documented in
[`docs/insights_ctl.md`](insights_ctl.md) § "Interactive debug —
session daemon" — four sections (globals, locals, code window with `→`
on the current line, callstack) separated by 60-hyphen rules. `--json`
emits the same shape as a single payload for scripting.

Verbs deliberately not supported: `pause` (needs a server-side signal
channel that doesn't exist), `var --ref N` (pdb has no DAP-style
`variablesReference` — drill via `evaluate "obj.attr"` instead),
`threads` (pdb is single-threaded by design).

#### `health`, `metrics`, `version`

- `pyopsctl health [--full]` — summary; `--full` for the entire payload
- `pyopsctl health failed` — every failed job in detail
- `pyopsctl metrics` — raw Prometheus exposition
- `pyopsctl version` — server version + CLI version

#### `config`

- `pyopsctl config show [--profile NAME]` — prints the resolved profile, `api_key` redacted
- `pyopsctl config check [--profile NAME]` — reachability + auth pathway smoke test

### Output modes

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

### Worked examples

**Author + sync a scriptlet:**

```
pyopsctl scriptlet sets                                       # user sets
pyopsctl scriptlet list --set myset
pyopsctl scriptlet create myset myscript --kind task          # empty stub
$EDITOR myscript.task.py
pyopsctl scriptlet draft myset myscript --file myscript.task.py
pyopsctl scriptlet diff  myset myscript --local-file myscript.task.py
```

`draft` writes the working-tree copy without committing. Use `diff`
first to see what's about to change. Commit + push the whole set's
draft changeset at once via the Sets view's "Commit drafts" button (or
`pyopsctl job run internal set_commit_drafts --param 'name="myset"; message="..."'`
if you'd rather drive it from the CLI).

**Run a task and follow it:**

```
JOB=$(pyopsctl job run myset myscript --kind task -q)
pyopsctl job view "$JOB" --follow --stream both
```

**Operator / Agent: interactive debug:**

```
# 1) Start a debug job. The runner pauses at the pdb bootstrap.
pyopsctl debug start myset myscript --json
# {"job_id": "...", "set": "myset", "name": "myscript",
#  "port": 55678, "host": "localhost"}

# 2) Attach. The session daemon spawns and prints the initial state.
pyopsctl debug attach "$JOB_ID"

# 3) Drive the debugger.
pyopsctl debug evaluate "obj.attr"
pyopsctl debug step-in
pyopsctl debug locals
pyopsctl debug break set myset::myscript:42
pyopsctl debug continue                  # state dump on bp hit

# 4) Done — either let the job finish, or terminate it.
pyopsctl debug detach                    # session ends, job continues
# OR
pyopsctl debug terminate                 # send q (BdbQuit), job ends
```

Stage now, attach later: `debug start` returns immediately after the
enqueue; the runner waits at `set_trace()` until something connects to
the leased port. An agent that pre-stages a debug job and only attaches
once a downstream condition is met just runs steps (1) and (2) from
different processes.

User preamble (runs *after* attach, before the script body):

```
pyopsctl debug start myset myscript --param-file ./preamble.py
```

The preamble file can carry its own header directives (`#!pip somepkg`,
etc.) — they end up on their own lines below the pdb bootstrap and the
runner sees both.

**Monitoring + bulk ops:**

```
pyopsctl health [--full]
pyopsctl health failed
pyopsctl metrics                                # raw Prometheus
pyopsctl job cancel-all --owner mybot
pyopsctl job clean --state done
```

### Not currently in pyopsctl

Set lifecycle (clone, create, pull, switch, remove, update-deps) and
schedule CRUD still go through the WebUI (Sets Manager below;
`/scriptlet/schedule/*` for schedules). The set flows can be invoked
from the CLI by running the internal task scriptlets directly (e.g.
`pyopsctl job run internal set_clone --param '<json>'`), but the param
schema isn't part of pyopsctl's documented surface yet.

## Sets Manager

PyOps UI: http://{pyops.serverUrl}/controller/internal/sets

The Sets view is itself a pyops scriptlet — a controller backed by the `internal::data` service. Each action (clone, create, pull, switch, remove, update-deps) enqueues a `set_*` task with `drop_done=true`; a modal polls the job and tails its stdout/stderr live until terminal state.

- List sets/branch; auto-refresh.
- Add: clone (name, Git URL/branch, git name/email, username/password).
- Actions: pull (sync repos), switch (local branches), remove (confirm), update dependencies.

Safe Git ops with per-set locks; errors and progress shown in the live job modal.

## Sidebar — collapsed-by-default user sets

The left sidebar groups built-in pyops entries (Manager / Scheduler / Monitor / Sets) under one section and one section per discovered user set. The built-in section is always expanded; user-set sections start **collapsed** so a host with many sets stays scannable.

Force-expand specific sets at boot via `etc/pyops.json`:

```json
{
    "web": {
        "bind": "0.0.0.0:80",
        "worker": 8,
        "timeout": 300,
        "expanded_sets": ["system", "myops"]
    }
}
```

`expanded_sets` is an array of set names whose sidebar section should open by default. Each section is still individually collapsible at runtime via the section header chevron — the config only seeds the initial state.
