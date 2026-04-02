# PyOps VSIX Manual Test Plan

This test plan covers the VS Code extension for PyOps. It **extends** the WebUI test plan (`test_plan.md`) and assumes the same Docker Compose environment from sections 0.1--0.8 is running with `test_set` cloned and all scriptlets from section 3.1 already created.

---

## 0. VSIX Environment Setup

### 0.1 Prerequisites

| # | Step | Expected |
|---|------|----------|
| 1 | Docker Compose environment from `test_plan.md` sections 0.1--0.8 running | Both containers healthy |
| 2 | All scriptlets from `test_plan.md` section 3.1 created via WebUI | 14 scriptlets in `test_set` |
| 3 | VS Code installed with Python extension | Extensions view shows Python extension |
| 4 | PyOps VSIX installed (`.vsix` file or from marketplace) | "PyOps" appears in Extensions list |

### 0.2 Workspace Setup

| # | Step | Expected |
|---|------|----------|
| 1 | Open a fresh workspace folder in VS Code | Empty workspace |
| 2 | Open Settings (Ctrl+,), search "pyops" | 8 settings visible: serverUrl, username, timeout, useRemoteLinter, localPyopsDir, autoRefreshInterval, excludedSets, debugPort |
| 3 | Set `pyops.serverUrl` to `https://localhost:8443` | Setting saved |
| 4 | Set `pyops.username` to `testuser` | Setting saved |
| 5 | Set `pyops.debugPort` to `15678` | Matches `debug_port` in `pyops.json` for `testuser` |

### 0.3 Authentication

| # | Step | Expected |
|---|------|----------|
| 1 | Click the PyOps icon in the Activity Bar | Password input prompt appears |
| 2 | Enter `testpass` | Password stored securely |
| 3 | Check status bar (bottom right) | `$(check) PyOps: Connected` with green text |
| 4 | Hover over status bar item | Tooltip shows "Connected to PyOps server" or similar |

### 0.4 Authentication Failure & Recovery

| # | Step | Expected |
|---|------|----------|
| 1 | Change `pyops.username` to `wronguser` | Status bar updates |
| 2 | Click status bar item to refresh | `$(circle-slash) PyOps: Failed` with red text |
| 3 | Revert `pyops.username` to `testuser` | — |
| 4 | Extension re-prompts for password | Enter `testpass`; status bar turns green |

### 0.5 No Config State

| # | Step | Expected |
|---|------|----------|
| 1 | Clear `pyops.serverUrl` | Status bar shows `$(circle-slash) PyOps: No serverUrl` |
| 2 | Manager and Monitor tree views show no data | Commands requiring connection are hidden |
| 3 | Restore `pyops.serverUrl` to `https://localhost:8443` | Connection re-established |

---

## 1. Manager Tree View

### 1.1 Initial Load

| # | Step | Expected |
|---|------|----------|
| 1 | Click PyOps icon in Activity Bar | Manager and Monitor views visible in sidebar |
| 2 | Manager tree loads | `test_set` appears as root folder |
| 3 | Expand `test_set` | All 14 scriptlets listed with correct type icons |
| 4 | Verify scriptlet types | task (5), library (1), service (1), controller (1), view (3), markdown (1), jslibrary (1) |

### 1.2 Local File Download

| # | Step | Expected |
|---|------|----------|
| 1 | After Manager loads, check workspace `pyops/` directory | `pyops/test_set/` directory created |
| 2 | Verify subdirectories | `task/`, `library/`, `service/`, `controller/`, `view/`, `markdown/`, `jslibrary/` present |
| 3 | Open `pyops/test_set/task/hello_task.py` | Contains the code from `test_plan.md` section 3.1.1 |
| 4 | Open `pyops/test_set/view/dashboard_body.html` | Contains HTML from section 3.1.12 |
| 5 | Open `pyops/test_set/jslibrary/utils.js` | Contains JavaScript from section 3.1.14 |
| 6 | Open `pyops/test_set/markdown/README.md` | Contains markdown from section 3.1.13 |

