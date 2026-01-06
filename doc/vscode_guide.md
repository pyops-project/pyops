# Pyops Developer Guide: Creating and Managing Sets/Scriptlets via VSIX and Cline

This guide is for developers creating and maintaining sets and scriptlets in Pyops using VSCode with the PyOps VSIX extension and tools like Cline for AI-assisted code generation. Pyops enables isolated Python executions for tasks and services, with Git-stored code in remote sets, accessible via HTTP API/UI, and optional scheduling. Jobs run in dedicated workspaces, with owner based on authentication (default "anonymous"). Focus: Coding scriptlets, controllers, and views in local mirrors via VSIX, syncing to remote sets for activation. Assume Pyops server is running (e.g., via `run.sh`), with web UI at the configured bind (e.g., http://localhost:8000).

## Introduction

Pyops allows set developers to build reusable operations: scriptlets for tasks/services, controllers for UI routing, views for HTML fragments. Code is stored in remote sets (logical groups, e.g., "myops"). Use the PyOps VSIX extension in VSCode for tree views, commands, and local editing mirrors (pyops/ dir). Integrate Cline (or similar LLM extension) for one-shot code generation into files based on prompts.

### VSIX Installation and Configuration
- **Install**: In VSCode, go to Extensions view > ... > Install from VSIX > select `vsix/pyops.vsix`.
- **Configure** workspace settings (Ctrl+, > Workspace tab, or `Preferences: Open Workspace Settings (JSON)`):
  ```json
  {
    "pyops.serverUrl": "http://localhost:8000",
    "pyops.localPyopsDir": "pyops",
    "pyops.autoRefreshInterval": 10,
    "pyops.username": "anonymous",  // Optional for Basic Auth (X-User header; password prompted & stored securely in workspace state)
    "pyops.excludedSets": []        // Exclude sets from manager tree
  }
  ```
- **Auth**: Set `pyops.username`; extension prompts for password on first API call (stored securely in workspace state).
- **Status**: Status bar shows connection (requires valid `pyops.serverUrl`).


VSIX provides pyops-container in sidebar/activitybar:
- **pyops-manager**: Sets > type folders (task/service/library/controller/view/markdown) > scriptlets; icons: ✓ synced, ⚠ unsynced/draft/local-only; contextValues pyopsItem-{type}(-local).
  Right-click: Reset Local from Remote, Write Local to Remote Draft, Save Remote Draft, Diff Local Remote, Delete Scriptlet; Start/Stop Debug Job (tasks).
  Titlebar: Refresh Manager, Create Scriptlet, Attach to Debug Job.
- **pyops-monitor**: Jobs by state; right-click: Copy Job Param, Cancel/Download/Delete/View Job.
  Titlebar: Refresh Monitor, Cancel All Jobs, Delete All Done/Canceled, Delete All Failed/Killed.
- Sets Manager: Open http://server/scriptlet/sets.html for clone/switch/remove.


Local `pyops/` mirrors remote files for editing (create opens file; upload/save syncs changes).

### Coding with Cline
Use Cline in VSCode to generate code into existing scriptlet files via prompts. Open file in VSIX (e.g., via create command), then prompt Cline with context + instructions for one-shot replacement.

Examples:
- Task: "Generate a task scriptlet to fetch data from https://api.example.com using requests, process JSON, write result to job output file, and log completion. Use directives for deps. Ensure error handling."
- Service: "In this service file, add a handler for POST requests with JSON data: validate payload, call external API via control, return {'status': 'ok', 'value': result}. Include setup loop with listener."
- Controller: "Update this controller to route to 'myservice', use views 'head', 'menu', 'body', set debug=False, name='My UI'."
- View: "Enhance body.html with Bootstrap table for job list, AJAX GET to /q?cmd=list via apiCall, populate rows with Material Icons."

Cline outputs full file content; paste/replace in editor, then save via VSIX. Iterate prompts for refinements (e.g., "Add validation to previous code").

## Core Concepts

- **Sets**: Remote repositories grouping scriptlets (e.g., "myops"). Managed via Sets Manager (clone from URL, switch branches, remove).
- **Scriptlets**: Python files for execution:
  - **Tasks**: One-off runs (e.g., data fetch/process).
  - **Services**: Persistent handlers for requests (e.g., from UI/API).
  - **Libraries**: Reusable code (imported via directives).
- **Controllers**: Python stubs defining UI structure and routing to services (e.g., /controller/{set}/{name}).
- **Views**: HTML fragments (head for styles/JS, menu for nav, body for content) rendered by controllers.
- **Execution Model**: Isolated global dict (`glob`) with:
  - `control`: Pyops.Control for API calls (e.g., `control.job.list()` returns dict of jobs).
  - `listener`: For services (inbound requests; see Services).
  - Logging: Use Python's built-in `logging` module for structured logging. Configure it early:
  ```
  import logging
  logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  logger = logging.getLogger(__name__)
  ```
  Output routes to stdout.log (INFO and above) and stderr.log (ERROR and CRITICAL). For simple debugging, `print()` works but lacks levels and formatting.
  - Job workspace: Dir for outputs/files (abstract; access via `workspace` var in code).
- **Dependency Directives**: Line-start (after indent), processed before exec (in param/libraries/main). No recursion.
  - Pip: `#!module_name` (e.g., `#!requests` or `#!numpy==1.21`); installs quietly in isolated venv.
  - Git Repo: `#!git repopath [branch]` (e.g., `#!git git@github.com/user/myrepo.git main`); clones/pulls, prepends to sys.path.
  - Local Library: `#!{::lib_name}` (own set) or `#!{otherset::lib_name}` (foreign); loads/execs.
  - Post-process: Commented out (e.g., `# #!requests`); errors fail job.
- **Param**: Optional pre-exec code string (enqueue via UI/API; supports directives). Vars/functions available in main.
- **Debug Mode**: Enqueue as "debug" (uses draft files, no auto-cleanup).
- **Enqueue/Run**: Via web UI (e.g., /scriptlet/manager.html) or API (POST /scriptlet/job/item {set, name, kind="task"/"service"/"debug", param, drop_done}); manage jobs in VSIX pyops-monitor.
- **Scheduling**: POST /scriptlet/schedule/item {set, name, kind="task", schedule="cron_expr" (e.g., "* * * * *"), param, drop_done}.
- **Monitoring**: VSIX pyops-monitor or /scriptlet/monitor for jobs/schedules by state. Download logs/files.
- **API Access**: HTTP to `/controller/{set}/{name}/{rest:path}` proxies (via controller globals service name) to service listener with ZMQ msg dict: `{"type": METHOD, "path": "/{rest:path}", **all query_params (e.g. ?cmd=run → "cmd": "run"), "data": body (parsed to dict if application/json, str if text/*, bytes otherwise), "data_type": "json"/"text"/"binary"/"none"}`. Convention: `/q?cmd=...` GET or POST `/q` body.

## VSIX Workflow

- **Create Scriptlet**: pyops-manager titlebar > Create Scriptlet (prompts for set/type/name); opens local `pyops/{set}/{type}/{name}.{ext}` (.py for task/service/library/controller, .html for view, .md for markdown).
- **Edit/Generate**: Use VSCode/Cline to code. Edit local file.
- **Sync**: Right-click > Reset Local from Remote (download draft), Write Local to Remote Draft (upload), Save Remote Draft (commit/push).
- **Diff**: Right-click > Diff Local Remote.
- **Debug**: Right-click task(-local) > Start/Stop Debug Job.
- **Delete**: Right-click > Delete Scriptlet (remote; local-only ok).
- **Enqueue/Monitor**: pyops-monitor right-click jobs; titlebar bulk ops.
- No auto-sync: Manual control.

## Advanced Debugging

**PyOps Server**:
Add to `etc/pyops.json`:
```json
"users": {
  "anonymous": {"debug_port": 5678}
}
```
- GET /debug/port returns {"port": port} for owner (X-User/anonymous).
- Runner kind="debug": auto-installs debugpy, injects `debugpy.listen(('0.0.0.0', port))`; `wait_for_client()` if param '#>AWAIT_DEBUG'.
- Parses `overrides` JSON: uses local code for `{set}::{name}` keys (main/libs).
- Kills previous owner debug job on enqueue.

**VSIX**:
- Right-click task(-local) > Start Debug Job: recursive pyops/set/*.py → overrides, enqueues debug '#>AWAIT_DEBUG', fetches port/jobId.
- pyops-manager titlebar > Attach to Debug Job: lists kind="debug" running/dequeued (1 only), vscode.debug attach localhost:{port} justMyCode=false.
- Local-only tasks: full debug cycle (overrides skip remote check).

**Controllers**: `debug=True` uses unstaged/draft code (preview).

## Execution environment

Execution environment provides these globals:
- `userspace`: `data/jobs/{owner}/` (owner's jobs root)
- `setspace`: `data/jobs/{owner}/{set}/` (set's jobs root)
- `workspace`: `data/jobs/{owner}/{set}/{id}/` (this job's workspace)
- `listener`: ServiceListener for inbound ZMQ IPC (services only). `listener.listen()` blocking; `listener.poll()` non-blocking (None/"TERM"/msg); `response(dict)`.

## Scriptlet Types

Precreate via VSIX; exec order: param → libraries (directives) → main.

### Tasks (One-Off)
Use: Run code once, produce output/files.

Template (`task/{name}.py`):
```
# Task: One-off execution
#!requests  # Install requests if missing
#!git git@github.com/user/myrepo.git main  # Clone/pull myrepo

import os
import requests  # Now available
import myrepo.helper  # From cloned repo

# Access control and workspace
# control: e.g., control.job.list()
# workspace: job output dir

# Example: HTTP request
response = requests.get("https://api.example.com/data")
data = response.json()

# Write output
output_path = os.path.join(workspace, "result.txt")
with open(output_path, "w") as f:
    f.write(str(data))

# Example: API call via control
jobs = control.job.list()  # Returns dict

logger.info("Task done")
```

Param Example (enqueue string):
```
# Pre-setup with deps
#!pandas
import pandas as pd
df = pd.DataFrame({"col": [1, 2]})
MY_DF = df  # Available in main
```

Run: Enqueue kind="task" via VSIX/API; outputs to workspace/logs; drop_done auto-removes.

### Services (Persistent)
Service Tasks (kind="service" jobs) loop with listener for inbound requests.
Use: Handle requests from controllers/API (e.g., /q endpoints).

Request: `listener.listen()` returns dict:
- "type": "GET"/"POST"/"PUT"/"DELETE".
- Query params: Direct keys (e.g., ?cmd=run → msg["cmd"] = "run").
- Body (POST/PUT/DELETE): "data" (dict/JSON str/text/bytes/none); "data_type" ("json"/"text"/"binary"/"none").
- From controllers: Body as direct payload dict in "data".
- Respond: `listener.response(result_dict)` (e.g., {"status": "ok", "value": ...}).
- "TERM": Shutdown.

Template (`service/{name}.py`):

#### Blocking Example (listen() - Simple Loops)
```
# Service: Persistent, handles requests (blocking listener)
#!flask  # Install Flask for internal use
#!git git@gitlab.com/team/utils.git  # Clone utils

import time
import utils.api  # From cloned repo

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

# Setup (runs once)
logger.info("Service started")

# Main loop: Blocking poll for requests (original/default)
while True:
    msg = listener.listen()  # Blocks up to 1s until message or TERM
    if msg == "TERM":
        break  # Graceful shutdown

    request_type = msg.get("type")
    cmd = msg.get("cmd")  # Query param
    other_param = msg.get("other")

    if request_type == "GET":
        if cmd == "fetch":
            result_value = utils.api.fetch_external(other_param)
            result = {"status": "ok", "value": result_value}
        else:
            result = {"status": "error", "message": "Unknown cmd"}
    elif request_type in ["POST", "PUT"]:
        data = msg.get("data", {})  # Body data; direct payload from controllers
        if isinstance(data, str) and msg.get("data_type") == "json":
            try:
                import json
                data = json.loads(data)
            except:
                pass
        data_type = msg.get("data_type")
        if data_type == "json":
            result_value = utils.api.process_json(data)
        else:
            result_value = "Processed: " + str(data)
        result = {"status": "ok", "value": result_value}
    else:
        result = {"status": "error", "message": "Unsupported type"}

    listener.response(result)  # Send dict back

logger.info("Service stopped")
```

#### Non-Blocking Example (poll() - Responsive Loops)
```
# Service: Persistent, handles requests (non-blocking poll)
#!git git@gitlab.com/team/utils.git  # Clone utils

import time
import utils.api  # From cloned repo

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

# Setup (runs once)
logger.info("Service started")

# Main loop: Non-blocking poll for requests (alternative for interleaving work)
while True:
    msg = listener.poll()  # Returns immediately: msg, "TERM", or None
    if msg == "TERM":
        break
    elif msg is not None:
        request_type = msg.get("type")
        cmd = msg.get("cmd")  # Query param
        other_param = msg.get("other")

        if request_type == "GET":
            if cmd == "fetch":
                result_value = utils.api.fetch_external(other_param)
                result = {"status": "ok", "value": result_value}
            else:
                result = {"status": "error", "message": "Unknown cmd"}
        elif request_type in ["POST", "PUT"]:
            data = msg.get("data", {})  # Body data; direct payload from controllers
            if isinstance(data, str) and msg.get("data_type") == "json":
                try:
                    import json
                    data = json.loads(data)
                except:
                    pass
            data_type = msg.get("data_type")
            if data_type == "json":
                result_value = utils.api.process_json(data)
            else:
                result_value = "Processed: " + str(data)
            result = {"status": "ok", "value": result_value}
        else:
            result = {"status": "error", "message": "Unsupported type"}

        listener.response(result)  # Send dict back
    else:
        # No message; perform other non-blocking work (e.g., periodic checks)
        # time.sleep(0.1)  # Optional small delay to reduce CPU
        pass

logger.info("Service stopped")
```

Run: Enqueue kind="service" via VSIX/API; auto-restarts (<3 failures). Requests routed from UI/API.

### Libraries (Reusable)
Use: Shared functions, imported via directives.

Template (`library/{lib_name}.py`):
```
# Library: Pure code, no directives/loops
#!numpy  # Install if needed

import numpy as np

def helper_func(x):
    return np.array(x) * 2

# Available after #!{::lib_name} import
```

Usage: In scriptlet/param: `#!{::lib_name}` (own set) or `#!{otherset::lib_name}` (foreign); replaces with comment after load/exec.

## Controllers and Views

### Controllers (UI Stubs)
Use: Define UI and route to service.

Precreate `controller/{name}.py` via VSIX (committed only).

Template:
```
# Controller: Defines UI and routes to service
set = "myops"                  # Your set
service = "myservice"          # Target service name
debug = False                  # True for draft code
head = "my_head"               # Head view name
menu = "my_menu"               # Menu view name
body = "my_body"               # Body view name
name = "My Controller Title"   # Menu display
```

Run:
- UI: GET /controller/{set}/{name} (renders base HTML + views; Bootstrap/jQuery/Material Icons).
- API: `/{set}/{name}/{rest:path}` proxies ALL methods (exec controller for service name) to service via ZMQ: path=/{rest:path}, all query_params as keys (e.g. cmd), body parsed to data/data_type by Content-Type. E.g., `/q?cmd=...`.
- Menu: GET /menu lists controllers (uses "name").

In views, use {{SET}}/{{NAME}} for dynamic routes ({{NAME}} = basename, e.g., 'os-dashboard-controller').

### Views (HTML Fragments)
Precreate `view/{name}.html` via VSIX (committed only; raw HTML).

Guidelines:
- Global: Bootstrap 5 with automatic light/dark theme switching based on system preference (prefers-color-scheme), jQuery, Material Icons (e.g., `<i class="material-icons">add</i>`). No manual toggle buttons are provided; themes switch automatically.
- Use Bootstrap classes like `bg-light`/`bg-dark`, `text-light`/`text-dark`, `table-secondary`/`table-dark`, `btn-outline-secondary`, etc., for consistent theming across light and dark modes.
- No `<title>` or `<h1>` (auto-handled).
- JS `<script>` preferably in head; inline in body ok for simple scripts. Custom in head.html.
- Use {{SET}}/{{NAME}} in URLs/AJAX for portability.

#### Head View (my_head.html)
Styles/scripts only.
```
<style>
  .custom-head { color: blue; }
</style>
<script>
  // Utility apiCall for AJAX to /q
  function apiCall(type, params, callback) {
    var url = "/controller/{{SET}}/{{NAME}}/q";
    if (type === "GET") {
      $.get(url, params, callback);
    } else {
      $.ajax({
        url: url,
        type: type,
        data: JSON.stringify({data: params, data_type: "json"}),
        contentType: "application/json",
        success: callback
      });
    }
  }
</script>
```

#### Available Resources

All standard libraries and frameworks are available at `/files/` paths:

**CSS Frameworks:**
- Bootstrap 5.2.3: `/files/css/bootstrap/bootstrap.min.css` (supports `data-bs-theme` for automatic light/dark theming)
- Material Icons: `/files/css/material/material-icons.css`
- ACE Editor theme: `/files/css/ace/ace.css`

**JavaScript Libraries:**
- jQuery 3.6.4: `/files/js/jquery.min.js`
- js-cookie 3.0.5: `/files/js/js.cookie.min.js`
- Bootstrap 5.2.3: `/files/js/bootstrap/bootstrap.bundle.min.js`
- ACE Editor core: `/files/js/ace/ace.js`
- ACE modes: `/files/js/ace/mode-{name}.js` (e.g., mode-python.js, mode-javascript.js, mode-html.js, mode-css.js, mode-json.js, mode-markdown.js, etc.)
- ACE extensions: `/files/js/ace/ext-{name}.js` (e.g., ext-linking.js, ext-language_tools.js)

**Dynamic Loading Pattern (Recommended):**
For ACE modes, load on-demand to avoid initial load delays:
```javascript
function loadAceMode(mode, callback) {
  try {
    ace.require("ace/mode/" + mode);
    callback();
  } catch {
    var script = document.createElement('script');
    script.src = '/files/js/ace/mode-' + mode + '.js';
    script.onload = callback;
    document.head.appendChild(script);
  }
}

// Usage:
loadAceMode('python', function() {
  editor.session.setMode("ace/mode/python");
});
```

#### Menu View (my_menu.html)
Nav with Bootstrap/Material Icons.
```
<ul class="navbar-nav">
  <li class="nav-item">
    <a class="nav-link" href="/controller/{{SET}}/{{NAME}}">
      <i class="material-icons">dashboard</i> Current
    </a>
  </li>
  <li class="nav-item">
    <a class="nav-link" href="/controller/{{SET}}/other">
      <i class="material-icons">list</i> Other
    </a>
  </li>
</ul>
```

#### Body View (my_body.html)
Content with buttons/modals.
```
<div class="container">
  <p>Content for {{SET}}::{{NAME}}.</p>
  <button id="action-btn" class="btn btn-primary">
    <i class="material-icons">play_arrow</i> Click
  </button>

  <!-- Modal -->
  <div class="modal fade" id="exampleModal" tabindex="-1">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Title</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">Content.</div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
          <button type="button" class="btn btn-primary" onclick="submitAction()">Submit</button>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
  $("#action-btn").click(function() {
    var postData = {data: {key: "value"}, data_type: "json"};
    $.ajax({
      url: "/controller/{{SET}}/{{NAME}}/q",
      type: "POST",
      data: JSON.stringify(postData),
      contentType: "application/json",
      success: function(resp) {
        alert("Response: " + JSON.stringify(resp));
      }
    });
  });
  function submitAction() {
    // e.g., apiCall("POST", params, callback);
  }
</script>
```

## Full Workflow Example

1. **Clone Set**: Browser to http://localhost:8000/scriptlet/sets.html > Add (name="myops", URL=git@github.com/user/myops.git, branch=main).
2. **Create Components**:
   - pyops-manager titlebar > Create Scriptlet (set="myops", type="service", name="myservice"); opens pyops/myops/service/myservice.py; use Cline to generate code.
   - Create controller/mycontroller.py (globals: set="myops" service="myservice" ...).
   - Create view/my_head.html, my_menu.html, my_body.html (Bootstrap + apiCall).
3. **Edit/Generate**: Cline prompts for code/logic refinements.
4. **Sync**: Right-click > Write Local to Remote Draft > Save Remote Draft (commit).
5. **Enqueue Service**: Enqueue via web UI (/scriptlet/manager.html POST kind="service") or API /scriptlet/job/item.
6. **Access UI**: http://localhost:8000/controller/myops/mycontroller.
7. **Debug**: Right-click task > Start Debug Job; titlebar Attach to Debug Job for VSCode debugger.
8. **Monitor/Schedule**: pyops-monitor bulk ops; /scriptlet/schedule.


## Sets Manager

PyOps UI: http://{pyops.serverUrl}/scriptlet/sets.html

- List sets/branch; auto-refresh.
- Add: clone (name, Git URL/branch, git name/email, username/password).
- Actions: switch (local branches), remove (confirm).

Safe Git ops with per-set locks; errors in UI.


## Best Practices

- **Self-Contained**: Directives for all deps; validate inputs in services.
- **Error Handling**: Services return {"status": "error", "message": "..."}; tasks use `logger.error()` for errors. Prefer `logging` over `print` for production code to enable level-based filtering and better error tracking.
- **Security**: Sanitize untrusted params/cmds in services.
- **Performance**: Efficient service loops (listener polls ~1s); keep tasks short.
- **Cline Tips**: Provide full context (file + prompt); request iterative changes (e.g., "Refine previous: add logging"); ensure directives commented post-gen.
- **Debug**: Use VSIX debug for quick tests; check logs in monitor.
- **Views**: Rely on Bootstrap defaults; dynamic {{SET}}/{{NAME}}; JS in head only.

## Logging Best Practices

For robust logging in scriptlets, always import and configure the `logging` module at the start:

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
```

- Use `logger.info()` for normal operations, `logger.warning()` for potential issues, `logger.error()` for failures.
- Log exceptions with `logger.exception("Error details")` to include tracebacks.
- Avoid `print()` in production; it doesn't support levels or structured output.
- Logs automatically route to Pyops' stdout.log/stderr.log files for monitoring.

For hands-on implementation or further examples, use VSIX/Cline directly.

## Insights

VSIX-Extension:
VSIX is TypeScript extension for VSCodium & VSCode
; activates on startup & registers providers
; provides pyops-manager tree for sets/scriptlets & pyops-monitor for jobs
; integrates Cline for AI code generation into local mirrors.

Config-Auth:
VSCode workspace settings: "pyops.serverUrl", "pyops.username" (prompts password stored in state)
; "pyops.localPyopsDir": "pyops"
; status bar shows connection (serverUrl required)
; X-User header = username or "anonymous".

Trees-Actions:
ManagerTreeDataProvider lists sets > type folders > scriptlets; icons ✓/⚠/local-only
; right-click: Reset Local/Write Draft/Save Draft/Diff/Delete; Debug (tasks)
; downloads to `pyops/{set}/{type}/{name}.{ext}`.
JobsTreeDataProvider shows jobs by state (todo/running/done/failed)
; enables bulk cancel/download/view/remove & refresh.

Sets-Management:
Sets are Git repos grouping scriptlets
; clone_set via VSIX webview or /scriptlet/sets API with URL/branch
; switch_branch checks out local branch & pull_if_needed syncs
; remove_set deletes dir with confirmation
; locking prevents concurrent Git ops.

Scriptlets-Types:
Scriptlets are Python files executed isolated
; tasks run one-off with output to workspace
; services loop with listener for persistent requests
; libraries provide reusable functions imported via directives
; exec order: param > directives (libraries) > main.

Directives-Deps:
Directives start with `#!` after indent & process pre-exec
; #!module installs pip (e.g., #!requests==2.31.0)
; #!git git@url [branch] clones/pulls repo to sys.path
; #!{::lib_name} loads/execs local library.

Execution-Globals:
glob provides control (Pyops.Control for API: job.list/enqueue, schedule.create)
; listener (ServiceListener for ZMQ IPC: listen blocks 1s, poll non-blocking, response dict)
; workspace is job dir for files/output
; userspace/setspace are parent dirs
; logging via import logging & basicConfig(level=INFO).

Services-Handling:
Services enqueue as kind=service & auto-restart <3 failures
; listener.listen() blocks for msg or TERM
; msg has type (GET/POST), cmd/query params, data (dict/str/bytes) & data_type (json/text/binary/none)
; handle POST data: json.loads if str & json type
; listener.response({"status": "ok/error", "value/message": ...})
; non-blocking: while loop with poll() & time.sleep(0.1) for other work.

Controllers-Views:
Controllers are .py stubs defining set/service/debug/head/menu/body/name
; render GET /controller/{set}/{name} with base HTML + views (Bootstrap/jQuery/Material Icons)
; forward methods to service via /q?cmd=... or POST data
; views are .html fragments: head for <style>/<script> (apiCall util), menu for nav, body for content/modals
; use {{SET}}/{{NAME}} in URLs/AJAX
; resources at /files/: Bootstrap 5 CSS/JS, jQuery, Ace Editor (core/modes/ext-*, dynamic loadAceMode).

Routers-API:
FastAPI routers at /scriptlet/: editor (CRUD code, Jedi completion), inventory (list scriptlets)
; job (enqueue/cancel/remove/list), monitor (jobs/schedules by state), schedule (CRUD cron)
; sets (clone/switch/remove), manager (bulk ops UI)
; scheduler activates cron triggers via runner.

Runner-Jobs:
runner.py daemon polls DB for todo jobs every 5s
; dequeues & executes in data/jobs/{owner}/{set}/{id}/ with venv isolation
; states: todo > dequeued > running (PID) > done/failed/cancelled > remove (drop_done)
; processes directives, logs to stdout.log (INFO+)/stderr.log (ERROR+), ZMQ for services.

Best-Practices:
Use Cline for one-shot code gen with full context/prompts & iterate refinements
; configure logging early with levels & exception for tracebacks
; validate/sanitize inputs in services, return error dicts
; self-contained deps via directives, efficient loops (poll 1s max)
; views: Bootstrap classes for light/dark theme, JS in head, dynamic Ace modes
; debug via VSIX start debug or enqueue debug (draft files, no cleanup)
; monitor via pyops-monitor tree or /scriptlet/monitor.
