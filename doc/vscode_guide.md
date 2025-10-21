# Pyops Developer Guide: Creating and Managing Sets/Scriptlets via VSIX and Cline

This guide is for developers creating and maintaining sets and scriptlets in Pyops using VSCode with the PyOps VSIX extension and tools like Cline for AI-assisted code generation. Pyops enables isolated Python executions for tasks and services, with Git-stored code in remote sets, accessible via HTTP API/UI, and optional scheduling. Jobs run in dedicated workspaces, with owner based on authentication (default "anonymous"). Focus: Coding scriptlets, controllers, and views in local mirrors via VSIX, syncing to remote sets for activation. Assume Pyops server is running (e.g., via `run.sh`), with web UI at the configured bind (e.g., http://localhost:8000).

## Introduction

Pyops allows set developers to build reusable operations: scriptlets for tasks/services, controllers for UI routing, views for HTML fragments. Code is stored in remote sets (logical groups, e.g., "myops"). Use the PyOps VSIX extension in VSCode for tree views, commands, and local editing mirrors (.pyops/ dir). Integrate Cline (or similar LLM extension) for one-shot code generation into files based on prompts.

### VSIX Installation and Configuration
- Install: In VSCode, go to Extensions view > ... > Install from VSIX > select file.
- Configure: Add `.pyops.yaml` in workspace root:
  ```
  serverUrl: http://localhost:8000  # Pyops server
  localPyopsDir: .pyops  # Local mirror dir (default .pyops)
  autoRefreshInterval: 10  # Tree refresh in seconds
  ```
- If required: Add `.pyops.current user_name.yaml`
  ```
  auth: http-basic  # Or other scheme
  user: your_username
  key: you_token
  ```
- Status: Check connection in VSCode status bar.

VSIX provides:
- **pyops-manager**: Tree for sets > scriptlets (by type: task/library/controller/view) with icons; right-click/toolbar for create/reset/upload/save/diff/delete/debug.
- **pyops-monitor**: Jobs tree by state (todo/running/done/failed); actions: copy param/cancel/download/delete/view/bulk operations.
- **Webviews**: UI for Sets Manager (clone/switch/remove sets).

Local `.pyops/` mirrors remote files for editing (create opens file; upload/save syncs changes).

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
  logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
  logger = logging.getLogger(__name__)
  ```
  Output routes to stdout.log (INFO and above) and stderr.log (ERROR and CRITICAL). For simple debugging, `print()` works but lacks levels and formatting.
  - Job workspace: Dir for outputs/files (abstract; access via `workspace` var in code).
- **Dependency Directives**: Line-start (after indent), processed before exec (in param/libraries/main). No recursion.
  - Pip: `!module_name` (e.g., `!requests` or `!numpy==1.21`); installs quietly in isolated venv.
  - Git Repo: `!repopath [branch]` (e.g., `!git@github.com/user/myrepo.git main`); clones/pulls, prepends to sys.path.
  - Local Library: `!{::lib_name}` (own set) or `!{otherset::lib_name}` (foreign); loads/execs.
  - Post-process: Commented out (e.g., `# !requests`); errors fail job.
- **Param**: Optional pre-exec code string (enqueue via UI/API; supports directives). Vars/functions available in main.
- **Debug Mode**: Enqueue as "debug" (uses draft files, no auto-cleanup).
- **Enqueue/Run**: Via VSIX monitor or API (POST /scriptlet/job/item {set, name, kind="task"/"service"/"debug", param, drop_done}).
- **Scheduling**: POST /scriptlet/schedule/item {set, name, kind="task", schedule="cron_expr" (e.g., "* * * * *"), param, drop_done}.
- **Monitoring**: VSIX pyops-monitor or /scriptlet/monitor for jobs/schedules by state. Download logs/files.
- **API Access**: For services/controllers: HTTP to /controller/{set}/{name}/q (methods forward as dict {type, params/body}).

## VSIX Workflow

- **Create Scriptlet**: Right-click in pyops-manager > Create > select type/set/name; opens local `.pyops/{set}/{type}/{name}.{ext}` (e.g., .py for task/library/controller, .html for view).
- **Edit/Generate**: Use VSCode/Cline to code (see Coding with Cline). Edit local file.
- **Sync**: Right-click > Upload (to draft) or Save (commit to remote set, activates).
- **Diff/Reset**: Right-click > Diff (local vs remote) or Reset (pull remote to local).
- **Debug**: Right-click > Start Debug (runs job, shows output in channels) or Stop.
- **Delete**: Right-click > Delete (remote).
- **Enqueue/Monitor**: Use pyops-monitor tree for actions (enqueue via right-click, view/cancel/download jobs).
- No auto-sync: Manual steps ensure control.

## Execution environment
Execution environment provides a set of globals
- `userspace`: user directory path containings set subdirectories
- `setspace`: set directory containing job subdirectories
- `workspace`: the jobs workspace directory (default current working path)
- `listener`: listener a task could use to service a controller, `listener.listen()` blocks.

## Scriptlet Types

Precreate via VSIX; exec order: param → libraries (directives) → main.

### Tasks (One-Off)
Use: Run code once, produce output/files.

Template (`task/{name}.py`):
```
# Task: One-off execution
!requests  # Install requests if missing
!git@github.com/user/myrepo.git main  # Clone/pull myrepo

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
!pandas
import pandas as pd
df = pd.DataFrame({"col": [1, 2]})
MY_DF = df  # Available in main
```

Run: Enqueue kind="task" via VSIX/API; outputs to workspace/logs; drop_done auto-removes.

### Services (Persistent)
Services are a special kind of task looping a service listener.
Use: Handle requests from controllers/API (e.g., /q endpoints).

Request: `listener.listen()` returns dict:
- "type": "GET"/"POST"/"PUT"/"DELETE".
- Query params: Direct keys (e.g., ?cmd=run → msg["cmd"] = "run").
- Body (POST/PUT/DELETE): "data" (dict/JSON str/text/bytes/none); "data_type" ("json"/"text"/"binary"/"none").
- From controllers: Body as direct payload dict in "data".
- Respond: `listener.response(result_dict)` (e.g., {"status": "ok", "value": ...}).
- "TERM": Shutdown.

Template (`task/{name}.py`):
```
# Service: Persistent, handles requests
!flask  # Install Flask for internal use
!git@gitlab.com/team/utils.git  # Clone utils

import time
from flask import jsonify  # Available after install
import utils.api  # From cloned repo

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

# Setup (runs once)
logger.info("Service started")

# Main loop: Poll for requests
while True:
    msg = listener.listen()  # Dict: {"type": "GET", "cmd": "value", ...} or {"type": "POST", "data": {...}, "data_type": "json"}
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

Run: Enqueue kind="service" via VSIX/API; auto-restarts (<3 failures). Requests routed from UI/API.

### Libraries (Reusable)
Use: Shared functions, imported via directives.

Template (`library/{lib_name}.py`):
```
# Library: Pure code, no directives/loops
!numpy  # Install if needed

import numpy as np

def helper_func(x):
    return np.array(x) * 2

# Available after !{::lib_name} import
```

Usage: In scriptlet/param: `!{::lib_name}` (own set) or `!{otherset::lib_name}` (foreign); replaces with comment after load/exec.

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
- API: /{set}/{name}/q + method (forwards to service).
- Menu: GET /menu lists controllers (uses "name").

In views, use {{SET}}/{{NAME}} for dynamic routes ({{NAME}} = basename, e.g., 'os-dashboard-controller').

### Views (HTML Fragments)
Precreate `view/{name}.html` via VSIX (committed only; raw HTML).

Guidelines:
- Global: Bootstrap 5 with automatic light/dark theme switching based on system preference (prefers-color-scheme), jQuery, Material Icons (e.g., `<i class="material-icons">add</i>`). No manual toggle buttons are provided; themes switch automatically.
- Use Bootstrap classes like `bg-light`/`bg-dark`, `text-light`/`text-dark`, `table-secondary`/`table-dark`, `btn-outline-secondary`, etc., for consistent theming across light and dark modes.
- No `<title>` or `<h1>` (auto-handled).
- JS `<script>` only in head; custom in head.html.
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

1. **Clone Set**: In Sets Manager webview > Add > name="myops", URL=git@github.com/user/myops.git, branch=main.
2. **Create Components**:
   - VSIX pyops-manager > right-click myops > Create > service > "myservice" (opens .pyops/myops/task/myservice.py; use Cline to generate template code).
   - Create controller "mycontroller" (generate globals routing to myservice).
   - Create views: head/menu/body (generate HTML with {{SET}}/{{NAME}}, apiCall).
3. **Edit/Generate**: Use Cline prompts to fill code (e.g., add handlers in service).
4. **Sync**: Right-click files > Save (commits to remote set).
5. **Enqueue Service**: pyops-monitor > right-click myservice > Enqueue kind="service".
6. **Access UI**: Browser to /controller/myops/mycontroller (renders views; interactions route to service via /q).
7. **Monitor/Debug**: pyops-monitor for jobs; right-click > Start Debug for live output.
8. **Schedule/Task**: Enqueue tasks or schedules via monitor/UI.

## Sets Manager

Access: Scriptlet menu > Sets or folder icon in manager.

- **Clone**: Add button > modal: name, Git URL, branch (default main), git config. Clones remote set; adds .gitignore/README if needed.
- **List**: Table: name/branch; auto-refresh 10s. Actions: delete (confirm), switch branch (select from local branches via API).
- **Switch**: Updates checked-out branch.
- **Remove**: Deletes remote set dir.

Uses locks for safe Git ops; errors in modals/logs.

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