### 1.3 Sync Status Icons

| # | Step | Expected |
|---|------|----------|
| 1 | All downloaded scriptlets show check icon | Indicates synced with remote |
| 2 | Edit `pyops/test_set/task/hello_task.py` locally, add `# local change` | File modified |
| 3 | Click Refresh in Manager toolbar | `hello_task` now shows warning icon (unsynced) |
| 4 | Undo the local change, save | — |
| 5 | Refresh Manager | `hello_task` shows check icon again |

### 1.4 Click to Open

| # | Step | Expected |
|---|------|----------|
| 1 | Click `hello_task` in tree | `hello_task.py` opens in editor |
| 2 | Click `dashboard_body` (view) | `dashboard_body.html` opens |
| 3 | Click `README` (markdown) | `README.md` opens |
| 4 | Click `utils` (jslibrary) | `utils.js` opens |

### 1.5 Excluded Sets

| # | Step | Expected |
|---|------|----------|
| 1 | Add `test_set` to `pyops.excludedSets` array | — |
| 2 | Refresh Manager | `test_set` no longer visible in tree |
| 3 | Remove `test_set` from `pyops.excludedSets` | — |
| 4 | Refresh Manager | `test_set` reappears |

### 1.6 Auto-Refresh

| # | Step | Expected |
|---|------|----------|
| 1 | Verify `pyops.autoRefreshInterval` is `10` (default) | — |
| 2 | Create a new scriptlet via WebUI (e.g. `auto_test` task in `test_set`) | — |
| 3 | Wait ~10 seconds | New scriptlet appears in Manager without manual refresh |
| 4 | Delete `auto_test` via WebUI | Disappears from Manager after auto-refresh |

---

## 2. Scriptlet Create (VSIX)

### 2.1 Create Task

| # | Step | Expected |
|---|------|----------|
| 1 | Click "Create" (+ icon) in Manager toolbar | Quick pick: type selection appears |
| 2 | Select "Task" | Set selection appears with `test_set` listed |
| 3 | Select `test_set` | Name input box appears |
| 4 | Enter `vsix_task` | — |
| 5 | Press Enter | Info message: "Created local task test_set::vsix_task" |
| 6 | Verify editor opens | `vsix_task.py` opens with `pass` as template content |
| 7 | Verify file on disk | `pyops/test_set/task/vsix_task.py` exists |
| 8 | Manager tree shows `vsix_task` with diff-added icon | Marked as local-only |

### 2.2 Duplicate Name Prevention

| # | Step | Expected |
|---|------|----------|
| 1 | Click Create, select Task, select `test_set` | Name input appears |
| 2 | Enter `hello_task` (already exists remotely) | Warning: "Name 'hello_task' already exists" |
| 3 | Input remains open for retry | Enter a unique name or cancel |

### 2.3 Local File Conflict Prevention

| # | Step | Expected |
|---|------|----------|
| 1 | Click Create, select Task, select `test_set` | Name input appears |
| 2 | Enter `vsix_task` (local file already exists from 2.1) | Warning: "Local file 'vsix_task.py' already exists" |

### 2.4 Markdown Name Restriction

| # | Step | Expected |
|---|------|----------|
| 1 | Click Create, select "Markdown", select `test_set` | Name input appears |
| 2 | Enter `NOTES` (not "README") | Error: "Markdown only allowed for README" |

### 2.5 Create All Types

| # | Step | Expected |
|---|------|----------|
| 1 | Create Library `vsix_lib` | `.py` file, template `pass` |
| 2 | Create Controller `vsix_ctrl` | `.py` file, template `pass` |
| 3 | Create View `vsix_view` | `.html` file, template empty |
| 4 | Create JS Library `vsix_jslib` | `.js` file, template empty |

### 2.6 Cancel at Any Step

| # | Step | Expected |
|---|------|----------|
| 1 | Click Create, press Escape at type selection | No scriptlet created |
| 2 | Click Create, select type, press Escape at set selection | No scriptlet created |
| 3 | Click Create, select type, select set, press Escape at name | No scriptlet created |

