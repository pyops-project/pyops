# PyOps WebUI Manual Test Plan

## 0. Environment Setup (Docker Compose)

### 0.1 Prepare a Git Repository for the Test Set

Create a bare Git repository that PyOps will clone as its scriptlet set:

```bash
mkdir -p /tmp/pyops-test-repo
cd /tmp/pyops-test-repo
git init --bare test_set.git
```

### 0.2 Directory Structure

Create a fresh deployment directory:

```bash
mkdir -p pyops-test
cd pyops-test
mkdir -p volumes/app/data volumes/app/etc volumes/app/venv
mkdir -p volumes/nginx/conf.d volumes/nginx/auth volumes/nginx/log
mkdir -p volumes/repos
```

Copy the bare repo into the volumes so the app container can access it:

```bash
cp -r /tmp/pyops-test-repo/test_set.git volumes/repos/
```

### 0.3 Configuration File

Create `volumes/app/etc/pyops.json`:

```json
{
    "web": {
        "bind": "0.0.0.0:80",
        "worker": 4,
        "timeout": 300
    },
    "data": {
        "jobs": "data/jobs",
        "git": "data/git",
        "db": "data/app.db"
    },
    "runner": {
        "venv": "venv",
        "pkg": "pkg",
        "git_pull_interval": 60,
        "drop_done_delay_seconds": 60
    },
    "log": {
        "loglevel": "debug"
    },
    "users": {
        "testuser": {"debug_port": 15678}
    }
}
```

### 0.4 Docker Compose File

Create `docker-compose.yml`:

```yaml
networks:
  default:
    driver: bridge

services:
  app:
    container_name: pyops_app_test
    image: 'registry.ralph.or.at/pyops/pyops:dev'
    restart: unless-stopped
    networks:
      - default
    volumes:
      - './volumes/app/data:/opt/pyops/data'
      - './volumes/app/etc:/opt/pyops/etc'
      - './volumes/app/venv:/opt/pyops/venv'
      - './volumes/repos:/repos:ro'
  nginx:
    container_name: pyops_nginx_test
    image: 'nginx:1.25'
    restart: unless-stopped
    ports:
      - '8443:443'
    networks:
      - default
    depends_on:
      - app
    volumes:
      - './volumes/nginx/conf.d:/etc/nginx/conf.d:ro'
      - './volumes/nginx/auth:/etc/nginx/auth:ro'
      - './volumes/nginx/log:/var/log/nginx:rw'
```

### 0.5 Self-Signed TLS Certificate

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout volumes/nginx/conf.d/selfsigned.key \
  -out volumes/nginx/conf.d/selfsigned.crt \
  -subj "/CN=localhost"
```

### 0.6 Nginx Configuration

Create `volumes/nginx/conf.d/app.conf`:

```nginx
server {
    listen 443 ssl;
    server_name localhost;

    ssl_certificate /etc/nginx/conf.d/selfsigned.crt;
    ssl_certificate_key /etc/nginx/conf.d/selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    add_header Strict-Transport-Security max-age=63072000;

    access_log /var/log/nginx/app_access.log;
    error_log /var/log/nginx/app_error.log;

    location / {
        auth_basic "PyOps";
        auth_basic_user_file /etc/nginx/auth/htpasswd;

        proxy_pass http://pyops_app_test;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host localhost;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Protocol https;
        proxy_set_header X-Original-Host $host;
        proxy_set_header X-User $remote_user;
        proxy_read_timeout 300;
        proxy_connect_timeout 120;
        proxy_send_timeout 300;
    }
}
```

### 0.7 HTTP Basic Auth

```bash
htpasswd -cb volumes/nginx/auth/htpasswd testuser testpass
htpasswd -b volumes/nginx/auth/htpasswd testuser2 testpass2
```

### 0.8 Start Services

```bash
docker compose up -d
```

### 0.9 Verify Setup

| # | Step | Expected |
|---|------|----------|
| 1 | `docker compose ps` | Both `pyops_app_test` and `pyops_nginx_test` running |
| 2 | `curl -k https://localhost:8443/` (no auth) | HTTP 401 Unauthorized |
| 3 | `curl -k -u testuser:testpass https://localhost:8443/` | HTTP 200; HTML dashboard returned |
| 4 | Open `https://localhost:8443/` in browser | Browser prompts for credentials |
| 5 | Enter `testuser` / `testpass` | Dashboard loads with sidebar and iframe |
| 6 | `docker compose logs app` | No startup errors; workers ready |
| 7 | `docker compose logs nginx` | No configuration errors |

