# PyOps User Guide

## Introduction

PyOps is an execution platform for Python operations scriptlets, licensed under GNU AGPLv3. It allows set developers to build reusable operations: scriptlets for tasks/services, controllers for UI routing, views for HTML fragments. Code is stored in remote sets (logical groups, e.g., "myops"). Use the PyOps VSIX extension in VSCode for tree views, commands, and local editing mirrors (pyops/ dir). Integrate Cline (or similar LLM extension) for one-shot code generation into files based on prompts.

### VSIX Installation and Configuration

- **Install**: In VSCode, go to Extensions view > ... > Install from VSIX > select `vsix/pyops.vsix`.
- **Configure** workspace settings (Ctrl+, > Workspace tab, or `Preferences: Open Workspace Settings (JSON)`):
  ```json
  {
    "pyops.profile": "",            // Client-config profile to inherit connectivity from (empty → the file's default_profile)
    "pyops.serverUrl": "http://localhost:8000",
    "pyops.localPyopsDir": "pyops",
    "pyops.autoRefreshInterval": 10,
    "pyops.authMode": "basic",      // "basic" (Basic Auth, via an auth proxy) or "x-user" (send X-User directly, no password)
    "pyops.username": "anonymous",  // Basic Auth username, or the X-User identity in x-user mode
    "pyops.excludedSets": [],       // Sets to hide from the manager tree view (blacklist)
    "pyops.includedSets": []        // If non-empty, only these sets are shown and excludedSets is ignored (whitelist)
  }
  ```