### 2.7 No Sets Available

| # | Step | Expected |
|---|------|----------|
| 1 | Temporarily remove all sets via WebUI | — |
| 2 | Click Create in VSIX | Error: "No existing sets found. Cannot create scriptlet without a set." |
| 3 | Recreate `test_set` via WebUI | Manager refreshes |

---

## 3. Scriptlet Sync Workflow

### 3.1 Write Local to Remote Draft

| # | Step | Expected |
|---|------|----------|
| 1 | Open `pyops/test_set/task/hello_task.py` in editor | Code displayed |
| 2 | Add `# vsix draft change` at end, save | File saved locally |
| 3 | Right-click `hello_task` in Manager > "Write Local to Remote Draft" | Info message: draft written |
| 4 | Open `hello_task` in WebUI editor | Draft shows `# vsix draft change` appended |

### 3.2 Save Remote Draft

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click `hello_task` in Manager > "Save Remote Draft" | Info message: draft committed |
| 2 | Verify in WebUI: Revert button disabled | Draft committed to Git |
| 3 | Refresh Manager in VSIX | `hello_task` shows check icon (synced) |

### 3.3 Reset Local from Remote

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `hello_task.py` locally: add `# will be overwritten` | File modified |
| 2 | Right-click `hello_task` in Manager > "Reset Local from Remote Draft" | Info message: local file reset |
| 3 | Verify `hello_task.py` content | Local change gone; matches remote version |

### 3.4 Write Local-Only to Remote

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `vsix_task.py` (local-only from section 2.1): add `print("hello")` | File saved |
| 2 | Right-click `vsix_task` > "Write Local to Remote Draft" | Auto-creates remote scriptlet then writes draft |
| 3 | Refresh Manager | `vsix_task` no longer shows diff-added icon; shows as remote item |
| 4 | Verify in WebUI | `vsix_task` appears in Manager with draft content |

### 3.5 Diff Local vs Remote

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `hello_task.py` locally: add `# diff test` | — |
| 2 | Right-click `hello_task` > "Diff Local vs Remote" | VS Code diff editor opens |
| 3 | Verify diff view | Left: remote version (read-only); Right: local version (editable) |
| 4 | Verify syntax highlighting | Python highlighting on both sides |
| 5 | Undo local change | — |

### 3.6 Diff for View Type

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `dashboard_body.html` locally, add `<!-- diff -->` | — |
| 2 | Diff Local vs Remote on `dashboard_body` | Diff shows HTML syntax highlighting |
| 3 | Undo local change | — |

### 3.7 Context Menu Visibility

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click a remote scriptlet (e.g. `hello_task`) | Shows: Reset, Write Draft, Save Draft, Diff, Delete |
| 2 | Right-click a local-only scriptlet (e.g. `vsix_lib`) | Shows: Write Draft, Delete (no Reset, Save Draft, or Diff) |
| 3 | Right-click a task scriptlet | Additionally shows: Start Debug, Stop Debug |
| 4 | Right-click a non-task scriptlet (e.g. `helpers` library) | No Start/Stop Debug options |

---

## 4. Scriptlet Delete

### 4.1 Delete Remote Scriptlet

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click `vsix_task` > "Delete Scriptlet" | Confirmation dialog appears |
| 2 | Confirm deletion | Info message: deleted |
| 3 | Verify Manager tree | `vsix_task` removed |
| 4 | Verify local file | `pyops/test_set/task/vsix_task.py` deleted |
| 5 | Verify WebUI | `vsix_task` no longer in Manager |

### 4.2 Delete Local-Only Scriptlet

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click `vsix_lib` (local-only) > "Delete Scriptlet" | Confirmation dialog |
| 2 | Confirm | Local file removed; item disappears from tree |

---

## 5. Job Monitor Tree View

### 5.1 Job List

