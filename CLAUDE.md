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

**E2E tests** (Playwright, starts real server per test):
```bash
pytest tests/e2e/ -v
```

## Architecture

PyOps is a monorepo execution platform for Python Operations Scriptlets. Core flow:

```
WebUI/VSIX → FastAPI (app/web.py) → SQLite DB ↔ Runner daemon (app/runner.py) → Per-job subprocess: app/job_setup.py (installs under lock) → os.execv → app/job_runner.py (fresh interpreter, exec user code)
```

**Key separation**: `app/runner.py` is the daemon that polls the DB and spawns jobs. Per job it Popens `app/job_setup.py` in the per-set venv, which runs every `#!pip`/`#!<git-url>` directive under `sets.set_install_lock(set)` then `os.execv`s into `app/job_runner.py` (same PID, fresh interpreter — `site.py` ingests any `.pth` from editable installs at startup, so user `import` works on first try). All three talk to the daemon via ZMQ ROUTER/REQ over `/tmp/pyops/runner.sock` (msgpack).

### app/ structure

- `web.py` — FastAPI app; mounts routers, serves static files, proxies controller requests to ZMQ services
- `runner.py` — Background daemon: polls DB for `todo` jobs, spawns `job_setup.py` via `subprocess.Popen` in per-set venvs, handles ZMQ completion signals, manages stale PIDs with psutil
- `job_setup.py` — First-phase subprocess; opens workspace log files in `'w'`, runs `process_code_installs` under `set_install_lock`, on success `os.execv`s into `job_runner.py`, on failure sends ZMQ completion `state="failed"` and exits 1
- `job_runner.py` — Second-phase subprocess (post-execv, same PID, fresh interpreter); opens workspace log files in `'a'` so install logs are preserved; dispatches to `run_task_job`, `run_service_job`, `run_debug_job`, `run_draft_job`
- `gunicorn.py` — WSGI config; also spawns the runner process; reads `PYOPS_CONFIG` env var for config path override (default `etc/pyops.json`)
- `routers/scriptlet/` — REST API: `job.py`, `editor.py`, `inventory.py`, `schedule.py`, etc. (no `sets.py` — sets management is a controller, see `app/internal/`)
- `internal/` — Pyops-internal scriptlet bundle (sets controller + data service + six `set_*` sub-tasks + jslib modal). Reached at `/controller/internal/sets`. Runs in the pyops host venv (not a per-set venv) and is the canonical place to add new self-hosted admin operations.
- `routers/monitoring.py` — top-level (not under `/scriptlet/`), unauthenticated `/health`, `/health/failed`, `/metrics` for external monitoring (Zabbix/Prometheus)
- `orm/` — SQLAlchemy models: `Job` (states: todo→dequeued→loading→running→done/failed/cancelled/remove/removed) and `Schedule` (cron-based)
- `lib/` — Shared utilities

### lib/ key modules

- `runner.py` — Shared job execution utilities used by `runner.py`, `job_setup.py`, and `job_runner.py`: `send_completion`, `terminate_debugpy_adapter`, `process_imports`, `process_install_lines`, `process_code_installs` (acquires `sets.set_install_lock` for whole call), `kill_proc_tree`, `get_process_tree`
- `pyops.py` — `Control` (API proxy for scriptlets) and `ServiceListener` (ZMQ service IPC); available in scriptlet global namespace
- `git.py` — Git operations for scriptlet storage; serialized via `repo_lock()` per set; scriptlets stored as `{name}.{type}.py` in `data/git/{set}/`
- `files.py` — Config (`etc/pyops.json`) access: `conf_data()`, `conf_runner()`, `conf_web()`, `conf_log()`, `conf_users()`; supports dynamic reload
- `sets.py` — Runner-side per-set venv primitives only (`ensure_set_environment`, `set_install_lock`, bootstrap-lock helpers, `CORE_DEPS`); the user-facing clone/remove/switch/pull/create/update-deps operations are scriptlets under `app/internal/`

### Config isolation in subprocesses

Both `job_setup.py` and `job_runner.py` receive config as the same JSON-serialized kwargs on the command line — `job_setup.py` passes them through to `job_runner.py` verbatim via `os.execv`. No file reads or module patching in subprocess. Relative paths are resolved using a locally computed `project_root`.

### Debug mode

Runner no longer injects a debugger. The client (VSIX, pyopsctl, anything else) composes the debugger bootstrap into `job.param` — VSIX emits a debugpy `listen(..., in_process_debug_adapter=True) + wait_for_client() + breakpoint()` block; pyopsctl emits a `RemotePdb(...).set_trace()` block. Pyops's debug-kind contribution is (a) source materialisation under `.debug/src/<set>/{task,library}/<name>.py` so path-mapping debuggers can resolve `{set}::{name}`, (b) no timeout, no retry. The `running` transition fires when `exec(param)` returns — universal across task/service/draft/debug. Port allocation is a client-side concern (`pyops.debugPorts` in VSIX settings, `debug_ports = [...]` in pyopsctl's profile); the server has no `/debug/port` route.

### VSIX (vsix/)

VSCode extension in TypeScript. Key services: `ManagerTreeDataProvider` (scriptlets), `JobsTreeDataProvider` (jobs), `DebugService` (build overrides + enqueue debug), `AuthService` (Basic Auth). Local-only scriptlets (not yet pushed to remote) are discovered from `localPyopsDir/pyops/{set}/{type}/` and can be debug-run via `overrides` that bypass remote existence checks.

## Rules

- **No fixes or workarounds** in `app/` or `vsix/` — fix root causes
- **Test imports** — only import from `app` within the test method body, not at module level
- **Test fixtures** — never `autouse`; ensure isolation and cleanup; don't overlap; inherit if meaningful
- **Test runtime files** — store in `.cache/`
- **Coverage targets** — Python tests >90%; VSIX tests >80%
- **E2E tests** — skipped by default in `pytest tests/`; run via `pytest tests/e2e/` or `pytest tests/ --e2e`

## Technical Insights (docs/)

[docs/insights_app.md](docs/insights_app.md) and [docs/insights_vsix.md](docs/insights_vsix.md) contain deep technical details on the app and VSIX respectively. These files **must be kept up to date** when relevant code changes. They describe **current technical state only** — no historical records, changelogs, or "was changed from X to Y" entries.