- **Connectivity profile**: if the shared PyOps client config `~/.config/pyops/config.toml` exists (the file `pyopsctl` reads, see *pyopsctl → Configure* below; override via `PYOPSCTL_CONFIG`), the extension takes `serverUrl`, `username`, auth and `debugPorts` from the profile named by `pyops.profile` (empty → the file's `default_profile`). Any of those `pyops.*` settings you set explicitly overrides the profile's value; without the file (or a matching profile) the settings are used as-is. The set black/whitelist and the other settings are always VSCode-only.
- **Auth**: `basic` — set `pyops.username`; the extension prompts for the password on first API call (stored securely in workspace state). With a profile, its `api_key` is used as the password with no prompt — unless `pyops.username` remaps a different user onto the profile, which brings the prompt back. `x-user` — the extension sends `X-User: <username or "vsix">` with no password, for direct connections to a server that trusts the header (dev / no auth proxy). `pyops.authMode` defaults to `basic`; with a profile and no explicit `authMode`, a profile with an `api_key` means `basic`, one without means `x-user`.
- **Status**: Status bar shows connection (requires valid `pyops.serverUrl`).

VSIX provides pyops-container in sidebar/activitybar:
- **pyops-manager**: Sets > type folders (task/service/library/controller/view/markdown/jslibrary/data) > scriptlets; icons: ✓ synced, ⚠ unsynced/draft/local-only; contextValues pyopsItem-{type}(-local). Markdown supports multiple pages: top-level `README.md` plus any number of free-named `docs/{name}.md`. Data files (yaml/json) live under `data/{name}.{yaml,yml,json}` and round-trip through the standard sync/diff/draft flow.
  Right-click: Reset Local from Remote, Write Local to Remote Draft, Diff Local Remote, Delete Scriptlet; Start/Stop Debug Job (tasks). Per-file commit/push is gone — commit the whole set's draft changeset at once via the Sets view.
  Titlebar: Refresh Manager, Create Scriptlet, Attach to Debug Job.
- **pyops-monitor**: Jobs by state; right-click: Copy Job Param, Cancel/Download/Delete/View Job.
  Titlebar: Refresh Monitor, Cancel All Jobs, Delete All Done/Canceled, Delete All Failed/Killed.
- Sets Manager: Open http://{pyops.serverUrl}/controller/pyops/sets for clone/switch/remove.

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
- Markdown: "Write a markdown scriptlet for documentation. Use `README` as name for the set's top-level README.md, or any other name for a `docs/{name}.md` page."
- Data: "Create a YAML data file scriptlet with `threshold: 42`, `enabled: true`, and a `defaults` section. Tasks/libraries read it via `with data.read('config') as cfg:`."

Cline outputs full file content; paste/replace in editor, then save via VSIX. Iterate prompts for refinements (e.g., "Add validation to previous code").

## VSIX Workflow

- **Create Scriptlet**: pyops-manager titlebar > Create Scriptlet (prompts for set/type/name); opens local `pyops/{set}/{type}/{name}.{ext}` (.py for task/service/library/controller, .html for view, .md for markdown, .js for jslibrary, .yaml for data). The server stores markdown as `docs/{name}.md` (or `README.md` if name=README) and data as `data/{name}.{yaml,json}`; the per-type subdir layout is a client-side convention.
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

## Writing Tests

Tests are a peer of debug: enqueue any `*.task.py` with `kind=test` and pyops sets `__name__='__test__'`, materialises sources under `.test/src/<set>/{task,library}/<name>.py`, then exec's. The same file can serve as both production task and its own test suite via the `__main__` / `__test__` idiom — no retry, no timeout, same source-path-mapping shape as debug.

Pyops core is pytest-agnostic. The bundled `pyops::pytest` library encapsulates the common pattern: AST-driven class/method-id resolution against the materialised library tree, programmatic `pytest.main()` invocation, auto-attached coverage when `pytest-cov` is in the import graph, `[paths]`-mapped `.coveragerc` so reports reference canonical source.

```python
# my_task.task.py
#!pytest-cov                # optional — turns on coverage automatically
#!{::unit_tests}            # test classes live in libraries
#!{pyops::pytest}           # the helper; transitively installs pytest

def do_work(x):
    return x * 2

if __name__ == "__main__":
    do_work(input_value)

if __name__ == "__test__":
    pytest_run(
        test_sets=["TestWork", "TestEdge::test_zero"],
        args=["-v"],
    )
```

Test libraries hold the pytest classes. Production code those classes
need is reached the same way every pyops scriptlet reaches its
directive-merged content: by bare name. `pytest_run` captures the
calling task's `f_globals` (one frame up — that's why the convention
is to call it directly from the `__test__` block, never through a
helper) and pre-registers each materialised test file in
`sys.modules` with that namespace seeded into the module's `__dict__`
before the file body runs. So the test library can reference any
symbol the task pulled in via `#!{set::name}` directives, plus
pyops-injected globals (`setname`, `workspace`, `data`, `listener`,
`control`, `gitspace`, ...), by bare name. **Classical
`from production_lib import do_work` inside test files is wrong for
pyops-side content** — it would imply that the production library
sits in `sys.modules` as a Python module, which is not what the
directive merge produces and would also push pyops library authors
to dodge stdlib / framework module names. Classical imports remain
valid (and necessary) for stdlib and venv-installed packages
(`pytest`, framework libs installed via `#!<pip-pkg>` / git
directives) — those reach `sys.modules` independently of the pyops
directive merge.

```python
# my_task.task.py
#!pytest-cov                # optional — turns on coverage automatically
#!{::shared_lib}            # production helpers visible to both task body + tests
#!{::unit_tests}            # test classes live in libraries
#!{pyops::pytest}           # the helper; transitively installs pytest

def work_with(x):
    return double(x)         # `double` directive-merged from shared_lib

if __name__ == "__main__":
    work_with(input_value)

if __name__ == "__test__":
    pytest_run(
        test_sets=["TestWork", "TestEdge::test_zero"],
        args=["-v"],
    )
```

```python
# unit_tests.library.py — no `from shared_lib import double` line;
# `double` lands here via the pyops::pytest namespace bridge.
import pytest               # stdlib / venv → classical import is fine

class TestWork:
    def test_doubles(self):
        assert double(3) == 6

    def test_zero(self):
        assert double(0) == 0

class TestEdge:
    def test_zero(self):
        assert double(0) == 0

    def test_negative(self):
        assert double(-2) == -4

class TestEnv:
    def test_workspace_visible(self):
        # pyops-injected globals are seeded by the bridge too
        import os
        assert os.path.isdir(workspace)

@pytest.fixture
def shared_thing():
    return {"key": "value"}
```

Fixtures, monkeypatch, and `@pytest.fixture` all work — pyops invokes the real pytest API.

Enqueue: `pyopsctl job run myset my_task --kind test`, or `PUT /scriptlet/job/item` with `kind=test`, or via the manager UI's **Run as Test** button (material `science` icon, info-blue, sits next to **Run as Draft** on every task row and inside the task editor).