| # | Step | Expected |
|---|------|----------|
| 1 | Switch to Monitor view in PyOps sidebar | Job list visible |
| 2 | Previous jobs from WebUI testing visible | Jobs sorted newest first by start time |
| 3 | Each job shows ID as label | Description shows state and kind |
| 4 | Hover over a job | Tooltip: state, detail, start, end, param |

### 5.2 Job State Icons

| # | Step | Expected |
|---|------|----------|
| 1 | Find a completed job | Check icon displayed |
| 2 | Find a failed job | Close icon displayed |
| 3 | Find a cancelled job | Close icon displayed |
| 4 | Run `long_task` via WebUI while Monitor is open | Running job shows spinning loading icon |

### 5.3 View Job Output

| # | Step | Expected |
|---|------|----------|
| 1 | Run `hello_task` with param `{"from": "vsix"}` via WebUI | Job appears in Monitor |
| 2 | Right-click job > "View Job Details" | "PyOps Job View" output channel opens |
| 3 | Observe output | stdout streams incrementally: "Hello from PyOps!", owner, set, name, param |
| 4 | Wait for completion | Polling stops; output complete |

### 5.4 View Failed Job Output

| # | Step | Expected |
|---|------|----------|
| 1 | Run `failing_task` via WebUI | Job appears as failed |
| 2 | View Job Details | stdout: "About to fail..."; stderr: traceback with ValueError |

### 5.5 Cancel Job

| # | Step | Expected |
|---|------|----------|
| 1 | Run `long_task` via WebUI | Job appears as running |
| 2 | Right-click running job > "Cancel Job" | Job state changes to cancelled |
| 3 | Verify in WebUI Monitor | Job shows cancelled |

### 5.6 Download Job Workspace

| # | Step | Expected |
|---|------|----------|
| 1 | Run `file_writer` via WebUI; wait for completion | Job done |
| 2 | Right-click job > "Download Job Workspace" | Save dialog appears |
| 3 | Save ZIP file | File saved to selected location |
| 4 | Extract ZIP | Contains `report.txt` with expected content |

### 5.7 Copy Job Param

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click a job that had parameters > "Copy Job Param" | Info message: param copied |
| 2 | Paste from clipboard | Job parameters appear |

### 5.8 Delete Job

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click a completed job > "Delete Job" | Confirmation dialog |
| 2 | Confirm | Job removed from list |
| 3 | Try right-clicking a running job | "Delete Job" not available (only terminal states) |

### 5.9 Bulk: Cancel All

| # | Step | Expected |
|---|------|----------|
| 1 | Run `long_task` twice via WebUI | Two running jobs |
| 2 | Click "Cancel All" in Monitor toolbar | Confirmation dialog |
| 3 | Confirm | Both jobs cancelled |

### 5.10 Bulk: Delete All Done

| # | Step | Expected |
|---|------|----------|
| 1 | Ensure multiple done/cancelled jobs exist | — |
| 2 | Click "Delete All Done/Canceled" in Monitor toolbar | Confirmation dialog |
| 3 | Confirm | All done and cancelled jobs removed |

### 5.11 Bulk: Delete All Failed

| # | Step | Expected |
|---|------|----------|
| 1 | Ensure failed jobs exist (run `failing_task` a few times) | — |
| 2 | Click "Delete All Failed/Killed" in Monitor toolbar | Confirmation dialog |
| 3 | Confirm | All failed and killed jobs removed |

### 5.12 Monitor Auto-Refresh

| # | Step | Expected |
|---|------|----------|
| 1 | Keep Monitor view open | — |
| 2 | Submit a job via WebUI or curl | — |
| 3 | Wait ~10 seconds | New job appears in Monitor without manual refresh |

### 5.13 Context Menu Conditions

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click a running job | Shows: View, Cancel, Download |
| 2 | Right-click a done job | Shows: View, Download, Delete |
| 3 | Right-click a failed job | Shows: View, Delete |

---

## 6. Debug Workflow

### 6.1 Start Debug Job from Manager