### 0.10 Verify User Identity Propagation

| # | Step | Expected |
|---|------|----------|
| 1 | Log in as `testuser`, open Monitor | Job list loads (empty); no errors |
| 2 | Open DevTools > Network tab | Requests include `X-User: testuser` header (set by nginx `$remote_user`) |

---

## 1. Root Dashboard & Navigation

### 1.1 Initial Load

| # | Step | Expected |
|---|------|----------|
| 1 | Open `https://localhost:8443/` and authenticate | Dashboard loads with dark sidebar on left, iframe content area on right |
| 2 | Verify sidebar menu | "Scriptlet" section visible with items: Manager, Scheduler, Monitor, Sets |
| 3 | Check default view | Monitor page loads by default in the iframe |
| 4 | Check favicon | PyOps favicon displayed in browser tab |

### 1.2 Sidebar Navigation

| # | Step | Expected |
|---|------|----------|
| 1 | Click "Manager" in sidebar | Manager page loads in iframe; menu item highlighted |
| 2 | Click "Scheduler" | Scheduler page loads; highlight moves |
| 3 | Click "Monitor" | Monitor page loads |
| 4 | Click "Sets" | Sets page loads |
| 5 | Click section header "Scriptlet" | Section collapses/expands |
| 6 | Reload browser after navigating | URL state preserved; same page reloads in iframe |

---

## 2. Sets Management

### 2.1 Clone the Test Set

| # | Step | Expected |
|---|------|----------|
| 1 | Navigate to Sets page | Table shows no sets (or any pre-existing ones) |
| 2 | Click "Clone" | Modal opens with URL, optional username/password, git name/email |
| 3 | Enter URL `file:///repos/test_set.git`, git name `Test User`, git email `test@test.local`, submit | Set `test_set` appears in table with branch `main` or `master` |
| 4 | Try cloning the same URL again | Error: "Set 'test_set' already exists." |

### 2.2 Create a Local Set

| # | Step | Expected |
|---|------|----------|
| 1 | Click "Create" | Modal opens with Name input |
| 2 | Enter `local_set` and submit | Set appears in table with a branch |
| 3 | Try creating with empty name | Error message or validation prevents creation |
| 4 | Try creating `local_set` again | Error: "Set 'local_set' already exists." |

### 2.3 Pull a Set

| # | Step | Expected |
|---|------|----------|
| 1 | Click Pull on `test_set` (has remote) | Set syncs; no errors |
| 2 | Click Pull on `local_set` (no remote) | Error or "no remote" message |

### 2.4 Switch Branch

| # | Step | Expected |
|---|------|----------|
| 1 | Click Switch Branch on `test_set` | Modal opens with branch input/selector |
| 2 | Enter `dev` and confirm | Branch column updates to `dev` (new branch created from HEAD) |
| 3 | Switch back to `main`/`master` | Branch reverts |

### 2.5 Remove a Set

| # | Step | Expected |
|---|------|----------|
| 1 | Click Remove on `local_set` | Confirmation dialog appears |
| 2 | Confirm deletion | `local_set` disappears from table |
| 3 | Verify `test_set` remains | Still present |

### 2.6 Auto-Refresh

| # | Step | Expected |
|---|------|----------|
| 1 | Leave Sets page open; use curl to create a set via API | New set appears within ~10 seconds without manual refresh |

---

## 3. Scriptlet Manager

### 3.1 Create All Scriptlet Types

All scriptlets below are created in the `test_set` set.

#### 3.1.1 Task: `hello_task`

| # | Step | Expected |
|---|------|----------|
| 1 | Navigate to Manager, click "Create" | Modal opens |
| 2 | Select set `test_set`, name `hello_task`, type `task` | — |
| 3 | Click "Create & Edit" | Scriptlet created; task editor opens |
| 4 | Enter the following code and press Ctrl+S to save: | Saved to Git |