## Set-Scoped Data

Tasks and libraries in the same set can read/write small structured files under `data/git/{set}/data/{name}.{yaml,yml,json}` via the `data` global (peer of `control` and `listener`, injected into every scriptlet's namespace).

```python
with data.read("config") as cfg:        # shared lock — concurrent reads run in parallel
    threshold = cfg["threshold"]

with data.write("state") as st:         # exclusive lock — blocks readers and other writers
    st["last_run"] = time.time()

data.list()                              # ['config', 'state']
```

Format is detected by file extension. On first create, `data.write("name", format="yaml")` or `format="json"` is required. Existing files keep their format; a name is unique across formats (`config.yaml` and `config.json` can't coexist). Cross-set access raises — there is no API surface that accepts another set name.

Files are versioned alongside scriptlets; runtime writes show as unstaged changes foldable into the standard draft-commit flow. Per-file flocks live alongside the data files under `data/.{name}.lock` and are gitignored automatically.

## Full Workflow Example

1. **Clone Set**: Browser to http://localhost:8000/controller/pyops/sets > Add (name="myops", URL=git@github.com/user/myops.git, branch=main).
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

`pyopsctl` reads `~/.config/pyops/config.toml` — the shared PyOps client
config, whose connectivity profiles are read by every PyOps client (the
CLI here, and the VSIX extension via its `pyops.profile` setting). The
file must be mode 0600 — `pyopsctl` refuses to load otherwise (it holds
API keys).

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
- `pyopsctl scriptlet create SET NAME --kind {task|service|schedule|controller|view|library|markdown|jslibrary|data}`
- `pyopsctl scriptlet draft SET NAME [--file PATH | --stdin]`
- `pyopsctl scriptlet delete SET NAME`
- `pyopsctl scriptlet diff SET NAME [--local-file PATH]`

#### `job` — full lifecycle

- `pyopsctl job list [--state S] [--owner U] [--set X] [--name Y]`
- `pyopsctl job view ID [--follow] [--stream stdout|stderr|both]`
- `pyopsctl job run SET NAME [--kind task|service|debug|draft|test] [--param TEXT | --param-file PATH] [--drop-done] [--max-retries N]`
- `pyopsctl job cancel ID`
- `pyopsctl job delete ID`
- `pyopsctl job download ID [--out PATH]`
- `pyopsctl job cancel-all [--owner U] [--set X]`
- `pyopsctl job clean --state done|failed [--owner U]`

> **`--param` is a Python code snippet, not a JSON/argument payload.**
> The runner runs `exec(param, globals)` *before* the scriptlet body, so
> `param` is where you bind variables the scriptlet then reads by bare
> name. There is **no** injected `param` global and **no** JSON parsing.
>
> ```
> # param sets two globals the scriptlet reads as `customer` / `quarter`:
> pyopsctl job run reporting export_report \
>   --param 'customer = "000000_sysup"; quarter = "2025-Q4"'
> ```
>
> The scriptlet body just references `customer` and `quarter` directly.
> The same model holds for one scriptlet starting another via
> `control.job.create(set, name, param=f"objid = {obj_id}")` — the
> callee picks `objid` up as a global. A task that needs structured
> input typically takes a JSON *string* and parses it itself, e.g.
> `--param 'raw = "{...}"'` then `json.loads(raw)` in the body — the
> `--param` value is still Python source, never parsed by the runner.

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
`pyopsctl job run pyops set_commit_drafts --param 'name="myset"; message="..."'`
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
from the CLI by running the pyops-set task scriptlets directly — the
param is a code snippet binding the task's expected variables (e.g.
`pyopsctl job run pyops set_clone --param 'url = "https://git.example/grp/repo.git"'`),
but which variables each set task expects isn't part of pyopsctl's
documented surface yet.

## Sets Manager

PyOps UI: http://{pyops.serverUrl}/controller/pyops/sets

The Sets view is itself a pyops scriptlet — a controller backed by the `pyops::data` service. Each action (clone, create, pull, switch, remove, update-deps) enqueues a `set_*` task with `drop_done=true`; a modal polls the job and tails its stdout/stderr live until terminal state.

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
