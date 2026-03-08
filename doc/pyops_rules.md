# PyOps Scriptlet Development Rules

## Core Concepts

- **Sets**: Remote repositories grouping scriptlets (e.g., "myops").
- **Scriptlets**: Python files for execution:
  - **Tasks**: One-off runs (e.g., data fetch/process).
  - **Services**: Persistent handlers for requests (e.g., from UI/API).
  - **Libraries**: Reusable code (imported via directives).
- **Controllers**: Python stubs defining UI structure and routing to services (e.g., /controller/{set}/{name}).
- **Views**: HTML fragments (head for styles/JS, menu for nav, body for content) rendered by controllers.
- **Markdown**: Documentation files in sets.
- **JS Libraries**: JavaScript modules for client-side code.
- **Execution Model**: Jobs execute in isolated per-set venv subprocesses with global dict (`glob`) containing:
  - `control`: Pyops.Control for API calls (e.g., `control.job.list()` returns dict of jobs).
  - `listener`: For services (inbound requests; see Services).
  - `"__name__": "__main__" (enables `if __name__ == "__main__":` idiom: executes main logic in PyOps jobs; skips when imported locally for pytest testing without running main block).`
  - Logging: Use Python's built-in `logging` module for structured logging. Configure it early:
    ```python
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    ```
    Output routes to stdout.log (INFO and above) and stderr.log (ERROR and CRITICAL). For simple debugging, `print()` works but lacks levels and formatting.
  - Job workspace: Dir for outputs/files (abstract; access via `workspace` var in code).
- **Dependency Directives**: Line-start (after indent), processed before exec (in param/libraries/main). No recursion.
  - Pip: `#!module_name` (e.g., `#!requests` or `#!numpy==1.21`); installs quietly in isolated venv.
  - Git Repo: `#!` + git URL (e.g., `#!git@github.com/user/myrepo.git` or `#!https://github.com/user/myrepo.git`); clones/pulls, prepends to sys.path. Optional branch as second argument.
  - Local Library: `#!{::lib_name}` (own set) or `#!{otherset::lib_name}` (foreign); loads/execs.
  - Post-process: Commented out (e.g., `# #!requests`); errors fail job.
- Directive Logic: Types distinguished by argument pattern:
  - Git: Contains '://' or starts with 'git@'
  - Pip: Plain string not starting with '{' and not containing '::'
  - Library: Starts with '{' or contains '::'
- **Param**: Optional pre-exec code string (supports directives). Vars/functions available in main.


## Execution environment