```python
import json

print("Hello from PyOps!")
print(f"Owner: {owner}")
print(f"Set: {set}")
print(f"Name: {name}")
print(f"Workspace: {workspace}")

try:
    config = json.loads(param)
    print(f"Received param: {config}")
except Exception:
    print("No JSON param provided")
```

#### 3.1.2 Task: `failing_task`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `failing_task`, type `task` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
import sys

print("About to fail...")
sys.stderr.write("Error details on stderr\n")
raise ValueError("Intentional failure for testing")
```

#### 3.1.3 Task: `long_task`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `long_task`, type `task` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
import time

for i in range(60):
    print(f"Tick {i+1}/60")
    time.sleep(1)

print("Completed!")
```

#### 3.1.4 Task: `file_writer`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `file_writer`, type `task` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
import os

output_dir = workspace
report_path = os.path.join(output_dir, "report.txt")

with open(report_path, "w") as f:
    f.write("Test Report\n")
    f.write(f"Owner: {owner}\n")
    f.write(f"Set: {set}\n")
    f.write(f"Name: {name}\n")

print(f"Report written to {report_path}")
```

#### 3.1.5 Task: `lib_consumer`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `lib_consumer`, type `task` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
#!{::helpers}

result = greet("PyOps Tester")
print(result)

total = add(17, 25)
print(f"17 + 25 = {total}")
```

#### 3.1.6 Library: `helpers`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `helpers`, type `library` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
def greet(name):
    return f"Hello, {name}! Welcome to PyOps."

def add(a, b):
    return a + b
```

#### 3.1.7 Task: `scheduled_task`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `scheduled_task`, type `task` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
from datetime import datetime

now = datetime.now().isoformat()
print(f"Scheduled execution at {now}")
```

#### 3.1.8 Service: `api_service`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `api_service`, type `service` | Created |
| 2 | Open editor (library editor), enter the following code, press Ctrl+S: | Saved |

```python
import json

print("Service starting...")

while not listener.kill_now:
    msg = listener.poll()
    if msg is None:
        continue

    path = msg.get("path", "/")
    method = msg.get("type", "GET")
    data = msg.get("data")
    user = msg.get("user", "unknown")

    if path == "/hello":
        listener.response({"message": f"Hello {user}!", "method": method}, 200)
    elif path == "/echo":
        listener.response({"echo": data, "user": user}, 200)
    elif path == "/status":
        listener.response({"status": "running", "service": name, "set": set}, 200)
    else:
        listener.response({"error": "not found", "path": path}, 404)

print("Service stopped.")
```

#### 3.1.9 Controller: `dashboard`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `dashboard`, type `controller` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```python
set = "test_set"
service = "api_service"
name = "Dashboard"
debug = False

head = "dashboard_head"
menu = "dashboard_menu"
body = "dashboard_body"
```

#### 3.1.10 View: `dashboard_head`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `dashboard_head`, type `view` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```html
<style>
    .card { margin-top: 20px; }
    #response-area { min-height: 100px; }
</style>
```

#### 3.1.11 View: `dashboard_menu`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `dashboard_menu`, type `view` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```html
<li class="nav-item">
    <a class="nav-link" href="/controller/{{SET}}/{{NAME}}">Home</a>
</li>
```

#### 3.1.12 View: `dashboard_body`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `dashboard_body`, type `view` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```html
<div class="container mt-4">
    <div class="card">
        <div class="card-header">{{SET}}::{{NAME}} API Test</div>
        <div class="card-body">
            <button id="btn-hello" class="btn btn-primary me-2">GET /hello</button>
            <button id="btn-status" class="btn btn-secondary me-2">GET /status</button>
            <button id="btn-echo" class="btn btn-info me-2">POST /echo</button>
            <hr>
            <pre id="response-area" class="bg-dark text-light p-3 rounded">Click a button to call the API...</pre>
        </div>
    </div>
</div>
<script>
    const base = "/controller/{{SET}}/{{NAME}}";
    function show(data) {
        document.getElementById("response-area").textContent = JSON.stringify(data, null, 2);
    }
    document.getElementById("btn-hello").addEventListener("click", () => {
        fetch(base + "/hello").then(r => r.json()).then(show);
    });
    document.getElementById("btn-status").addEventListener("click", () => {
        fetch(base + "/status").then(r => r.json()).then(show);
    });
    document.getElementById("btn-echo").addEventListener("click", () => {
        fetch(base + "/echo", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({"test": true, "timestamp": new Date().toISOString()})
        }).then(r => r.json()).then(show);
    });