| # | Step | Expected |
|---|------|----------|
| 1 | Ensure `hello_task` code is synced locally | `pyops/test_set/task/hello_task.py` matches remote |
| 2 | Right-click `hello_task` in Manager > "Start Debug Job" | "PyOps Job Output" channel opens |
| 3 | Observe output channel | stdout streams: "Hello from PyOps!", owner, set, name |
| 4 | Check Monitor | Job with kind `debug` visible, eventually reaches `done` |

### 6.2 Debug with Local Overrides

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `hello_task.py` locally: add `print("LOCAL OVERRIDE")` at the end | File saved |
| 2 | Do NOT write to remote draft | Local-only change |
| 3 | Right-click `hello_task` > "Start Debug Job" | Job submitted with overrides |
| 4 | Observe output | "LOCAL OVERRIDE" appears in stdout |
| 5 | Revert local change | — |

### 6.3 Debug Local-Only Task

| # | Step | Expected |
|---|------|----------|
| 1 | Create a local-only task `debug_local` with `print("debug local only")` | File at `pyops/test_set/task/debug_local.py` |
| 2 | Right-click `debug_local` > "Start Debug Job" | Job submitted with overrides (no remote scriptlet needed) |
| 3 | Observe output | "debug local only" appears |
| 4 | Clean up: delete `debug_local` | — |

### 6.4 Stop Debug Job

| # | Step | Expected |
|---|------|----------|
| 1 | Edit `long_task.py` locally (no changes needed, just ensure synced) | — |
| 2 | Right-click `long_task` > "Start Debug Job" | Output starts streaming ticks |
| 3 | Right-click `long_task` > "Stop Debug Job" | Job cancelled; output stops |
| 4 | Check Monitor | Debug job shows cancelled |

### 6.5 Attach to Debug Job

| # | Step | Expected |
|---|------|----------|
| 1 | Create a task with `debugpy` wait: ensure `hello_task` param includes `#>AWAIT_DEBUG` | — |
| 2 | Start debug job for `hello_task` | Job enters running state, waits for debugger |
| 3 | Click "Attach Debug" button in Manager toolbar | Debugger attaches if exactly one debug job running |
| 4 | Debugger connects | Session name: "Attach to PyOps Debug (test_set::hello_task)" |
| 5 | Job continues execution | Output appears after attach |

### 6.6 Attach -- No Debug Jobs

| # | Step | Expected |
|---|------|----------|
| 1 | Ensure no debug jobs are running | — |
| 2 | Click "Attach Debug" in Manager toolbar | Warning: no debug jobs found |

### 6.7 Attach -- Multiple Debug Jobs

| # | Step | Expected |
|---|------|----------|
| 1 | Start two debug jobs (if possible) | — |
| 2 | Click "Attach Debug" | Warning: multiple debug jobs found |

### 6.8 Debug via Python Launch Config

| # | Step | Expected |
|---|------|----------|
| 1 | Open `pyops/test_set/task/hello_task.py` in editor | — |
| 2 | Press F5 (Start Debugging) with Python selected | `PyOpsPythonDebugConfigProvider` intercepts |
| 3 | Debug job enqueued automatically | Attach config generated with correct path mappings |
| 4 | Debugger attaches to remote job | Breakpoints can be hit in local source |

### 6.9 Debug Launch -- Non-Task File

| # | Step | Expected |
|---|------|----------|
| 1 | Open `pyops/test_set/library/helpers.py` | — |
| 2 | Press F5 | PyOps debug provider does NOT intercept (not a task) |
| 3 | Normal Python debug behavior | Standard Python launch config used |

### 6.10 Debug Port Mismatch

| # | Step | Expected |
|---|------|----------|
| 1 | Change `pyops.username` to `testuser2` (no `debug_port` configured) | — |
| 2 | Authenticate as `testuser2` | — |
| 3 | Start debug job | Error: debug port not configured for user |
| 4 | Revert to `testuser` | — |

---

## 7. Output Channels

### 7.1 PyOps Main Channel

| # | Step | Expected |
|---|------|----------|
| 1 | Open Output panel (Ctrl+Shift+U) | — |
| 2 | Select "PyOps" channel | Extension logs visible: connection status, refresh events |

