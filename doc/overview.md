# PyOps Overview

## Introduction
The project is called PyOps. It is licensed under the terms of GNU AGPL v3 Only. It was originally built as a simple tool to provide easy python leveraging in IT infrastructure.

### Structure
The monorepo is organized into a flat @/app directory for core functionality, with clear separation of concerns:
- **lib/**: Utility modules (e.g., @/app/lib/files.py for config/asset loading, @/app/lib/pyops.py for ZMQ-based service control, @/app/lib/runner.py for job queuing).
- **orm/**: SQLAlchemy models (@/app/orm/job.py, @/app/orm/schedule.py) defining entities like Jobs, Schedules, and Scriptlets (with revisions/drafts).
- **routers/scriptlet/**: FastAPI sub-routers for API endpoints (e.g., @/app/routers/scriptlet/inventory.py for CRUD on scriptlets, @/app/routers/scriptlet/job.py for job lifecycle, @/app/routers/scriptlet/schedule.py for cron-based scheduling).
- **sites/**: Static HTML templates (e.g., @/app/sites/scriptlet/manager.html, @/app/sites/scriptlet/monitor.html) for UI views, using Bootstrap and Ace editor.
- **web.py**: Main FastAPI app assembling routers and handling dynamic rendering (e.g., controller/view execution).
- **runner.py & gunicorn.py**: Background job executor and deployment config (spawns processes, creates DB tables).
- **files/**: Assets (CSS/JS, e.g., Bootstrap, jQuery, Ace for code editing).

- **vsix/**: VSCodium extension providing IDE integration for PyOps, including tree providers for jobs and extension activation (@/vsix/src/extension.ts, @/vsix/src/jobsTreeProvider.ts).

This layout supports quick navigation: web/UI → API → persistence → execution.

### Architecture
PyOps follows a layered, event-driven microservice-like design for scalable IT ops scripting:
- **Presentation Layer**: FastAPI serves REST APIs and renders HTML (e.g., dynamic controllers/views via `exec()` on scriptlets). UI is single-page with iframes for modularity.
- **API Layer**: Routers handle CRUD (e.g., `/scriptlet/inventory/item` for scriptlet management) and job ops (e.g., enqueue via `PUT /scriptlet/job/item`).
- **Business Logic Layer**: ORM for data ops; @/app/runner.py as a daemon process polls DB for "todo" jobs, spawns isolated subprocesses (with venv activation), and uses ZMQ IPC for service communication.
- **Persistence Layer**: SQLite DB (via SQLAlchemy) stores jobs, schedules, and scriptlet code/revisions; supports drop-on-success for cleanup.
- **Execution Layer**: Scriptlets (Python code) run in virtualenv-isolated processes; supports libs via `!{set::name}` imports, pip/git installs on-the-fly.
- **Deployment**: Gunicorn + Uvicorn workers; runner spawns on startup for async job handling.

Flow: User creates/edits scriptlet → Enqueue job/schedule → Runner polls/executes → Monitor results via API/UI.

### Technical Details
- **Stack**: Python 3.12 (FastAPI, SQLAlchemy 2.x, ZMQ, msgpack); JS (jQuery, Bootstrap 5, Ace editor with Jedi autocompletion); SQLite (file-based, thread-safe).
- **Key Mechanisms**:
  - Job States: todo → dequeued → running → done/failed/cancelled/remove (with PID tracking, logs in workspace dirs).
  - Scheduling: Croniter for timed tasks; auto-start services if none running (up to 3 retries on failure).
  - Security/Isolation: Per-job workspaces under `data/jobs/{owner}/{set}/{id}`; venv per-run for deps (pip installs from `!pip module` lines).
  - Error Handling: HTTPExceptions (404/409/500); traceback logging; graceful shutdown via signals.
  - Config: JSON (@/etc/pyops.json) for binds, logs, paths; loaded dynamically.

### Highlighted Features
- **Scriptlet Editor**: In-browser Ace with live autocompletion (Jedi), draft/revisions, param preview, and run/debug (stdout/stderr streaming).
- **Job Management**: Queue, cancel, download ZIP workspaces; auto-drop done jobs; service mode for persistent listeners.
- **Scheduling**: Cron-based or always-on services; activate/deactivate; manual triggers.
- **Monitoring**: Real-time job lists with actions (cancel/del/copy-param); bulk ops (e.g., del-all-done).
- **Dynamic UI**: Controller/views as executable Python templates; menu auto-built from DB.
- **Extensibility**: Types (task/library/controller/view); git/pip integration for deps; ZMQ for inter-service comms.