</script>
```

#### 3.1.13 Markdown: `README`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `README`, type `markdown` | Created |
| 2 | Open editor, enter the following content, press Ctrl+S: | Saved |

```markdown
# Test Set

This set contains scriptlets for manual testing of PyOps.

## Contents
- hello_task: Basic task with param support
- failing_task: Task that raises an error
- long_task: Long-running task for cancellation testing
- file_writer: Task that produces downloadable output
- lib_consumer: Task that imports a library
- helpers: Shared library
- scheduled_task: Task for scheduler testing
- api_service: Service with request/response handling
- dashboard: Controller with views for service proxy testing
```

#### 3.1.14 JS Library: `utils`

| # | Step | Expected |
|---|------|----------|
| 1 | Create scriptlet: set `test_set`, name `utils`, type `jslibrary` | Created |
| 2 | Open editor, enter the following code, press Ctrl+S: | Saved |

```javascript
function formatTimestamp(iso) {
    return new Date(iso).toLocaleString();
}

function showAlert(msg, type) {
    const el = document.createElement("div");
    el.className = "alert alert-" + (type || "info") + " alert-dismissible fade show";
    el.innerHTML = msg + '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    document.body.prepend(el);
}
```

### 3.2 Verify Manager Table

| # | Step | Expected |
|---|------|----------|
| 1 | Navigate to Manager and click Refresh | All 14 scriptlets listed with correct Set, Name, Type |
| 2 | Verify types shown | task (5), library (1), service (1), controller (1), view (3), markdown (1), jslibrary (1) |

### 3.3 Delete and Recreate

| # | Step | Expected |
|---|------|----------|
| 1 | Click Delete on `utils` (jslibrary) | Confirmation dialog |
| 2 | Confirm | Scriptlet removed from table |
| 3 | Recreate `utils` with same code | Scriptlet reappears |

---

## 4. Task Editor

### 4.1 Layout and Loading

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on `hello_task` | Split view: code editor (left), parameter input (top-right), stdout/stderr logs (bottom-right) |
| 2 | Verify code | Previously saved code displayed in Ace editor |
| 3 | Verify syntax highlighting | Python keywords, strings, comments colored |

### 4.2 Draft Auto-Save

| # | Step | Expected |
|---|------|----------|
| 1 | Add a comment `# draft test` to the code | — |
| 2 | Wait ~1 second | Draft auto-saved |
| 3 | Close editor tab, reopen from Manager | Draft comment persists |
| 4 | Click Revert/Rollback | Comment removed; code matches last Git commit |

### 4.3 Code Completion

| # | Step | Expected |
|---|------|----------|
| 1 | Type `import os` then on new line type `os.` | Autocomplete popup with `os` module members |
| 2 | Type `path` to filter | Suggestions narrow to `os.path` etc. |
| 3 | Select a suggestion | Inserted correctly |
| 4 | Undo changes (Ctrl+Z) and Revert | Code restored |

### 4.4 Run: hello_task

| # | Step | Expected |
|---|------|----------|
| 1 | In parameter input, enter: `{"target": "prod"}` | — |
| 2 | Click Run | Job submitted; logs panel updates |
| 3 | Observe stdout | Shows "Hello from PyOps!", owner, set, name, workspace, and `Received param: {'target': 'prod'}` |
| 4 | Observe stderr | Empty |

### 4.5 Run: failing_task

| # | Step | Expected |
|---|------|----------|
| 1 | Open `failing_task` editor, click Run | Job starts |
| 2 | Observe stdout | "About to fail..." |
| 3 | Observe stderr | "Error details on stderr" and `ValueError: Intentional failure for testing` traceback |

### 4.6 Cancel: long_task

| # | Step | Expected |
|---|------|----------|
| 1 | Open `long_task` editor, click Run | Stdout starts showing "Tick 1/60", "Tick 2/60"... |
| 2 | Wait a few ticks, then click Cancel | Job cancelled; logs stop updating; no "Completed!" message |

