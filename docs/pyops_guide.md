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
  Right-click: Reset Local from Remote, Write Local to Remote Draft, Save Remote Draft, Diff Local Remote, Delete Scriptlet; Start/Stop Debug Job (tasks).
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
  "anonymous": {"debug_port": 45678}
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

Controllers: `debug=True` uses unstaged/draft code (preview).

## Full Workflow Example

1. **Clone Set**: Browser to http://localhost:8000/controller/internal/sets > Add (name="myops", URL=git@github.com/user/myops.git, branch=main).
2. **Create Components**:
   - pyops-manager titlebar > Create Scriptlet (set="myops", type="service", name="myservice"); opens pyops/myops/service/myservice.py; use Cline to generate code.
   - Create controller/mycontroller.py (globals: set="myops" service="myservice" ...).
   - Create view/my_head.html, my_menu.html, my_body.html (Bootstrap + apiCall).
3. **Edit/Generate**: Cline prompts for code/logic refinements.
4. **Sync**: Right-click > Write Local to Remote Draft > Save Remote Draft (commit).
5. **Enqueue Service**: Enqueue via web UI (/scriptlet/manager.html POST kind="service", with optional `max_retries` to auto-restart on failure up to N times) or API /scriptlet/job/item.
6. **Access UI**: http://localhost:8000/controller/myops/mycontroller.
7. **Debug**: Right-click task > Start Debug Job; titlebar Attach to Debug Job for VSCode debugger.
8. **Monitor/Schedule**: pyops-monitor bulk ops; /scriptlet/schedule (create schedules with optional `max_retries` to auto-restart on failure).

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
