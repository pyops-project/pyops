# Job Polling and Execution Mechanisms

app/runner.py runs as a background daemon (invoked via app/runner.py main), handling job polling, execution, and scheduling with graceful shutdown via GracefulKiller (SIGINT/TERM sets kill_now).

**Main Loop**:
- Polls DB every 0.5s for oldest "todo" job (getNextToDo sets "dequeued").
- On startup, handleOddJobs: Resets "dequeued" to "todo", marks stale "running" as "failed" with detail.
- Loads job via loadJob (from lib/runner.py): Queries DB, infers type from git filename, reads code (committed if not debug/service), processes param/code for !{set::name} imports (process_imports appends lib_code to libs list via loadLib git read), sets "running"/start timestamp.
- Spawns multiprocessing.Process(target=run):
  - Builds workspace data/jobs/{owner}/{set}/{id} (makedirs if needed).
  - Sets sys.path[0]=pkg_path (for git clones), sys.path[1]=venv site-packages.
  - Redirects stdout/stderr to workspace/{stdout,stderr}.log (unbuffered).
  - Processes ! lines (process_install_lines): For !pip module (install -q if not in freeze), !git url [branch] (clone/pull to pkg/{sanitized_name}, sanitized for import via re.sub non-alnum to _).
  - Re-processes param/libs/code lines for !pip/!git post-lib load.
  - For service/debug: Instantiates pyops.ServiceListener(id) (ZMQ REP ipc://{temp/pyops/{id}.sock}, poller 1s timeout, signals).
  - exec(param) then each lib then code in glob={"userspace":..., "setspace":..., "workspace":..., "control": pyops.Control(web_bind), "listener": listener}.
  - Queue.put({"id":, "state": "done"/"failed", "detail": None/traceback}) on exit/exception.
- Assigns PID via assignJob, tracks in processes dict.
- Handles queue: finishJob sets end/detail/PID=None, "remove" if drop_done & done & not debug/service (else state=done/failed).
- Terminates on cancel/remove: getNextToTerm polls DB, term (terminate process), postTerm (canceled/end or delete+drop if remove: rmtree workspace, remove sock, rmtree empty parents).
- Cleans processes (remove dead), disposeJobs (delete "removed" from DB).

**Scheduling (schedule())**:
- For active schedules: If cron (croniter.get_prev > last), enqueue task via lib_runner.enqueue, update last=prev.
- For always-on (no cron): Enqueue service if no todo/dequeued/running/cancel/canceled/done (via find), and <3 fails.

**Git Integration**:
- Periodic pulls (every conf_runner.git_pull_interval s): For each set in data/git/, pull_if_needed (git pull if interval since last).

**Shutdown**:
- On kill_now: Terminate alive processes, join(3s), kill remaining; drain queue, disposeJobs.

lib/runner.py provides supporting DB ops: enqueue (create Job todo/param), query/get, find (filter states), cancel/remove (set state), drop (rmtree workspace/sock + empty parents), loadLib/loadJob (git read + state running/start).

Mechanisms emphasize isolation (workspaces/venvs/PIDs), fixed polling (no priority/queues), single-instance (no sharding), exec-based execution.

## Enhanced Termination
- **Cancellation Handling**: When job state set to "cancel" via API, daemon polls via getNextToTerm, calls term(): sends SIGTERM to process, polls is_alive every 0.1s for up to 5s timeout. If still alive, sends SIGKILL, waits 1s more.
- **State Updates**: postTerm sets state to "cancelled" with detail "terminated gracefully" if exited within timeout; "killed" with "force killed after 5s timeout" if force-killed. For "remove", deletes job and drops workspace/sock.
- **Shutdown Integration**: Applies same timeout/kill logic to all running processes before exiting, updating states accordingly.
- **Debug/Service Jobs**: ZMQ listener closed via process termination; sock removed in drop if "remove".