### 4.7 Download: file_writer

| # | Step | Expected |
|---|------|----------|
| 1 | Open `file_writer` editor, click Run | Stdout: "Report written to ..." |
| 2 | Click Download | ZIP file downloaded |
| 3 | Extract ZIP | Contains `report.txt` with owner, set, name |

### 4.8 Library Import: lib_consumer

| # | Step | Expected |
|---|------|----------|
| 1 | Open `lib_consumer` editor, click Run | Job runs |
| 2 | Observe stdout | "Hello, PyOps Tester! Welcome to PyOps." and "17 + 25 = 42" |

### 4.9 Font Size

| # | Step | Expected |
|---|------|----------|
| 1 | Change font size selector (e.g. 14 to 20) | Editor text resizes |
| 2 | Change back | Reverts |

---

## 5. Library Editor

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on `helpers` in Manager | Library editor opens (Ace, Python mode, no run panel) |
| 2 | Add a new function: `def multiply(a, b): return a * b` | Syntax highlighting works |
| 3 | Wait for draft auto-save; close and reopen | New function persists |
| 4 | Press Ctrl+S | Committed to Git |
| 5 | Click Revert | No pending changes; code stays as committed |

---

## 6. View Editor (HTML)

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on `dashboard_body` in Manager | View editor opens (Ace, HTML mode) |
| 2 | Add `<!-- test comment -->` | HTML syntax highlighting applied |
| 3 | Ctrl+S to save, then Revert | Works correctly |

---

## 7. JS Library Editor

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on `utils` in Manager | JS editor opens (Ace, JavaScript mode) |
| 2 | Modify code | JavaScript highlighting works |
| 3 | Ctrl+S to save, then Revert | Works correctly |

---

## 8. Markdown Editor

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on `README` in Manager | Markdown editor opens |
| 2 | Edit content | Markdown highlighting for headers, lists |
| 3 | Ctrl+S to save, then Revert | Works correctly |

---

## 9. Job Monitor

### 9.1 Job List Display

| # | Step | Expected |
|---|------|----------|
| 1 | Navigate to Monitor | Table with columns: ID, Start, End, Kind, State, Actions |
| 2 | Previous test runs visible | Jobs from sections 4.4–4.8 shown |
| 3 | Verify sorting | Jobs sorted by ID descending (newest first) |
| 4 | Hover over State on `failing_task` job | Tooltip shows ValueError detail |

### 9.2 Job State Lifecycle

| # | Step | Expected |
|---|------|----------|
| 1 | Run `hello_task` from editor while Monitor is open in another tab | State progresses: todo > dequeued > loading > running > done |
| 2 | Check `failing_task` job | State: failed |
| 3 | Check cancelled `long_task` job | State: cancelled |

### 9.3 Per-Job Actions

| # | Step | Expected |
|---|------|----------|
| 1 | Click Download on a completed `file_writer` job | ZIP downloaded |
| 2 | Click Copy Parameters on `hello_task` job | `{"target": "prod"}` copied to clipboard |
| 3 | Click Delete on a completed job | Job removed from table |
| 4 | Run `long_task`, then Cancel from Monitor | Job cancelled |

### 9.4 Bulk Actions

| # | Step | Expected |
|---|------|----------|
| 1 | Run `hello_task` twice; wait for completion; click "Delete Done" | Both done jobs removed |
| 2 | Run `failing_task`; wait for failure; click "Delete Failed" | Failed job removed |
| 3 | Run `long_task` twice; click "Cancel All" | Both running jobs cancelled |

### 9.5 User Filtering

