# Changelog

All notable changes to PyOps. Versions prior to 3.0.0 were tech preview
and are not documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from 3.0.0 onward.

## [3.1.0] — 2026-05-16

Adds `pyopsctl`, the operator CLI. Designed to be driven from a
terminal by both a human operator and agents (LLM-driven or otherwise)
and shell scripts — no venv, no pip-installed Python deps, distribution
packages only (`python3` + `python3-requests`). Installed via
`./ctl/install.sh` which symlinks the launcher into
`$XDG_BIN_HOME` (defaults to `~/.local/bin/`).

### Added

- **`pyopsctl` CLI** at `/ctl/`, parallel to `app/` and `vsix/`.
  Stdlib `argparse` dispatch, no Click/Typer. Speaks the existing
  pyops HTTP API; no new server endpoints introduced.
- **Per-profile credentials** in `~/.config/pyops/config.toml` (mode
  0600, refused on broader perms). Each profile carries `url`, `user`,
  and optional `api_key`. The auth pathway is implicit: `api_key`
  present → `Authorization: Basic base64(user:api_key)` (nginx in
  front of pyops decodes and injects `X-User`); `api_key` absent → no
  `Authorization`, `X-User` falls through to `profile.user` or
  `"pyopsctl"`. Useful for unproxied dev and bootstrap before
  credentials exist.
- **Detached debug jobs**, the load-bearing feature VSIX can't reach.
  `pyopsctl debug start SET NAME` defaults to a detached debug job —
  debugpy listens, the runner does not `wait_for_client()`. `--await`
  injects `#>AWAIT_DEBUG`. `--param TEXT` / `--param-file PATH` carry
  an arbitrary multi-line parameter (treated as Python script that may
  itself include `#`-prefixed directives like `#!pip foo`). When both
  flags are given the merge prepends `#>AWAIT_DEBUG\n` so the user's
  body stays a valid script with its own directives intact.
- **Command groups** covering: `scriptlet` (list/sets/status/repo-status/
  get-code/create/draft/save/delete/diff); `job`
  (list/view/run/cancel/delete/download/cancel-all/clean); `debug`
  (start/attach/port/stop); `health` / `metrics` / `version`; `config`
  (show/check).
- **Three output modes** — default (human, aligned columns), `--json`
  (server payloads passed through verbatim; multi-call composites
  wrapped as `{"data": …, "meta": …}`), and `--quiet` (IDs only, one
  per line; designed for shell pipelines).
- **Exit codes** — `0` ok, `2` usage, `3` config/auth, `4` HTTP 4xx,
  `5` HTTP 5xx, `6` timeout, `7` server-unreachable.
- **Test coverage**: 42 unit tests (config, client, output, the merge
  truth-table, argparse-to-request-body wiring) + 10 integration tests
  (pyopsctl ↔ pyops contract against a real server) + 3 e2e tests
  (pyopsctl + pyops + debugger TCP client interplay; verifies the
  runner actually opens debugpy's listener on the reported port and
  tears it down on `debug stop`). New CI stage `test:pyopsctl` runs
  all three suites. The existing `test:`, `test:e2e`, `test:vsix*`,
  `build:`, `build:image:`, and `release:` stages are untouched.

### Documentation

- New `docs/pyopsctl.md` — installation, config example, full command
  reference, worked operator + agent flows.
- `ctl/README.md` — prerequisites + `install.sh` usage on the
  operator's box.

## [3.0.0] — 2026-05-15

First stable release. Highlights below; full technical detail in
`docs/insights_app.md` and `docs/insights_vsix.md`.

### Added
- **Per-instance branding** via the `brand` block in `etc/pyops.json`:
  `title` (browser tab + sidebar/topbar header, HTML-escaped),
  `icon` (material-icons-round glyph name *or* a path to an image — SVG
  files are inlined so they inherit the brand text color via
  `currentColor`, raster files are served via the new `/brand/icon`
  route), and `favicon` (optional override for `/favicon.ico`). Defaults
  reproduce the bundled pyops look so existing deployments need not
  opt in.
- **Internal scriptlet set** (`app/internal/`) bundling the admin
  operations pyops needs to run itself: sets controller, always-on
  `data` service, and the six per-operation sub-tasks (`set_clone`,
  `set_create`, `set_pull`, `set_switch`, `set_remove`,
  `set_update_deps`). Reached at `/controller/internal/sets`. Runs in
  the pyops host venv, is hidden from `list_sets`, and is
  write-protected from inventory/editor/sets-remove operations.
- **Per-set "Update Dependencies"** flow that scans all scriptlets for
  `#!pip` / `#!<git-url>` directives, resolves pip/git conflicts via
  newest-mtime-wins, pre-empts impossible pip-resolver constraints, and
  writes `<venv_base>/{set}/.update_warnings.json` so `/health` can
  surface dependency conflicts as `degraded`.
- **Monitoring endpoints** intended for external scraping:
  `GET /health` (aggregated status + per-subsystem detail incl. ulimit
  utilisation, GPU, dependency-conflict surface, runner socket),
  `GET /health/failed` (every failed job in detail for filtering),
  `GET /metrics` (Prometheus exposition with per-state job counts,
  per-(set, name, state) breakdown, ulimit gauges, GPU status,
  `pyops_oldest_todo_age_seconds`, `pyops_last_completed_seconds`).
