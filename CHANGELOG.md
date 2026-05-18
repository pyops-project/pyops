# Changelog

All notable changes to PyOps. Versions prior to 3.0.0 were tech preview
and are not documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from 3.0.0 onward.

## [Unreleased]

### Added
- **Sets view: "commit drafts" per-set action.** New `check`-icon button
  alongside pull / update-deps / switch / remove that stages, commits
  and pushes every unstaged scriptlet in the set in one batch. The
  button is enabled only when `git status --porcelain` reports drafts;
  clicking it opens a modal that previews the porcelain output and
  accepts an optional commit message (empty → `Commit drafts in <set>
  by <user>`). Backed by a new `set_commit_drafts` task in the internal
  set; routes through the data service at
  `/controller/internal/sets/commit_drafts/<name>`. Refuses on a tag;
  no-op when nothing is unstaged; skips push when no remote.
- New data-service endpoint `GET /controller/internal/sets/status/<name>`
  returning the porcelain status (powers the modal preview), and a new
  `has_drafts: bool` field on every `/list` row.
- **Manager view: "Run Draft" per-task action.** A new `play_arrow`-icon
  button next to delete/edit on every task row enqueues a `kind=draft`
  job (empty `param`, `drop_done=false`) via `PUT /scriptlet/job/item`.
  Lets the operator iterate-run without opening the editor; output is
  inspected from the Jobs view.
- **Zsh tab completion for `pyopsctl`.** `ctl/install.sh` now generates
  `~/.local/share/zsh/site-functions/_pyopsctl` via
  `register-python-argcomplete` and appends a marker-tagged
  `fpath=(…)` line to `~/.zshrc` (idempotent; uninstall removes both).
  Requires the optional `python3-argcomplete` distro package; absent it
  the install step is a no-op with a one-line hint. `pyopsctl.__main__`
  gained `argcomplete.autocomplete(parser)` wrapped in `try/except
  ImportError`, so the CLI runs unchanged on hosts without the package.

### Fixed
- **`ServiceListener.async_poll()` honours SIGTERM.** Pre-fix, the
  non-blocking poll never checked `kill_now`, so causal-style services
  (e.g. `system::os_service`) kept polling past SIGTERM until the
  runner's 15s SIGKILL escalation marked the row `failed`. `async_poll`
  now returns `[(b"", "TERM")]` once the signal handler has flipped
  `kill_now`, mirroring `listen()`'s `"TERM"` return. Regression tests
  at all three layers (`tests/unit/lib/test_lib_pyops.py`,
  `tests/integration/lib/test_zmq_integration.py`,
  `tests/e2e/test_shutdown.py::test_async_poll_service_acks_term_within_grace_window`).

### CI
- `test:e2e` step now also runs `tests/pyopsctl/e2e/`; `test:pyopsctl`
  step is scoped to `tests/pyopsctl/unit tests/pyopsctl/integration`
  only. Root-level `tests/conftest.py` carries the `--e2e` flag and the
  auto-skip for both e2e dirs.
- `python3-argcomplete` added to the `test:e2e` and `test:pyopsctl`
  before_script apt-install lines so the optional completion path is
  exercised under CI.

### Removed
- **Per-file commit/push is gone.** The inventory's
  `POST /scriptlet/inventory/item` route (the only "save and commit"
  pathway) is deleted; `PUT` (create) and `DELETE` no longer commit —
  both leave the change as an unstaged working-tree edit that
  `set_commit_drafts` picks up. The corresponding clients went with it:
  VSIX's `pyops.saveRemoteDraft` command, the Manager view's per-row
  save button + `saveRevision`, the editor templates' Save button
  (drafts still auto-save every 1s), and pyopsctl's `scriptlet save`
  subcommand.
- **The whole commit/push surface in `app.lib.git`.** Removed
  `_add_commit_push_locked`, `perform_git_add_commit_push`,
  `batch_commit`, and `delete_file_and_commit`. `create_scriptlet` no
  longer takes a `commit_msg`. New `delete_file_unstaged` replaces the
  old delete contract (working-tree only). The internal `set_*` tasks
  drive every commit/push directly via `git` subprocess invocations
  under `repo_lock` — pyops's FastAPI surface no longer touches the
  index.

### Changed
- **Debugger injection moved from pyops to the client.** `kind=debug` no
  longer imports debugpy, installs it, or starts a listener. The client
  (VSIX, pyopsctl, or any other operator surface) writes the debugger
  bootstrap into `param` — typically a `debugpy.listen() / wait_for_client()
  / breakpoint()` block, but also `gdbserver --attach $$ :<port>`,
  `cuda-gdbserver`, `lldb-server`, or anything else that speaks a wire
  the consumer understands. pyops's role shrinks to:
  - `kind=debug` lifecycle: no timeout, no retry.
  - Source-on-disk materialisation under `.debug/src/<set>/{task,library}/<name>.py`
    so any source-path-mapping debugger works without per-debugger plumbing.
  - The `running` state transition: now fires on a `state=running` ZMQ
    completion sent by the job process the moment `exec(param)` returns,
    rather than on a `has_debugpy_adapter(port)` poll. Universal — task,
    service, draft, and debug all flip `loading` → `running` via the
    same mechanism.

### Removed
- `GET /debug/port` HTTP route and the `users.<owner>.debug_port` config
  field. Port allocation is a client-side concern — each operator runs
  with their own port pool, configured in the client.
- `app/lib/job.py`: `get_debug_port`, `has_debugpy_adapter`,
  `get_debugpy_adapter_pid`, `terminate_debugpy_adapter`,
  `validate_debug_prerequisites`.
