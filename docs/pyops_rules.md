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
- **Names**: set and scriptlet names use only letters, digits, `_` and `-` (`[A-Za-z0-9_-]+`); the server answers 400 for any other name, and files with other names are not scriptlets (not listed, not importable).
- **Users**: every pyops user is an administrator. There is no per-user isolation: every user sees and can act on every set, scriptlet, schedule and job, and every job's workspace is readable by every job. A view that browses `jobsspace` or `setspace` shows all users' job output to every visitor — by design, not a leak to guard against.
- **Execution Model**: Jobs execute in isolated per-set venv subprocesses with global dict (`glob`) containing:
  - `control`: Pyops.Control for API calls (e.g., `control.job.list()` returns dict of jobs).
  - `listener`: ServiceListener for inbound requests (services only; see Services).
  - `"__name__"`: Set to `"__main__"`, enabling the `if __name__ == "__main__":` idiom: executes main logic in PyOps jobs; skips when imported locally for pytest testing without running main block.
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
- `user`: owner of this job (string; identifies the requesting user)
- `setname`: set this scriptlet belongs to (string)
- `name`: scriptlet name (string)
- `kind`: this job's execution kind (`task`/`service`/`draft`/`debug`/`test`/`testdebug`). `control.job.create(...)` reads it to default a spawned child's kind: a working-tree parent (`draft`/`debug`/`test`/`testdebug`) yields a `draft` child, anything else a `task`. Pass `kind=` to `create` to override.
- `setspace`: `data/jobs/{setname}/` (set's jobs root; shared across all users)
- `workspace`: `data/jobs/{setname}/{id}/` (this job's workspace)
- `jobsspace`: `data/jobs/` (jobs root of all sets; every job's workspace is `{jobsspace}/{set}/{id}/`)
- `gitspace`: `data/git/` (the sets' working trees, `{gitspace}/{set}/`)
- `pkgspace`: `data/pkg/` (git-installed packages of `#!<git-url>` directives, `{pkgspace}/{set}/{repo}/`)
- `data`: set-scoped YAML/JSON files under `data/git/{setname}/data/` with shared/exclusive locks (`data.read(name)`, `data.write(name)`, `data.list()`; see "Set-Scoped Data" in the pyops guide)
- `control`: `pyops.Control` for API calls (see Core Concepts)
- `listener`: ServiceListener for inbound ZMQ IPC (services, debug, and draft jobs). `listener.listen()` blocking; `listener.poll()` non-blocking (None/"TERM"/msg); `listener.async_poll()` returns list of pending messages; `response(dict)` or `async_response(identity, dict)`.

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
#!requests
#!git@github.com/user/myrepo.git main

import os
import requests  # Available after #!requests directive
import myrepo.helper  # Available after git directive

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
Use: Handle requests from controllers/API via REST-style endpoints.

A service schedule (empty cron string) starts its service only while none of its jobs is active, cancelled or done. A cancelled service therefore stays stopped — the operator's cancel is a decision, not a crash — and so does one that ended `done` without `drop_done`. To start it again, e.g. to deploy new code, delete that job (Monitor, `pyopsctl job delete`); the next scheduler pass starts the service. A failed service is restarted until `max_fail_n` of its jobs have failed (see "Schedules" in the pyops guide).

Request: `listener.listen()` returns dict with:
- `"type"`: HTTP method (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, `"PATCH"`, etc.)
- `"path"`: URL path after `/controller/{set}/{name}/` (e.g., `"/fetch"`, `"/api/models"`, `"/q"` for legacy)
- `"data"`: Request body — parsed JSON object, text string, bytes, or `None`
- `"data_type"`: `"json"`, `"text"`, `"binary"`, or `"none"`
- `"user"`: Requesting user (from `X-User` header)
- Query params as direct keys (e.g., `?target=foo` → `msg["target"] = "foo"`)
- `"TERM"`: Shutdown signal (string, not dict).
- Respond: `listener.response(result_dict, status=200)` — the controller proxy answers the caller with this status and body unchanged, e.g. `listener.response({"error": "bad input"}, 400)` reaches the client as 400 `{"error": "bad input"}` (a status that allows no body, e.g. 204, arrives without one). If the service does not answer within `web.timeout`, the proxy answers 504.

**REST-style endpoints** (recommended): Use URL path for routing.
- `GET /controller/{set}/{name}/items?limit=10` → `path="/items"`, `msg["limit"]="10"`
- `POST /controller/{set}/{name}/items` (JSON body) → `path="/items"`, `msg["data"]={...}`

**Legacy query-style**: `GET /controller/{set}/{name}/q?cmd=xxx` → `path="/q"`, `msg["cmd"]="xxx"`.

Asynchronous handling: `listener.async_poll()` returns list of (identity, message) tuples for all pending requests. Respond to each with `listener.async_response(identity, result_dict)` for concurrent processing.

**Shutdown contract** — when SIGTERM lands, the listener's signal
handler flips `kill_now`. `async_poll()` then returns a single
`[(b"", "TERM")]` sentinel on the next call, matching `listen()`'s
`"TERM"` return. Causal-style tick loops must check for `msg == "TERM"`
in their poll result and exit promptly; otherwise the runner SIGKILLs
the process after `runner.shutdown_timeout_seconds` (default 30 s) and
the row is marked `failed` with `"did not respond to shutdown SIGTERM"`.

Template (`service/{name}.py`):

#### Blocking Example (listen() - Simple Loops)
```python
# Service: Persistent, handles requests (blocking listener)
#!flask
#!git@gitlab.com/team/utils.git

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import utils.api  # Available after git directive

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

def handle_request(msg):
    """Route request by path and method. Returns (result_dict, http_status)."""
    request_type = msg.get("type")
    path = msg.get("path", "")
    data = msg.get("data", {})

    if path == "/fetch" and request_type == "GET":
        target = msg.get("target")  # Query param
        if not target:
            return {"error": "Missing 'target' parameter"}, 400
        result_value = utils.api.fetch_external(target)
        return {"status": "ok", "value": result_value}, 200

    elif path == "/process" and request_type == "POST":
        result_value = utils.api.process_json(data)
        return {"status": "ok", "value": result_value}, 200

    return {"error": f"Not found: {request_type} {path}"}, 404

def main():
    while True:
        msg = listener.listen()  # Blocks up to 1s until message or TERM
        if msg == "TERM":
            break
        result, status = handle_request(msg)
        listener.response(result, status)

if __name__ == "__main__":
    logger.info("Service started")
    main()
    logger.info("Service stopped")
```

**Service request message fields:**
- `type`: HTTP method (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, etc.)
- `path`: URL path after `/controller/{set}/{name}/` (e.g., `"/fetch"`, `"/api/models"`)
- `data`: Request body (parsed JSON object, text string, bytes, or `None`)
- `data_type`: `"json"`, `"text"`, `"binary"`, or `"none"`
- `user`: Requesting user (from `X-User` header)
- Additional query parameters as direct keys (e.g., `?target=foo` → `msg["target"] = "foo"`)

**Response:**
- `listener.response(result_dict, status)` — `status` is the HTTP status code (200, 400, 404, 500, etc.)
- `listener.async_response(identity, result_dict, status)` — same, for async handlers
- The controller proxy returns `status` and `result_dict` to the client unchanged (for 200 and every other status)

**URL patterns:**
- REST style (recommended): `GET /controller/{set}/{name}/fetch?target=foo` → `path="/fetch"`
- REST style: `POST /controller/{set}/{name}/process` (with JSON body) → `path="/process"`
- Legacy query style: `GET /controller/{set}/{name}/q?cmd=fetch` → `path="/q"`, `msg["cmd"]="fetch"`

#### Non-Blocking Example (poll() - Responsive Loops)
```python
# Service: Persistent, handles requests (non-blocking poll)
#!git@gitlab.com/team/utils.git

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import time
import utils.api  # Available after git directive

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

def handle_request(msg):
    """Route request by path and method. Returns (result_dict, http_status)."""
    request_type = msg.get("type")
    path = msg.get("path", "")
    data = msg.get("data", {})

    if path == "/fetch" and request_type == "GET":
        target = msg.get("target")  # Query param
        if not target:
            return {"error": "Missing 'target' parameter"}, 400
        result_value = utils.api.fetch_external(target)
        return {"status": "ok", "value": result_value}, 200

    elif path == "/process" and request_type in ["POST", "PUT"]:
        result_value = utils.api.process_json(data)
        return {"status": "ok", "value": result_value}, 200

    return {"error": f"Not found: {request_type} {path}"}, 404

def main():
    while True:
        msg = listener.poll()  # Returns immediately: msg, "TERM", or None
        if msg == "TERM":
            break
        elif msg is not None:
            result, status = handle_request(msg)
            listener.response(result, status)
        else:
            # No message; perform other non-blocking work (e.g., periodic checks)
            time.sleep(0.01)  # Small delay to reduce CPU

if __name__ == "__main__":
    logger.info("Service started")
    main()
    logger.info("Service stopped")
```

#### Asynchronous Example (async_poll() - Concurrent Requests)

Unlike `listen()`/`poll()` which pair each receive with an immediate response,
`async_poll()` decouples receiving from responding: it drains all pending requests
at once, returning `(identity, message)` tuples. You can then process them
concurrently (e.g., in a thread pool) and respond out-of-order via
`async_response(identity, result)`.

```python
# Service: Persistent, handles concurrent requests (asynchronous poll)
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Access control/listener/workspace
# control: e.g., control.job.list()
# listener: for inbound requests
# workspace: job output dir

def handle_request(msg):
    """Route request by path and method (may be slow/blocking). Returns (result_dict, http_status)."""
    request_type = msg.get("type")
    path = msg.get("path", "")
    data = msg.get("data", {})

    if path == "/status" and request_type == "GET":
        return {"status": "ok", "message": "Service running"}, 200

    elif path == "/process" and request_type == "POST":
        if not data:
            return {"error": "Missing request body"}, 400
        # Simulate slow work (e.g., external API call)
        time.sleep(1)
        return {"status": "ok", "processed": len(str(data))}, 200

    return {"error": f"Not found: {request_type} {path}"}, 404

def main():
    pool = ThreadPoolExecutor(max_workers=4)
    pending = {}  # future -> identity

    while True:
        # Drain all queued requests at once
        for identity, msg in listener.async_poll():
            if msg == "TERM":
                pool.shutdown(wait=False)
                return
            future = pool.submit(handle_request, msg)
            pending[future] = identity

        # Collect completed results and respond out-of-order
        done = [f for f in pending if f.done()]
        for future in done:
            identity = pending.pop(future)
            try:
                result, status = future.result()
            except Exception as e:
                logger.exception("Handler failed")
                result, status = {"error": str(e)}, 500
            listener.async_response(identity, result, status)

        if not pending:
            time.sleep(0.01)  # No in-flight work; avoid busy-waiting

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
#!numpy

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
- Global: Bootstrap 5, jQuery, Material Icons (e.g., `<i class="material-icons">add</i>`).
- No `<title>` or `<h1>` (auto-handled).
- JS `<script>` preferably in head; inline in body ok for simple scripts. Custom in head.html.
- Use {{SET}}/{{NAME}} in URLs/AJAX for portability.
- Mobile: the controller wrapper already places the menu inside a `navbar-expand-md` collapse with a hamburger toggler below 768px. Do not add your own toggler/collapse to menu.html — just emit the `<li>` items. For body content, wrap wide tables in `<div class="table-responsive">` and avoid fixed pixel widths so the layout reflows on phones.

#### Dark / light theme

PyOps follows the visitor's OS-level color scheme (`prefers-color-scheme`). The controller wrapper injects a theme-sync script into every view's `<head>` that runs at page load and on every change of the system preference. It:

1. Sets `<html data-bs-theme="dark|light">`.
2. Swaps the `<body>` between `bg-light` and `bg-dark text-light`.
3. Sets the global `window.pyopsDark` (`true|false`) and dispatches a `pyops:theme` CustomEvent on `window` with `detail.dark`.

Most Bootstrap components (`.btn-primary`, `.modal-content`, `.form-control`, default `.table`, …) adapt on their own and need no help. Variants whose contrast does NOT survive a theme flip have to be swapped by the view itself — subscribe to `pyops:theme` and re-apply on `$(document).ready()` for the first paint:

```html
<script>
  function applyTableTheme(dark) {
    const t = document.querySelector('[data-testid="my-table"]');
    if (!t) return;
    t.classList.toggle('table-dark', dark);
    t.classList.toggle('table-secondary', !dark);
  }
  window.addEventListener('pyops:theme', (ev) => applyTableTheme(ev.detail.dark));
  $(document).ready(() => applyTableTheme(window.pyopsDark === true));
</script>
```

Variants worth swapping:

| Light variant | Dark variant |
|---|---|
| `bg-light` | `bg-dark` |
| `text-dark` | `text-light` |
| `table-secondary` (or `table-light`) | `table-dark` |
| `btn-outline-dark` | `btn-outline-light` |
| `border-secondary` | `border-light` |

Do **not** add a manual theme toggle button — the wrapper drives this from the OS preference.

#### Head View (my_head.html)
Styles/scripts only.
```html
<style>
  .custom-head { color: blue; }
</style>
<script>
  // REST call to the service via the controller proxy. The callback always
  // runs, with (ok, body, status): ok is false for an error answer (status
  // not 2xx, or a body carrying `error`) and for a network failure (status 0).
  function apiCall(method, path, params, callback) {
    const opts = { method, headers: {} };
    if (method !== "GET" && params != null) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(params);
    }
    fetch("/controller/{{SET}}/{{NAME}}/" + path, opts)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        callback(r.ok && !body.error, body, r.status);
      })
      .catch((e) => callback(false, { error: e.toString() }, 0));
  }
  // Usage: apiCall("GET", "items?limit=10", null, (ok, body, status) => {
  //   if (!ok) { showError(body.error || `HTTP ${status}`); return; }
  //   ...
  // });
</script>
```

Answers a view's call can get:

| Status | Body | When |
|---|---|---|
| the service's | the service's, unchanged | the service answered (`listener.response(body, status)`) |
| 503 | `{"error": "No running service: <set>::<service>"}` (`preview` for `debug = True`) | the service is not running (starting up, cancelled, failed) |
| 504 | `{"error": "Service job <id> did not answer within <n>s"}` | the service did not answer within `web.timeout` |
| 400 / 404 | `{"error": "..."}` | invalid or unknown set or controller name |
| 200 | `{"error": "Controller execution failed: ..."}` | the controller's code failed to execute |
| 0 (network) | — | the request did not reach pyops |

Every error answer of the proxy carries its message in `error`, so a view shows `body.error`. Give the service's own error answers an `error` field too.

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
<div id="action-error" class="alert alert-danger" hidden></div>
<script>
  function showError(message) {
    $("#action-error").text(message).prop("hidden", false);
  }
  $("#action-btn").click(function() {
    apiCall("POST", "action", {key: "value"}, function(ok, body, status) {
      if (!ok) { showError(body.error || `HTTP ${status}`); return; }
      $("#action-error").prop("hidden", true);
      alert("Response: " + JSON.stringify(body));
    });
  });
  function submitAction() {
    // e.g., apiCall("POST", "submit", {field: "value"}, (ok, body, status) => { ... });
  }
</script>
```

## Best Practices

- **Self-Contained**: Directives for all deps; validate inputs in services.
- **Error Handling**: Services return appropriate HTTP status codes via `listener.response(result, status)` — use 400 for bad input, 404 for unknown routes, 500 for internal errors. Tasks use `logger.error()` for errors. Prefer `logging` over `print` for production code to enable level-based filtering and better error tracking.
- **Security**: Sanitize untrusted params/cmds in services.
- **Performance**: Efficient service loops (listener polls ~1s); keep tasks short.
- **Views**: Rely on Bootstrap defaults; dynamic {{SET}}/{{NAME}}; JS in head only; use REST-style endpoints (path-based routing) for service calls; show every failed call to the user (the `apiCall` callback's error branch) — the browser console is not a place users look.

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