### 7.2 PyOps Job Output Channel

| # | Step | Expected |
|---|------|----------|
| 1 | Start a debug job | "PyOps Job Output" channel auto-opens |
| 2 | Observe content | Incremental stdout/stderr from running job |
| 3 | Job completes | Output stops updating |

### 7.3 PyOps Job View Channel

| # | Step | Expected |
|---|------|----------|
| 1 | Right-click a completed job > "View Job Details" | "PyOps Job View" channel opens |
| 2 | Content shows | Full stdout and stderr for the job |

---

## 8. Status Bar

### 8.1 Connected State

| # | Step | Expected |
|---|------|----------|
| 1 | With valid config and credentials | `$(check) PyOps: Connected` in green |
| 2 | Click status bar item | Connection refreshes |

### 8.2 Error State

| # | Step | Expected |
|---|------|----------|
| 1 | Stop PyOps server (`docker compose stop app`) | — |
| 2 | Click status bar item or wait for refresh | `$(circle-slash) PyOps: Error` in red |
| 3 | Hover | Tooltip shows connection error details |
| 4 | Restart server (`docker compose start app`) | Status returns to connected after refresh |

### 8.3 Auth Failure Recovery

| # | Step | Expected |
|---|------|----------|
| 1 | Change password in htpasswd to `newpass` | — |
| 2 | Wait for auto-refresh or click status bar | 401 detected; status turns red |
| 3 | Extension clears stored password and re-prompts | Enter `newpass` |
| 4 | Status returns to connected | — |
| 5 | Revert htpasswd to `testpass` | — |

---

## 9. Configuration Settings

### 9.1 Server URL

| # | Step | Expected |
|---|------|----------|
| 1 | Change `pyops.serverUrl` to invalid URL | Status bar shows error |
| 2 | Restore valid URL | Connection re-established |

### 9.2 Timeout

| # | Step | Expected |
|---|------|----------|
| 1 | Set `pyops.timeout` to `1` (1 second) | — |
| 2 | Perform an operation that takes longer | Timeout error |
| 3 | Restore to `30` | Normal behavior |

### 9.3 Local PyOps Directory

| # | Step | Expected |
|---|------|----------|
| 1 | Set `pyops.localPyopsDir` to `custom_pyops` | — |
| 2 | Refresh Manager | Scriptlets downloaded to `custom_pyops/test_set/...` |
| 3 | Revert to `pyops` | — |

### 9.4 Auto-Refresh Interval

| # | Step | Expected |
|---|------|----------|
| 1 | Set `pyops.autoRefreshInterval` to `5` | — |
| 2 | Create a job via WebUI | Appears in Monitor within ~5 seconds |
| 3 | Set interval to `60` | Refresh noticeably slower |
| 4 | Restore to `10` | — |

---

## 10. Remote Content Provider

### 10.1 Python Content

| # | Step | Expected |
|---|------|----------|
| 1 | Diff a Python scriptlet (task/library) | Remote side shows Python syntax highlighting |

### 10.2 HTML Content

| # | Step | Expected |
|---|------|----------|
| 1 | Diff a view scriptlet | Remote side shows HTML syntax highlighting |

### 10.3 JavaScript Content

| # | Step | Expected |
|---|------|----------|
| 1 | Diff a jslibrary scriptlet | Remote side shows JavaScript syntax highlighting |

### 10.4 Markdown Content

| # | Step | Expected |
|---|------|----------|
| 1 | Diff `README` markdown scriptlet | Remote side shows Markdown syntax highlighting |

---

## 11. Multi-User Isolation

### 11.1 Separate User Sessions

| # | Step | Expected |
|---|------|----------|
| 1 | In VS Code window A: configure as `testuser` | Connected |
| 2 | In VS Code window B (separate workspace): configure as `testuser2` | Connected |
| 3 | Window A: run `hello_task` via debug job | Job visible in Window A Monitor |
| 4 | Window B: check Monitor | `testuser`'s job NOT visible |
| 5 | Window B: run `hello_task` via debug job | Only this job visible in Window B |