Execution environment provides these globals:
- `userspace`: `data/jobs/{owner}/` (owner's jobs root)
- `setspace`: `data/jobs/{owner}/{set}/` (set's jobs root)
- `workspace`: `data/jobs/{owner}/{set}/{id}/` (this job's workspace)
- `listener`: ServiceListener for inbound ZMQ IPC (services only). `listener.listen()` blocking; `listener.poll()` non-blocking (None/"TERM"/msg); `listener.async_poll()` returns list of pending messages; `response(dict)` or `async_response(identity, dict)`.

## Scriptlet Types

Exec order: param → libraries (directives) → main.

### Tasks (One-Off)
Use: Run code once, produce output/files.

Template (`task/{name}.py`):
```python
# Task: One-off execution
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# #!requests  # Install requests if missing
# #!git@github.com/user/myrepo.git main  # Clone/pull myrepo

import os
import requests  # Now available
import myrepo.helper  # From cloned repo

def main():
    # Access control and workspace
    # control: e.g., control.job.list()
    # workspace: job output dir

    # Write output
    output_path = os.path.join(workspace, "result.txt")
    with open(output_path, "w") as f:
        f.write("Task completed")

    # Example: API call via control
    jobs = control.job.list()  # Returns dict

    logger.info("Task done")

if __name__ == "__main__":
    main()
```


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

Asynchronous handling: `listener.async_poll()` returns list of (identity, message) tuples for all pending requests. Respond to each with `listener.async_response(identity, result_dict)` for concurrent processing.

Template (`service/{name}.py`):

#### Blocking Example (listen() - Simple Loops)
```python
# Service: Persistent, handles requests (blocking listener)
# #!flask  # Install Flask for internal use
# #!git@gitlab.com/team/utils.git  # Clone utils

import time
import utils.api  # From cloned repo

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

def main():
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

if __name__ == "__main__":
    logger.info("Service started")
    main()
    logger.info("Service stopped")
```

#### Non-Blocking Example (poll() - Responsive Loops)
```python
# Service: Persistent, handles requests (non-blocking poll)
# #!git@gitlab.com/team/utils.git  # Clone utils

import time
import utils.api  # From cloned repo

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

def main():
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

if __name__ == "__main__":
    logger.info("Service started")
    main()
    logger.info("Service stopped")
```

#### Asynchronous Example (async_poll() - Concurrent Requests)
```python
# Service: Persistent, handles concurrent requests (asynchronous poll)
import asyncio
import time

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

# Stationary dict for pending messages: identity -> message
pending_messages = {}

# Main loop: Asynchronous poll for concurrent requests
def main():
    while True:
        # Poll for all pending messages
        pending_messages.update(dict(listener.async_poll()))

        if not pending_messages:
            # No pending messages; perform other work
            time.sleep(0.01)  # Small delay to prevent busy-waiting
            continue

        # Process each message immediately
        for identity, msg in list(pending_messages.items()):
            if msg == "TERM":
                return

            request_type = msg.get("type")
            cmd = msg.get("cmd")
            data = msg.get("data", {})

            if request_type == "GET":
                if cmd == "status":
                    result = {"status": "ok", "message": "Service running"}
                else:
                    result = {"status": "error", "message": "Unknown command"}
            elif request_type == "POST":
                # Process data concurrently
                if cmd == "process":
                    result = {"status": "ok", "processed": len(str(data))}
                else:
                    result = {"status": "error", "message": "Unknown command"}
            else:
                result = {"status": "error", "message": "Unsupported type"}

            # Send response to specific client and remove from pending
            listener.async_response(identity, result)
            del pending_messages[identity]

if __name__ == "__main__":
  logger.info("Service started")
  main()
  logger.info("Service stopped")
```


### Libraries (Reusable)
Use: Shared functions, imported via directives.

Template (`library/{lib_name}.py`):
```python
# Library: Pure code, no directives/loops
# #!numpy  # Install if needed

import numpy as np

def helper_func(x):
    return np.array(x) * 2

# Available after #!{::lib_name} directive (executed in global namespace)
```

Usage: In scriptlet/param: `#!{::lib_name}` (own set) or `#!{otherset::lib_name}` (foreign); replaces with comment after load/exec.

### JS Libraries (Client-Side)
Use: JavaScript modules for client-side code, served to browsers.

Template (`jslibrary/{name}.js`):
```javascript
// JS Library: Client-side module
console.log('Hello from JS lib');

```


## Controllers and Views

### Controllers (UI Stubs)
Use: Define UI and route to service.

Create `controller/{name}.py`.

Template:
```python
# Controller: Defines UI and routes to service
set = "myops"                  # Your set
service = "myservice"          # Target service name
debug = False                  # True for draft code
head = "my_head"               # Head view name
menu = "my_menu"               # Menu view name
body = "my_body"               # Body view name
name = "My Controller Title"   # Menu display
```

In views, use {{SET}}/{{NAME}} for dynamic routes ({{NAME}} = basename, e.g., 'os-dashboard-controller').

### Views (HTML Fragments)
Create `view/{name}.html` (raw HTML).

Guidelines:
- Global: Bootstrap 5 with automatic light/dark theme switching based on system preference (prefers-color-scheme), jQuery, Material Icons (e.g., `<i class="material-icons">add</i>`). No manual toggle buttons are provided; themes switch automatically.
- Use Bootstrap classes like `bg-light`/`bg-dark`, `text-light`/`text-dark`, `table-secondary`/`table-dark`, `btn-outline-secondary`, etc., for consistent theming across light and dark modes.
- No `<title>` or `<h1>` (auto-handled).
- JS `<script>` preferably in head; inline in body ok for simple scripts. Custom in head.html.
- Use {{SET}}/{{NAME}} in URLs/AJAX for portability.

#### Head View (my_head.html)
Styles/scripts only.
```html
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
```html
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
```html
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

## Best Practices

- **Self-Contained**: Directives for all deps; validate inputs in services.
- **Error Handling**: Services return {"status": "error", "message": "..."}; tasks use `logger.error()` for errors. Prefer `logging` over `print` for production code to enable level-based filtering and better error tracking.
- **Security**: Sanitize untrusted params/cmds in services.
- **Performance**: Efficient service loops (listener polls ~1s); keep tasks short.
- **Views**: Rely on Bootstrap defaults; dynamic {{SET}}/{{NAME}}; JS in head only.

### Local Testing with Pytest

The runner sets `"__name__": "__main__"` in `glob`, enabling scriptlets to be structured for both PyOps execution and local testing:

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Directives and imports...

def main():
    # Task/service logic here
    logger.info("Execution complete")

if __name__ == "__main__":
    main()
```

**How it works**:
- PyOps jobs: `__name__ == "__main__"`, runs `main()`.
- Local import: `__name__ = "myops.task.mytask"`, skips `main()`; test functions directly.

**Pytest Example** (`tests/myops/test_mytask.py` or local `pyops/tests/`):
```python
import pytest
from myops.task.mytask import main  # No main() execution!

def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert "Execution complete" in captured.out
```

Run: `pytest -v` (mock `control`/`listener`/`workspace` as needed for services).


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