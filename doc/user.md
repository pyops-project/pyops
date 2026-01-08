# Pyops User Documentation: Building and Running Operations

This guide provides a complete coding model for end-users to create and execute operations (scriptlets for tasks/services, controllers for UI, views for HTML) in Pyops. Assumes Pyops is installed and running (via `run.sh` or `run_dev.sh`), with web UI at the configured bind (e.g., http://localhost:8000) and runner polling the job queue. Focus: Generating code into precreated files in Git repos under `data/git/{set}/` (where `{set}` is your logical group, e.g., "myops"). Commit and push changes to activate.

Pyops enables isolated Python executions (tasks/services) with Git-stored code, HTTP API/UI access, and optional scheduling. Jobs run in dedicated workspaces (`data/jobs/{owner}/{set}/{job_id}/`), with owner from X-User header ("anonymous" default). Edit `etc/pyops.json` for paths (data/git, data/jobs), web bind, logging; restart to apply.

## Core Concepts
- **Storage and Activation**: Code in Git repos (`data/git/{set}/`). Files: `{name}.{type}.py` (tasks/libraries/controllers/services), `{name}.html` (views). Edit in VSCode, commit/push to repo (unstaged for debug mode, committed for production). Repos auto-synced periodically.

- **Manual Set Management**: Sets are Git repositories in `data/git/{set}/`. To create manually:
  - Clone or init: `git clone <url> data/git/{set}/` or `git init data/git/{set}/`.
  - For private repositories, configure authentication (e.g., using a token via `.git/credentials` or SSH keys).
  - Add files: e.g., `{name}.task.py` for tasks, `{name}.library.py` for libraries, `{name}.controller.py` for UI, `{name}.html` for views.
  - Commit and push: `git add . && git commit -m "Initial set" && git push` to activate changes.
  - Monitor: Use /scriptlet/monitor for jobs; external sets like "system" provide optional automation (e.g., clone/remove via service).

- **Execution Model**: Scriptlets exec in isolated global dict (`glob`) with:
  - Paths: `userspace` (owner dir), `setspace` ({set} dir), `workspace` (job dir).
  - `control`: pyops.Control for API calls (e.g., `control.job.list()` returns dict).
  - `listener`: For services (inbound requests; see Services section).
  - Logging: `print` or `logging` to stdout.log (INFO), stderr.log (errors/warnings).
- **Dependency Directives**: Processed before exec (in param/libraries/main code). Line-start (after optional indent), no recursion.
  - Pip: `!module_name` (e.g., `!requests` or `!numpy==1.21`). Installs -q in isolated venv if missing (checks `pip list --freeze`).
  - Git Repo: `!repopath [branch]` (e.g., `!git@github.com/user/myrepo.git main` or `!https://github.com/user/myrepo.git`). Clones/pulls to `runner.pkg/{sanitized_name}` (hyphens/versions → underscores); optional branch checkout. Prepends to sys.path for imports.
  - Post-process: Commented (e.g., `# !requests`); errors set job to "failed".
- **Param**: Optional pre-exec code string (enqueue via UI/API; supports directives). Vars/functions available in main code.
- **Debug Mode**: Enqueue with kind="debug" (uses unstaged files, no auto-cleanup).
- **Enqueue/Run**: UI (/scriptlet/manager for jobs, /scriptlet/scheduler for cron) or API (POST /scriptlet/job/item {set, name, kind="task"/"service"/"debug", param (optional string), drop_done (bool: auto-remove done tasks)}).
- **Scheduling**: POST /scriptlet/schedule/item {set, name, kind="task", schedule="cron_expr" (e.g., "* * * * *"), param, drop_done}. Activate/deactivate by ID.
- **Monitoring**: /scriptlet/monitor lists jobs/schedules by state (todo/dequeued/running/done/failed/cancel/remove). Download logs/files from workspace.
- **API Access**: HTTP methods to /controller/{set}/{name}/q (forwards to service as dict {type: "GET"/"POST"/etc, params/body}). Auth: X-User header.

## Creating and Running Scriptlets (Tasks/Services)
Scriptlets are Python files for one-off tasks or persistent services. Precreate `{name}.task.py` (tasks), `{name}.service.py` (services), or `{name}.debug.py` (debug). Exec order: param → libraries (via directives) → main code.

### Tasks (One-Off Executions)
- **Use Case**: Run code once, produce output/files.
- **Template** ({name}.task.py):
  ```
  # Task: One-off execution
  !requests  # Install requests if missing
  !git@github.com/user/myrepo.git main  # Clone/pull myrepo to pkg/myrepo

  import os
  import requests  # Now available
  import myrepo.helper  # From cloned repo

  # Access paths/control
  # userspace: /path/to/data/jobs/owner/
  # setspace: .../owner/set/
  # workspace: .../set/job_id/
  # control: e.g., control.job.list()

  # Example: HTTP request
  response = requests.get("https://api.example.com/data")
  data = response.json()

  # Write output
  output_path = os.path.join(workspace, "result.txt")
  with open(output_path, "w") as f:
      f.write(str(data))

  # Example: API call via control
  jobs = control.job.list()  # Returns dict

  print("Task done")  # To stdout.log
  ```
- **Param Example** (string in enqueue):
  ```
  # Pre-setup with deps
  !pandas
  import pandas as pd
  df = pd.DataFrame({"col": [1, 2]})
  MY_DF = df  # Available in main
  ```
- **Running**: Enqueue kind="task". Outputs to workspace/logs; drop_done=True auto-removes.

### Services (Persistent/Background)
- **Use Case**: Long-running, handles requests from controllers/API (e.g., via /q endpoints).
- **Request Handling**: `listener.listen()` returns dict:
  - "type": "GET"/"POST"/"PUT"/"DELETE".
  - Query params: Direct keys (e.g., ?cmd=run → msg["cmd"] = "run").
  - Body (POST/PUT/DELETE): "data" (dict for JSON, str for text, bytes for binary, None for none); "data_type" ("json"/"text"/"binary"/"none").
  - When requests originate from controllers via /q endpoints, the body is forwarded as msg['data'] containing the direct payload dict (e.g., {"action": "switch", "name": "..."}); access keys directly under data.
  - Respond: `listener.response(result_dict)` (e.g., {"status": "ok", "value": ...}).
  - "TERM": Shutdown signal.
- **Template** ({name}.service.py):
  ```
  # Service: Persistent, handles requests
  !flask  # Install Flask for internal use
  !git@gitlab.com/team/utils.git  # Clone utils to pkg/utils

  import time
  from flask import jsonify  # Available after install
  import utils.api  # From cloned repo

  # Access paths/control/listener
  # userspace: /path/to/data/jobs/owner/
  # setspace: .../owner/set/
  # workspace: .../set/job_id/
  # control: e.g., control.job.list()
  # listener: for inbound requests

  # Setup (runs once)
  print("Service started")

  # Main loop: Poll for requests
  while True:
      msg = listener.listen()  # Dict: {"type": "GET", "cmd": "value", ...} or {"type": "POST", "data": {...}, "data_type": "json"}
      if msg == "TERM":
          break  # Graceful shutdown

      request_type = msg.get("type")
      cmd = msg.get("cmd")  # Query param "cmd"
      other_param = msg.get("other")  # Another param

      if request_type == "GET":
          if cmd == "fetch":
              result_value = utils.api.fetch_external(other_param)
              result = {"status": "ok", "value": result_value}
          else:
              result = {"status": "error", "message": "Unknown cmd"}
      elif request_type in ["POST", "PUT"]:
          data = msg.get("data", {})  # Body data; direct payload from controller requests
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

  print("Service stopped")
  ```
- **Running**: Enqueue kind="service". Auto-restarts on failure (<3 times). Requests from controllers/API.

### Libraries (Reusable Code)
- **Use Case**: Shared functions (imported via directives).
- **Template** ({lib_name}.library.py):
  ```
  # Library: Pure code, no directives/loops
  !numpy  # Install if needed

  import numpy as np

  def helper_func(x):
      return np.array(x) * 2

  # Available after !{} import
  ```
- **Usage**: In scriptlet/param: `!{::lib_name}` for own set, `!{otherset::lib_name}` for foreign set (loads/execs, replaces with comment). Note: The braces '{' and '}' are literal characters in the syntax, not placeholders.

## Creating and Running Controllers (UI Stubs)
- **Use Case**: Define UI structure and route requests to a service/scriptlet.
- **Precreate**: `{name}.controller.py` in {set} repo (committed only).
- **Template**:
  ```
  # Controller: Defines UI and routes to service
  set = "myops"                  # Your set
  service = "myservice"          # Target scriptlet/service name
  debug = False                  # True for unstaged code
  head = "my_head"               # Head view name
  menu = "my_menu"               # Menu view name
  body = "my_body"               # Body view name
  name = "My Controller Title"   # Optional menu display
  ```
- **Running**:
  - UI: GET /controller/{set}/{name} (where {name} is the basename of the .controller.py file, e.g., 'os-dashboard-controller' for os-dashboard-controller.controller.py; renders base HTML + views; Bootstrap/jQuery/Material Icons).
  - API: /{set}/{name}/q + method (GET/POST/PUT/DELETE). Query + body → service as dict. X-User for owner.
  - Menu: GET /menu lists controllers (uses "name" global for display title).
  - **Note**: In views, use {{SET}} and {{NAME}} placeholders for dynamic routes ({{NAME}} expands to the full basename like 'os-dashboard-controller'). This ensures URLs resolve correctly to the controller's endpoint.

## Creating and Running Views (HTML Fragments)
- **Use Case**: UI sections for controllers (head/menu/body).
- **Precreate**: `{name}.html` in {set} repo (committed only; raw HTML).

### Guidelines
- Pyops provides Bootstrap 5, jQuery, and Material Icons globally. Use Bootstrap classes (e.g., `btn btn-primary`, `modal-dialog`, `table`) and Material Icons (e.g., `<i class="material-icons">add</i> Clone</button>`) directly in views. Avoid including custom CSS/JS for core UI elements like modals, tables, or navigation; rely on Bootstrap defaults to reduce bundle size and ensure consistency.
- Strictly omit any `<title>` tags or explicit titles (e.g., `<h1>`) in head or body views, as Pyops handles page titles automatically.
- JavaScript <script> blocks are only allowed in the head view. Place all custom scripts (e.g., utility functions, event handlers, AJAX calls) in head.html to ensure proper loading and separation of concerns.

### Head View (my_head.html)
Custom styles and scripts only; no content or titles.

**Important: Use placeholders {{SET}} (replaced with the controller's set, e.g., 'system') and {{NAME}} (replaced with the controller name from the .controller.py basename, e.g., 'os-dashboard-controller') for dynamic URLs to ensure portability across sets and controllers. Hardcoding routes will break when the controller name or set changes.**

```
<style>
  .custom-head { color: blue; }
</style>
<script>
  // Utility functions, e.g., apiCall for AJAX
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
Note: Title is auto-generated by Pyops. Always use {{SET}}/{{NAME}} in href/src for links, AJAX, etc., to match the controller's route (/controller/{set}/{name}).

### Menu View (my_menu.html)
Navigation items using Bootstrap navbar classes; include Material Icons for better UX. Use {{SET}}/{{NAME}} for self-references or other controllers.
```
<ul class="navbar-nav">
  <li class="nav-item">
    <a class="nav-link" href="/controller/{{SET}}/{{NAME}}">
      <i class="material-icons">dashboard</i> Current Controller
    </a>
  </li>
  <li class="nav-item">
    <a class="nav-link" href="/controller/{{SET}}/other">
      <i class="material-icons">list</i> Other Action
    </a>
  </li>
</ul>
```

### Body View (my_body.html)
Main content container; dynamic placeholders, buttons, modals with Bootstrap.
```
<div class="container">
  <p>Dynamic content for {{SET}}::{{NAME}}.</p>
  <button id="action-btn" class="btn btn-primary">
    <i class="material-icons">play_arrow</i> Click Me
  </button>

  <!-- Bootstrap Modal Example -->
  <div class="modal fade" id="exampleModal" tabindex="-1">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Modal Title</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          Modal content here.
        </div>
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
    // POST to /q (routes to service) with JSON body
    var postData = {data: {key: "value"}, data_type: "json"};
    $.ajax({
      url: "/controller/{{SET}}/{{NAME}}/q",
      type: "POST",
      data: JSON.stringify(postData),
      contentType: "application/json",
      success: function(resp) {
        alert("Response: " + JSON.stringify(resp));  // Dict from service
      }
    });
  });

  function submitAction() {
    // Handle modal submit, e.g., apiCall("POST", params, callback);
  }