---

## 12. Command Palette

### 12.1 Command Availability

| # | Step | Expected |
|---|------|----------|
| 1 | Open Command Palette (Ctrl+Shift+P) | — |
| 2 | Type "PyOps" | All 19 PyOps commands listed |
| 3 | Verify categories | All show "PyOps" category |

### 12.2 Commands Without Connection

| # | Step | Expected |
|---|------|----------|
| 1 | Clear `pyops.serverUrl` | `pyops.hasValidConfig` becomes false |
| 2 | Open Command Palette > "PyOps: Create Scriptlet" | Command hidden or disabled |
| 3 | Toolbar buttons in Manager/Monitor | Create, Refresh hidden |
| 4 | Restore config | Commands reappear |

---

## 13. Error Handling

### 13.1 Server Down During Operations

| # | Step | Expected |
|---|------|----------|
| 1 | Stop server; try "Write Local to Remote Draft" | Error message displayed |
| 2 | Try "Diff Local vs Remote" | Error message |
| 3 | Try Refresh Manager | Graceful failure; tree shows empty or stale data |
| 4 | Restart server | Operations resume normally |

### 13.2 Network Timeout

| # | Step | Expected |
|---|------|----------|
| 1 | Set `pyops.timeout` to `1` | — |
| 2 | Try any API operation | Timeout error with clear message |
| 3 | Restore timeout | — |

### 13.3 Invalid API Responses

| # | Step | Expected |
|---|------|----------|
| 1 | Delete a set via WebUI while VSIX shows its scriptlets | — |
| 2 | Try "Write Local to Remote Draft" for a scriptlet from deleted set | Error message (auto-create may succeed or fail gracefully) |

---

## 14. End-to-End Workflows

### 14.1 Full Local Development Cycle

1. Create scriptlet `e2e_task` (task) via VSIX Create command
2. Write code: `print("end to end")`
3. Save locally
4. Write Local to Remote Draft
5. Save Remote Draft
6. Refresh Manager -- shows synced
7. Start Debug Job from Manager
8. Observe "end to end" in output
9. Job completes in Monitor
10. View Job Details from Monitor
11. Delete job from Monitor
12. Delete scriptlet from Manager
13. Verify removed from both VSIX and WebUI

### 14.2 Debug Iteration Cycle

1. Open `hello_task.py` locally
2. Add `print("iteration 1")`
3. Start Debug Job (no need to sync -- overrides used)
4. Observe "iteration 1" in output
5. Change to `print("iteration 2")`
6. Start Debug Job again
7. Observe "iteration 2" -- previous debug job auto-cancelled
8. Revert local changes

### 14.3 Sync and Diff Workflow

1. Edit `hello_task.py` locally: add `# local work`
2. Diff Local vs Remote -- see difference
3. Write Local to Remote Draft
4. Verify in WebUI: draft visible
5. Save Remote Draft from VSIX
6. Diff again -- no difference
7. Edit again locally: add `# more work`
8. Reset Local from Remote -- local change discarded
9. Diff -- no difference

### 14.4 Cross-Tool Consistency

1. Create scriptlet `cross_test` in WebUI
2. Refresh VSIX Manager -- `cross_test` appears, local file downloaded
3. Edit code in WebUI editor, save (Ctrl+S to Git)
4. Reset Local from Remote in VSIX -- local file updated
5. Edit locally, Write to Remote Draft
6. Verify draft in WebUI editor
7. Save draft via WebUI
8. Refresh VSIX -- synced
9. Delete via VSIX -- removed from both

---

## 15. Teardown

| # | Step | Expected |
|---|------|----------|
| 1 | Delete any leftover test scriptlets (vsix_*, e2e_*, debug_local, cross_test) | Cleaned up |
| 2 | Revert any local code changes | `pyops/` directory matches remote |
| 3 | Restore all settings to defaults | — |
| 4 | Continue with WebUI teardown from `test_plan.md` section 15 | Environment cleaned |
