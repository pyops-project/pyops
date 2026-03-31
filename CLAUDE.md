# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Activate the virtual environment before any command:
```bash
. ./.venv/bin/activate
```

## Commands

**Development server** (with hot reload):
```bash
./run_dev.sh
```

**Run all Python tests** (with coverage):
```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

**Run a single test**:
```bash
pytest tests/path/to/test_file.py::test_function_name -v
```

**VSIX unit tests**:
```bash
npm run test --prefix vsix/
```

**VSIX integration tests** (requires running PyOps on localhost:8000):
```bash
npm run test:integration --prefix vsix/
```

## Architecture

PyOps is a monorepo execution platform for Python Operations Scriptlets. Core flow:

```
WebUI/VSIX → FastAPI (app/web.py) → SQLite DB ↔ Runner daemon (app/runner.py) → Isolated subprocess (app/job_runner.py)
```

**Key separation**: `app/runner.py` is the daemon that polls the DB and spawns jobs. `app/job_runner.py` is the subprocess entry point that executes inside the per-set venv. They communicate via ZMQ ROUTER/REQ over `/tmp/pyops/runner.sock` (msgpack-packed messages).

### app/ structure

- `web.py` — FastAPI app; mounts routers, serves static files, proxies controller requests to ZMQ services
- `runner.py` — Background daemon: polls DB for `todo` jobs, spawns `job_runner.py` via `subprocess.Popen` in per-set venvs, handles ZMQ completion signals, manages stale PIDs with psutil
- `job_runner.py` — Subprocess entry point; accepts JSON-serialized kwargs; dispatches to `run_task_job`, `run_service_job`, `run_debug_job`, `run_draft_job`
- `gunicorn.py` — WSGI config; also spawns the runner process
- `routers/scriptlet/` — REST API: `job.py`, `editor.py`, `inventory.py`, `schedule.py`, `sets.py`, etc.
- `orm/` — SQLAlchemy models: `Job` (states: todo→dequeued→loading→running→done/failed/cancelled/remove/removed) and `Schedule` (cron-based)
- `lib/` — Shared utilities

### lib/ key modules

- `runner.py` — Shared job execution utilities used by both `runner.py` and `job_runner.py`: `send_completion`, `terminate_debugpy_adapter`, `process_imports`, `process_install_lines`, `process_code_installs`, `kill_proc_tree`, `get_process_tree`
- `pyops.py` — `Control` (API proxy for scriptlets) and `ServiceListener` (ZMQ service IPC); available in scriptlet global namespace
- `git.py` — Git operations for scriptlet storage; serialized via `repo_lock()` per set; scriptlets stored as `{name}.{type}.py` in `data/git/{set}/`
- `files.py` — Config (`etc/pyops.json`) access: `conf_data()`, `conf_runner()`, `conf_web()`, `conf_log()`, `conf_users()`; supports dynamic reload
- `sets.py` — Git repo set management (clone/remove/switch branch)

### Config isolation in subprocesses

`job_runner.py` receives config as JSON-serialized kwargs on the command line — no file reads or module patching in subprocess. Relative paths are resolved using a locally computed `project_root`.

### Debug mode

Runner injects `debugpy.listen(('0.0.0.0', port))` — intentionally binds all interfaces for Docker remote debugging. If `#>AWAIT_DEBUG` is in `job.param`, appends `wait_for_client()`. VSIX sends `param='#>AWAIT_DEBUG'` for the attach workflow. The `/debug/port` endpoint returns the port for the requesting user from `conf_users()[owner]["debug_port"]`.

### VSIX (vsix/)

VSCode extension in TypeScript. Key services: `ManagerTreeDataProvider` (scriptlets), `JobsTreeDataProvider` (jobs), `DebugService` (build overrides + enqueue debug), `AuthService` (Basic Auth). Local-only scriptlets (not yet pushed to remote) are discovered from `localPyopsDir/pyops/{set}/{type}/` and can be debug-run via `overrides` that bypass remote existence checks.

## Rules

- **No fixes or workarounds** in `app/` or `vsix/` — fix root causes
- **Test imports** — only import from `app` within the test method body, not at module level
- **Test fixtures** — never `autouse`; ensure isolation and cleanup; don't overlap; inherit if meaningful
- **Test runtime files** — store in `.cache/`
- **Coverage targets** — Python tests >90%; VSIX tests >80%

## Technical Insights (doc/)

[doc/insights_app.md](doc/insights_app.md) and [doc/insights_vsix.md](doc/insights_vsix.md) contain deep technical details on the app and VSIX respectively. These files **must be kept up to date** when relevant code changes. They describe **current technical state only** — no historical records, changelogs, or "was changed from X to Y" entries.