</script>
```

## Integration Example: Full Component Set
- **Files in {set="myops"}**:
  - myservice.service.py: Service template (handles GET ?cmd=fetch, POST data JSON).
  - mycontroller.controller.py: Controller template (service="myservice", views="my_head", "my_menu", "my_body").
  - my_head.html, my_menu.html, my_body.html: View templates (JS POST to /q with {data: {action: "do"}}).
- **Workflow**:
  1. Generate/commit files to repo.
  2. Enqueue service: kind="service", name="myservice" (UI/API).
  3. Access UI: /controller/myops/mycontroller (renders views; JS sends ?cmd=fetch or POST data → service processes → response dict displayed).
  4. Monitor: /scriptlet/monitor for job states/logs.

## Quick Start Workflow
1. **Setup Repo**: Create {set} in data/git (git init if needed); precreate files (e.g., via VSCode or API editor /scriptlet/editor).
2. **Generate Code**: Use templates above; add directives for deps.
3. **Commit/Push**: `git add . && git commit -m "Update" && git push` (activates).
4. **Enqueue/Schedule**: UI (/manager, /scheduler) or API (POST /job/item or /schedule/item).
5. **Interact/Monitor**: UI (/controller/... for pages, /monitor for jobs). Download logs from workspace.
6. **Debug**: Enqueue kind="debug"; check unstaged files/logs.

## Sets Manager

The Sets Manager (/scriptlet/sets) allows managing Git repositories (sets) for scriptlets: cloning from URL, listing with branches, switching branches, and removing.

- **Access**: From Scriptlet menu > Sets, or via folder icon in Manager nav.
- **Clone Set**: Click "Add" button; modal for name, Git URL, branch (default: main), optional git name/email (sets local config). Clones to data/git/{name}; auto-adds .gitignore (*.bck) and README.md if needed.
- **List Sets**: Table shows name and current branch; auto-refreshes every 10s. Actions: delete (confirm), switch branch (modal with local branches fetched via API).
- **Switch Branch**: Select set, choose from local branches (git branch); updates checked-out branch.
- **Remove Set**: Confirm deletion of data/git/{name} directory.

All operations use repo_lock for serialized Git calls, preventing interference. Errors shown in modals/logs.

## Best Practices
- **Self-Contained**: Use directives for deps; avoid global assumes. Note: In debug mode, raw directive lines (e.g., !{::lib}) may cause linter errors in editors but are processed at runtime before execution.
- **Error Handling**: In services, return {"status": "error", "message": "..."}; tasks print to stdout.
- **Security**: Services handle untrusted params; validate data/cmd.
- **Performance**: Services loop efficiently (listener polls 1s); tasks keep short.
- **Cleanup**: drop_done=True for tasks; services auto-manage.

For hands-on file generation or examples, toggle to Act mode.