- `Job.debugpy_pid` ORM column (still present in existing SQLite files;
  inert, not read).
- `debugpy` from `CORE_DEPS` in `app/lib/sets.py` and
  `app/internal/set_update_deps.task.py`. Debug-kind clients pull
  debugpy via a `#!pip debugpy` directive in `param` when they need it.
- The `#>AWAIT_DEBUG` substring check in the runner. The directive is now
  meaningless to pyops; clients that still emit it (current pyopsctl,
  current VSIX) get it carried verbatim through the job's `param`,
  ignored by the runtime.
- `has_debugpy_adapter` 5-second readiness poll in the main loop.
  Debug jobs spawn exactly like task/service/draft.

### Changed (pyopsctl)
- **pyopsctl debug now drives stdlib pdb instead of DAP.** The CLI
  composes a remote-pdb bootstrap into the job's `param`, leases a TCP
  port from the profile's `debug_ports` pool, and on `attach` spawns
  a session daemon that holds the TCP connection to remote-pdb for
  the lifetime of the session. Rationale: pyopsctl is terminal/agent-
  shaped; pdb's text REPL maps 1:1 to the CLI verbs and has zero
  external client-library dependency. The DAP path that VSIX uses
  (debugpy + VSCode's internal client) is unaffected — that pathway
  rewrite ships separately.
- New profile field `debug_ports = [...]` in
  `~/.config/pyops/config.toml`. Lease store at
  `$XDG_RUNTIME_DIR/pyops/debug-ports/<profile>/<port>.lease`
  (operator-only, mode 0600), concurrent-safe via `O_CREAT|O_EXCL`,
  stale-lease reclaim via server job-state check.

### Removed (pyopsctl)
- `ctl/pyopsctl/dap/` (DapClient + wire) — replaced by
  `ctl/pyopsctl/pdb/client.py` (PdbClient).
- `ctl/pyopsctl/session/resolve.py` — friendly-name ↔ DAP frame path
  resolver. No longer needed: pdb sees real `.debug/src/...` paths
  directly; reverse-mapping is a single regex in the daemon.
- `pyopsctl debug` verbs `pause`, `var`, `threads`. Pause needs a
  server-side signal channel that doesn't exist yet; `var` (DAP
  variablesReferences) has no pdb equivalent — operator drills with
  `evaluate 'obj.attr'`; `threads` would need a multi-thread pdb
  fork, not in scope.
- `commands/debug._merge_debug_param` and the `#>AWAIT_DEBUG`
  directive: param is now composed as the pdb bootstrap + (optional)
  user preamble appended below it.
- `GET /debug/port` HTTP hop (the route is gone server-side anyway).
  pyopsctl resolves the port locally via `ports.find_port_for_job`.

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

- **Interactive debugger control** layered on top of `debug attach`:
  the CLI spawns a long-lived **session daemon** that holds the DAP
  TCP connection to debugpy on the pyops runner. State dir under
  `$XDG_RUNTIME_DIR/pyops/sessions/<job_id>/` (or `~/.cache/...`);
  per-job Unix domain socket carries newline-delimited JSON ops.
  Subsequent CLI verbs run against that daemon:
  - `state` / `stack` / `locals` / `globals` / `var --ref N` /
    `evaluate "EXPR"` for inspection
  - `break set/list/remove` for breakpoints (line + optional condition)
  - `step` / `step-in` / `step-out` / `continue` / `pause` for control
  - `threads` / `source <set>::<name>` for context
  - `detach` / `terminate` for tearing down
  - `session list` / `session info` for daemon-state visibility

  Inline DAP client under `ctl/pyopsctl/dap/` (~300 LOC, stdlib socket
  + json) handles the `Content-Length`-framed wire and the debugpy
  bootstrap quirk where the `attach` response is held until
  `configurationDone` arrives.

  The resolver learns `<set>::<name>` ↔ absolute-path mappings from
  stack frames as stops arrive (debugpy's `loadedSources` isn't
  reliable). `break set` against a not-yet-known friendly ref stores
  a pending breakpoint and applies it when a future stop brings the
  path in.

- **Vertical state-dump output** (default human mode) for every
  callstack-changing verb: four sections separated by 60-hyphen
  rules — `globals`, `locals`, `code (set::name)` with `→` on the
  current line, `callstack` (frame 0 first). 20-line code window
  centered on the current line; a separate `debug source <set>::<name>`
  prints the full file. `--json` and `--quiet` remain available for
  tooling.

### Known limitations (planned 3.1.x follow-ups)

- **`debug attach` doesn't auto-land at user line 1.** It reports
  `state=running`; the operator runs `debug pause` + `break set` +
  `continue` to reach a known stop. Fixing this needs a runner-side
  change so the shim sets a fireable breakpoint at user line 1 from
  the DAP side during attach. See `docs/insights_ctl.md` § "Why
  attach doesn't auto-land at user line 1".
- **Re-attach after detach** isn't supported by the runner: pyops
  calls `debugpy.listen()` + `wait_for_client()` once per debug job;
  after DAP disconnect there's no re-listen. Recovery is to start a
  fresh debug job.
- **`pause` during `time.sleep`** doesn't fire — debugpy can't
  preempt the C-level sleep call. Pause works between sleeps (lands
  on the next Python-level statement).

### Documentation

- New `docs/pyopsctl.md` — installation, config example, full command
  reference, worked operator + agent flows.
- New `docs/insights_ctl.md` — architecture deep-dive: session daemon
  spawn, control protocol, DAP bootstrap quirks, source-path
  resolution, race-freedom of step/continue.
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