- **`/version` endpoint** returning the project-root `VERSION` file —
  the single source of truth shared between the Python app and the
  VSIX (`vsix/build.sh` rewrites `package.json` from it; the version
  unit test asserts equality, so drift fails CI).
- **Collapsible mobile-friendly sidebar** with a configurable list of
  default-expanded sets (`web.expanded_sets`).
- **Dark-mode UI** with system-preference detection and per-element
  Bootstrap 5.2 overrides at the controller wrapper so views don't
  need per-element class swaps. The rules live in
  `docs/pyops_rules.md`.
- **Controller-proxy preview routing**: a controller registered as
  `debug=True` falls back to the matching `draft`-kind service when no
  `debug`-kind service is running, so a Manager-started preview is
  reachable from the same controller URL.
- **VSCodium extension** (`vsix/`) — full lifecycle: manager tree,
  jobs tree, debug-with-overrides, content provider, connection
  status, scriptlet editor integration. Unit and integration tests
  ship under `vsix/tests/`.

### Changed
- **Default `users.anonymous.debug_port`** is now **45678** (was
  15678). Avoids collisions with common dev tooling and the pyops
  Docker `:15678:15678` port forward typical in multi-instance setups
  on the same host. Existing deployments with their own `pyops.json`
  are unaffected.
- **Job-execution split**: `runner.py` Popens `app/job_setup.py` per
  job, which runs every `#!pip`/`#!<git-url>` directive under
  `sets.set_install_lock(set)` then `os.execv`s into
  `app/job_runner.py` — same PID, fresh interpreter, `site.py`
  ingests `.pth` entries from editable installs at startup so user
  `import` works on first try.
- **Service auto-restart gating**: any prior `cancel` / `cancelling` /
  `cancelled` row blocks re-enqueue of that service until the operator
  clears it. Applies to both `INTERNAL_ESSENTIAL_SERVICES` and
  schedule-row services with an empty cron, so an operator's "cancel"
  is never silently undone by the auto-restart loop.
- **Controller proxy** returns `503` (with a structured `{error,
  set, name}` payload) instead of raising when the bound controller
  service is not running, so client UIs can show a clear "service not
  available" state.

### Removed
- **`/scriptlet/sets/*` REST router** and `app/sites/sets.html`. Sets
  management is now an internal scriptlet bundle reached at
  `/controller/internal/sets` (see "Internal scriptlet set" above).
- **`app/lib/sets.py`** is trimmed to runner-side per-set venv
  primitives (`ensure_set_environment`, `set_install_lock`,
  bootstrap-lock helpers, `CORE_DEPS`). The user-facing
  clone/create/pull/switch/remove/update-deps operations live in
  `app/internal/` as scriptlets.

### Fixed
- **Clean container shutdown**. `run.sh` now `exec`s gunicorn so
  Docker's SIGTERM reaches the gunicorn master directly instead of
  being swallowed by bash at PID 1. The shutdown sweep deletes
  clean-exit service rows so the schedule loop re-enqueues them on
  next boot; SIGKILL'd survivors stay as `failed`; operator cancels
  stay as `cancelled`.
- **Terminal-state preservation across duplicate completions**.
  `finishJob`'s CAS preserves all terminal states (`cancel`,
  `cancelling`, `cancelled`, `done`, `failed`, `remove`, `removed`)
  so a late duplicate `done`/`failed` arriving after `remove` cannot
  demote the row.
- **Startup-incomplete row deduplication**. `handleOddJobs` now
  dedups `"incomplete execution detected on startup"` rows per
  `(set, name, owner, kind)` so repeated unclean restarts no longer
  accumulate against the per-service `<3 failed` gate.
- **Sidebar double-click on initial load**. `setActive()` flipped the
  global `goto` mid-loop, causing the post-loop branch to fire a
  second navigation on top of the default-item click. Snapshotting
  the URL `goto` before the loop fixes the visible flash and slower
  first paint.
- **Stale 'running' service rows after unclean restart**. The
  `purgeLeftoverServiceJobs` cleanup is trimmed to `remove`/`removed`
  rows only, so `failed`/`done`/`cancelled` survive restarts and
  feed the cancel/`<3 failed` gates.

### Operations
- **Compose deployments must set `stop_grace_period: 180s`** (or
  higher). Docker's default 10s SIGKILLs the container before the
  shutdown chain (workers drain + runner `_shutdown_job_processes`,
  worst-case ≈ 135s) finishes, leaving service rows stuck `running`.
- **Compose deployments must raise `ulimits.nofile.soft/hard` to
  65535**. The Docker default (1024 soft historically) is the first
  wall pyops hits — between gunicorn workers, the runner subprocess,
  per-job log streams and ZMQ sockets the FD count climbs quickly,
  and `/health` flips to `degraded` once the `pyops_open_fds /
  pyops_max_fds_soft` ratio crosses the warning threshold.
- **Cross-process shutdown flag** at `/tmp/pyops/shutdown.flag` gates
  `lib_runner.enqueue` against gunicorn-worker and data-service
  callbacks, so no task or service is enqueued or re-enqueued while
  the system is shutting down.

See the README for the full compose example with both settings and
the `brand` block.