| # | Step | Expected |
|---|------|----------|
| 1 | As `testuser`, run `hello_task` | Job visible in Monitor |
| 2 | Open incognito window, log in as `testuser2` | Monitor shows no jobs (or only `testuser2`'s) |
| 3 | As `testuser2`, run `hello_task` | Only this new job visible; `testuser`'s job not shown |

### 9.6 Auto-Refresh

| # | Step | Expected |
|---|------|----------|
| 1 | Keep Monitor open; run a job from editor in another tab | Job appears and state updates within ~10 seconds |

---

## 10. Scheduler

### 10.1 Create Schedule

| # | Step | Expected |
|---|------|----------|
| 1 | Navigate to Scheduler | Table empty or shows existing schedules |
| 2 | Click Create | Modal with: Set, Name, Drop Done, Max Retries, Cron Expression, Parameters |
| 3 | Set: `test_set`, Name: `scheduled_task`, Cron: `*/1 * * * *`, submit | Schedule appears in table |
| 4 | Enter invalid cron `not-a-cron` | Validation error |

### 10.2 Edit Schedule

| # | Step | Expected |
|---|------|----------|
| 1 | Click Edit on the schedule | Modal pre-populated |
| 2 | Change cron to `*/5 * * * *`, save | Table updates |

### 10.3 Run Now

| # | Step | Expected |
|---|------|----------|
| 1 | Click Run on the schedule | Alert shows job ID |
| 2 | Switch to Monitor | Job visible with kind `task`, stdout: "Scheduled execution at ..." |
| 3 | Return to Scheduler | "Last Run" column shows current timestamp |

### 10.4 Activate / Deactivate

| # | Step | Expected |
|---|------|----------|
| 1 | Click Deactivate | Icon changes; schedule paused |
| 2 | Wait > 1 minute | No new jobs appear in Monitor |
| 3 | Click Activate | Icon changes; schedule resumes |

### 10.5 Drop Done & Max Retries

| # | Step | Expected |
|---|------|----------|
| 1 | Edit schedule: enable Drop Done, click Run Now | Job runs and auto-deletes after ~60 seconds |
| 2 | Create new schedule for `failing_task` with Max Retries: 1 | — |
| 3 | Click Run Now | Job retries once after initial failure (check Monitor) |

### 10.6 Delete Schedule

| # | Step | Expected |
|---|------|----------|
| 1 | Click Delete on a schedule | Confirmation dialog |
| 2 | Confirm | Schedule removed |

### 10.7 Copy Parameters

| # | Step | Expected |
|---|------|----------|
| 1 | Create a schedule with param `{"env": "test"}`, then Copy Parameters | `{"env": "test"}` copied to clipboard |

---

## 11. Controllers & Service Proxy

### 11.1 Start the Service

| # | Step | Expected |
|---|------|----------|
| 1 | Open `api_service` in editor (library editor for services) | Code displayed |
| 2 | Run `api_service` as a service job (via Monitor: submit job with kind `service`, or from editor if run button is available) | Job enters `running` state and stays running |
| 3 | Verify in Monitor | Kind: service, State: running |

### 11.2 Controller Rendering

| # | Step | Expected |
|---|------|----------|
| 1 | Reload dashboard (root page) | Sidebar now shows `test_set` section with `Dashboard` item |
| 2 | Click `Dashboard` in sidebar | Controller page renders with navbar showing "test_set::Dashboard" |
| 3 | Verify `{{SET}}` replacement | Menu link href is `/controller/test_set/dashboard` (not `{{SET}}`) |
| 4 | Verify card renders | Card with "test_set::dashboard API Test" header and three buttons visible |

### 11.3 Service Proxy

| # | Step | Expected |
|---|------|----------|
| 1 | Click "GET /hello" button | Response area shows `{"message": "Hello testuser!", "method": "GET"}` |
| 2 | Click "GET /status" button | Response: `{"status": "running", "service": "api_service", "set": "test_set"}` |
| 3 | Click "POST /echo" button | Response: `{"echo": {"test": true, "timestamp": "..."}, "user": "testuser"}` |

### 11.4 Service Proxy After Stop

| # | Step | Expected |
|---|------|----------|
| 1 | Go to Monitor, cancel the `api_service` job | Job cancelled |
| 2 | Return to Dashboard controller, click "GET /hello" | Error response (no running service) |

### 11.5 Controller with Syntax Error

| # | Step | Expected |
|---|------|----------|
| 1 | Open `dashboard` controller in editor | Code displayed |
| 2 | Introduce a syntax error (e.g. remove a quote), wait for draft auto-save | — |
| 3 | Navigate to `/controller/test_set/dashboard` | Error page with "Controller Draft Error" and traceback |
| 4 | Revert the draft in editor | — |
| 5 | Reload controller page | Renders correctly again |

---

## 12. Debug Mode (Web-Accessible Parts)

### 12.1 Debug Port Endpoint

| # | Step | Expected |
|---|------|----------|
| 1 | `curl -k -u testuser:testpass https://localhost:8443/debug/port` | `{"port": 15678}` |
| 2 | `curl -k -u testuser2:testpass2 https://localhost:8443/debug/port` | HTTP 400 (testuser2 not in `users` config) |

### 12.2 Debug Job

| # | Step | Expected |
|---|------|----------|
| 1 | Submit debug job via curl: `curl -k -u testuser:testpass -X PUT https://localhost:8443/scriptlet/job/item -H 'Content-Type: application/json' -d '{"set":"test_set","name":"hello_task","kind":"debug","param":""}'` | Job created with kind `debug` |
| 2 | Check Monitor | Debug job visible, runs unstaged code |
| 3 | Submit another debug job for same user | Previous debug job auto-cancelled |

---

## 13. Cross-Cutting Concerns

### 13.1 Authentication

| # | Step | Expected |
|---|------|----------|
| 1 | Access `https://localhost:8443/` without credentials | HTTP 401 |
| 2 | Access with wrong password (`testuser:wrong`) | HTTP 401 |
| 3 | Access with `testuser:testpass` | Dashboard loads |
| 4 | Access with `testuser2:testpass2` | Dashboard loads; separate user context |

### 13.2 Error Handling

| # | Step | Expected |
|---|------|----------|
| 1 | `GET /scriptlet/inventory/code?set=nonexistent&name=x&type=task` | Error response |
| 2 | `DELETE /scriptlet/job/item?id=nonexistent_id` | Error or 404 |
| 3 | Submit job for non-existent scriptlet | Error returned |

### 13.3 Concurrent Operations

| # | Step | Expected |
|---|------|----------|
| 1 | Open Manager in two tabs; create a scriptlet in tab 1 | Tab 2 shows new scriptlet after auto-refresh |
| 2 | Edit `hello_task` in two windows; save in both | Last save wins; no corruption |
| 3 | Submit multiple jobs rapidly | All queued and processed |

### 13.4 Browser Compatibility

| # | Step | Expected |
|---|------|----------|
| 1 | Run all tests in Chrome | All features work |
| 2 | Run all tests in Firefox | All features work |
| 3 | Run all tests in Edge | All features work |

---

## 14. End-to-End Workflows

### 14.1 Full Task Lifecycle

1. Create set (done in 2.1) > Create `hello_task` (done in 3.1.1) > Edit with code > Run with param `{"target": "prod"}` > Observe output > Check Monitor shows "done" > Download results > Delete job > Delete scriptlet > Verify removed from Manager

### 14.2 Scheduled Task Workflow

1. Ensure `scheduled_task` exists > Create schedule `*/1 * * * *` > Wait 1–2 minutes > Verify jobs appearing in Monitor > Deactivate schedule > Wait another minute > No new jobs > Delete schedule

### 14.3 Service + Controller Workflow

1. Ensure `api_service`, `dashboard`, and all `dashboard_*` views exist > Run `api_service` as service > Reload dashboard > Navigate to controller > Click all three API buttons > Verify responses > Cancel service > Verify proxy errors

### 14.4 Draft Workflow

1. Open `hello_task` editor > Add `# test draft` > Close without Ctrl+S > Reopen > Verify draft comment present > Click Revert > Comment gone > Add `# committed change` > Ctrl+S > Revert button disabled

### 14.5 Library Import Workflow

1. Ensure `helpers` library saved > Run `lib_consumer` > Stdout shows greeting and sum > Edit `helpers` to add `multiply` > Save > Edit `lib_consumer` to call `multiply(6, 7)` > Save and run > Stdout includes "42"

### 14.6 Multi-User Isolation

1. Browser A: `testuser` runs `hello_task` > Browser B (incognito): `testuser2` opens Monitor > `testuser2` sees no jobs > `testuser2` runs `hello_task` > Each user only sees own jobs > Both see same scriptlets in Manager

---

## 15. Teardown

```bash
docker compose down -v
cd ..
rm -rf pyops-test
rm -rf /tmp/pyops-test-repo
```
