# Changelog

All notable changes to PyOps. Versions prior to 3.0.0 were tech preview
and are not documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from 3.0.0 onward.

## [3.3.7] — 2026-10-09

### Added
- **Additive database schema sync at start-up.** Besides creating missing
  tables, start-up now adds missing columns and indexes to existing tables
  (each logged as `schema sync: …`); renames, type changes and drops are
  not synced. A new NOT NULL column needs a `server_default`. Before
  changing an existing database, a backup
  `<stem>.bck.<VERSION>.<HASH>.<DATETIME>.db` is written next to it
  (e.g. `data/app.bck.3.3.7.<hash>.<datetime>.db`); an existing backup
  with the same content hash is reused, so restarting after a failure
  does not pile up copies. All changes run in one transaction: on failure the
  database is unchanged and start-up aborts with an error naming the
  database, the SQLite error, the failing statement and the backup.
  Recovery: restore the backup, fix the cause, restart pyops.
- **Jobs record the schedule that started them.** A job started by a
  schedule — by the scheduler or via "run now" — stores the schedule's id
  in the new `job.schedule_id` column; all other jobs leave it empty.
  Existing databases get the column at start-up; older jobs stay empty.
- **Schedules can keep their last runs despite drop_done.** A new
  schedule field `keep_n` (0-100, default 0) keeps the newest `keep_n`
  done jobs of a cron schedule with drop_done; older ones are removed as
  before. Set it in the Web UI Scheduler, via `PUT /scriptlet/schedule/item`
  or `control.schedule.create(..., keep_n=…)`; existing schedules read 0.
- **Schedules limit their failed jobs.** A new schedule field `max_fail_n`
  (1-100, default 3): a cron schedule keeps only its newest `max_fail_n`
  failed jobs and removes older ones right away; a service schedule stops
  restarting once `max_fail_n` of its jobs have failed — the service restart
  limit, previously a fixed 3, is now per schedule. Set it in the Web UI
  Scheduler, via `PUT /scriptlet/schedule/item` or
  `control.schedule.create(..., max_fail_n=…)`; existing schedules read 3.
- **VSIX: connectivity inherited from the shared PyOps client config.** The
  extension now reads the PyOps client config (`~/.config/pyops/config.toml`,
  override via `PYOPSCTL_CONFIG`) — the same connectivity profiles the
  pyopsctl CLI reads — so all clients share one source of truth for
  connectivity. A new `pyops.profile` setting names the profile a
  workspace adopts (empty → the file's `default_profile`); the profile
  supplies `serverUrl`, `username`, auth (`api_key`) and `debugPorts` as
  a base layer, and any explicitly-set `pyops.*` setting fully overrides
  the matching profile field. With `pyops.authMode` unset, the profile
  also decides the auth mode: `api_key` present → `basic`, absent →
  `x-user`. Leaving `pyops.username` unset uses the
  profile's user; setting it remaps a different user onto that
  connection, in which case the profile's `api_key` no longer applies and
  a Basic password is prompted for (when the user is not remapped, the
  `api_key` is used directly with no prompt). The set black/whitelist
  (`excludedSets`/`includedSets`) and other client-local settings stay in
  VSCode settings. When no config file or profile resolves, connectivity
  comes entirely from `pyops.*` settings as before. A config file that
  cannot be parsed is shown as an error naming the file, and no
  connection is made.
- **VSIX: X-User authentication mode.** A new `pyops.authMode` setting
  switches between `basic` (HTTP Basic Auth, for deployments where an
  auth proxy in front of PyOps validates credentials and injects the
  `X-User` header) and `x-user` (the extension sends `X-User: <username
  or "vsix">` directly, with no Authorization header and no password).
  The x-user pathway targets direct connections to a PyOps server that
  trusts the header (dev / no proxy), mirroring pyopsctl's api-key-absent
  behaviour. In x-user mode the password prompt is skipped and a 401 is
  reported as a plain connection failure instead of triggering a
  password re-prompt.
- **VSIX: set whitelist for the Manager tree.** A new `pyops.includedSets`
  setting complements the existing `pyops.excludedSets` blacklist: when
  `includedSets` is non-empty the Manager tree shows only those sets and
  the blacklist is ignored; when it is empty the `excludedSets` blacklist
  applies as before.

### Fixed
- **Web workers leaked a ZMQ context and two threads per controller
  request.** `controller_proxy` created a `ServiceClient` for every
  request and never closed it — teardown was deferred to `atexit`, i.e.
  to worker exit — so each call to a controller pinned a `zmq.Context`
  and its `ZMQbg` IO/reaper thread pair for the worker's lifetime. Under
  service polling this showed as steadily growing kernel stack and
  shrinking `MemAvailable`, reclaimed only by a worker restart.
  `controller_proxy` now closes the client after every request (success
  or error), and the wait for the service's reply is bounded by
  `web.timeout`.
- **A busy or restarting runner could hang a job at completion.**
  `send_completion` waited indefinitely for the runner's ACK; it now
  gives up after 30 s (logged, not raised) and always tears down its
  ZMQ context, so the job subprocess can exit.
- **The runner and the web workers inherited the gunicorn master's open
  SQLite connection across `fork()`.** SQLite forbids carrying a
  connection into a child process (latent locking/corruption risk); the
  master now disposes its engine after the start-up schema sync, so each
  process opens its own.
- **The packaged VSIX failed to activate.** `vsix/.vscodeignore` excluded
  `node_modules/`, so the `.vsix` lacked the runtime dependency `smol-toml`
  and the extension installed from it failed at `require("smol-toml")`.
  The `.vsix` now carries the compiled extension and its production
  dependencies only (no sources, tests or build scripts), and
  `vsix/build.sh` fails unless every runtime dependency is packaged.

### CI
- **VSIX "integration" suite renamed to the VSIX e2e axis.** It boots a
  real Electron extension host against a real server, so it is e2e — a
  peer of `tests/e2e/` (WebUI) and `tests/pyopsctl/e2e/`.
  `vsix/tests/integration/` → `vsix/tests/e2e/`, npm script
  `test:integration` → `test:e2e`, CI job `test:vsix_integration` →
  `test:e2e:vsix`.
- **Node-using jobs run on `node:24-trixie-slim`** (Node 24 LTS on Debian
  trixie) instead of apt-installing trixie's Node 20 — the latest
  `@vscode/vsce` needs Node >= 22. Applies to `test:e2e`, `test:e2e:vsix`,
  `test:vsix` and `build`.
- **The container image is built and pushed on every main pipeline.**
  `build:image` (multi-arch via `build.sh`) runs automatically on main
  instead of manually, and logs in to the project registry with
  `--password-stdin`.
- **Non-main builds push a multi-arch `:dev` image.** The new
  `build:image:dev` job runs `./build.sh dev` (only the `:dev` tag, never
  `:latest` or version tags) — automatically in MR pipelines targeting
  main, manually on other branches, never on main. It replaces
  `test:image_build`; `build_dev.sh` is local only.

### Documentation
- **Agent instructions moved from `CLAUDE.md` to `AGENTS.md`** (read by
  Claude Code and other coding agents); the unused `.roo/` configuration
  was removed.

## [3.3.6] — 2026-07-01

### Fixed
- **Container ran as pyops but with `HOME=/root` (regression from 3.3.5).**
  3.3.5 drops privileges with `setpriv`, which changes the uid/gid but
  does not reset the environment, so root's `HOME=/root` leaked into the
  pyops process. pyops (uid 1000) cannot write to `/root`, so pip's
  cache, the git config and the credential helper all failed with
  permission errors. `entrypoint.sh` now exports `HOME=/home/pyops`
  (plus `USER`/`LOGNAME`) before the `setpriv` hand-off, pointing at the
  pyops-owned home created by `useradd -m`.
- **Creating a local set failed with no git identity configured.** The
  Create Set task makes the repo's initial commit itself, which `git`
  aborts ("Please tell me who you are") when no committer identity is
  set — the container case, where pyops runs unprivileged with no global
  git config. The Create dialog now carries the same optional Git Name /
  Git Email fields as Clone, and the task persists them (or a `pyops` /
  `pyops@localhost` fallback) as the set's local identity before the
  initial commit, so create — and any later commit-drafts on that
  locally-created set — succeeds.

## [3.3.5] — 2026-06-17

### Fixed
- **Container start-up could no longer install packages (regression from
  3.3.3).** 3.3.3 set `USER pyops`, so the whole container ran
  unprivileged and the deployment's `init.sh` hook (sourced at boot)
  could no longer `apt`/`pip install`. The container now starts as root
  via a new `entrypoint.sh`: it sources `init.sh` (privileged package
  setup), `chown`s the data tree to `pyops`, then drops to the
  unprivileged `pyops` user (uid/gid 1000) with `setpriv` and execs
  `run.sh` → gunicorn. pyops and every scriptlet job still run non-root;
  privileged start-up works again. Because the entrypoint now chowns the
  data tree on boot, the manual `chown -R 1000:1000` migration noted in
  3.3.3 is no longer required. `entrypoint.sh` also runs under `set -e`
  with an `ERR` trap, so a failing privileged step in `init.sh` (e.g. an
  `apt install` that errors) aborts the boot with a clear message in the
  container log instead of being swallowed — otherwise gunicorn would
  start anyway and the missing package would only surface later as an
  import error inside a job.

## [3.3.4] — 2026-06-15

### Changed
- **Sidebar is collapsible.** A button collapses the desktop navigation
  rail; collapsed, the layout falls back to the mobile presentation (top
  bar + offcanvas overlay) and the state is remembered across reloads.
  The top-bar toggle now shows the brand icon instead of the hamburger
  glyph.
- **Collapsed view toolbars scroll horizontally.** Below the
  `navbar-expand-md` breakpoint, the Manager / Monitor / Scheduler and
  controller-view toolbars lay their icons out in a single
  horizontally-scrollable row instead of stacking them vertically.

## [3.3.3] — 2026-06-10

### Changed
- **Manager, Scheduler and Monitor group rows into per-set collapsible
  tables, collapsed by default.** Each view previously rendered a
  single flat table across all sets; it now renders one Bootstrap
  collapsible card per set (with a row-count badge), sorted
  alphabetically and collapsed on load. The incremental refresh is
  preserved — a refresh only ever creates a set section that doesn't
  exist yet, so a section a user has expanded stays expanded across the
  10s auto-refresh. The redundant per-row Set column is dropped (the
  set is the group header). New per-set test ids
  (`<view>-set-<set>`, `<view>-set-toggle-<set>`, `<view>-table-<set>`,
  container `<view>-sets`); existing row/button test ids are unchanged.
- **Scheduler create/edit modals reworked.** Create: Set and Name are
  now dropdowns (Set from `/inventory/sets`; Name cascades from the
  chosen set's runnable task scriptlets), and Param is an Ace editor
  with Python syntax highlighting. Edit: Set and Name are read-only (a
  schedule can't be retargeted), while Schedule, Drop-done, Max-retries
  and Param stay editable; Param is the same Ace editor. Both modals'
  Schedule field carries a cron-preset selector with an explicit
  "(empty) — run as service" option (the edit modal pre-selects the
  matching preset for the current cron). Because the Name dropdown only
  offers existing scriptlets, an unknown scriptlet can no longer be
  scheduled from the UI at all.
- **Scheduler table: the Drops column shows a checkmark** for
  drop-on-success instead of the literal `true`/`false`.
- **Task editor: stdout/stderr panes are collapsible.** Each output
  pane is now an independently collapsible section, collapsed by
  default and opened automatically when Run/Test is pressed. Panes
  default to ~10 lines tall and are user-resizable (drag the bottom
  edge); the chosen height persists per pane in localStorage.
- **Descriptive tooltips on every Web UI button** (Manager, Monitor,
  Scheduler, the editors, the Sets manager, the sidebar) via native
  `title` attributes, serving as inline help until the richer overlay
  help system lands in 4.x.

### Security
- **The Docker image runs as a non-root user (`pyops`, uid/gid 1000)**
  instead of root — gunicorn, the runner, and every scriptlet job it
  spawns now run unprivileged. App code stays root-owned and read-only;
  only the runtime tree (`data/`, `etc/`, `venv/`) is owned by `pyops`.

  **Migration (existing deployments):** the container can no longer
  write a root-owned data volume. Before deploying this image, chown the
  host-side data (and any bind-mounted `etc`/`venv`) to the new uid:
  `chown -R 1000:1000 <data-volume-path>`. Fresh named volumes inherit
  the correct ownership from the image automatically.

### Added
- **Per-set "discard drafts" action in the Sets manager.** Each set row
  gains a button (enabled when the set has unstaged changes) that
  reverts tracked edits and removes untracked draft files, restoring the
  set to its last commit — the bulk counterpart to the editor's per-file
  revert. Backed by a new `set_discard_drafts` task
  (`git reset --hard HEAD` + `git clean -fd` under the set repo lock);
  guarded by a confirmation modal that lists the exact changes
  (`git status`) about to be discarded. The set-remove action gained
  the same modal treatment, replacing its old native `confirm()` dialog.

## [3.3.2] — 2026-06-09

Patch release: job detail endpoint completeness, programmatic-job kind
inheritance, and clearer `param` documentation.

### Fixed
- **`GET /scriptlet/job/item` omitted `name`, `kind`, `param`, `start`,
  `end`.** The detail endpoint returned only
  `id/owner/set/state/detail/stdout/stderr` — fewer fields than
  `/scriptlet/job/list` — so `pyopsctl job view` rendered `set/name`
  with an empty name and showed no `kind`/`param`, even though the job
  had been enqueued with them (the `param` had demonstrably run). The
  endpoint now returns the full job row; `pyopsctl job view` surfaces
  `kind` and `param` alongside the existing fields.
- **`control.job.create(...)` always spawned `kind=task`, even from a
  working-tree parent.** A scriptlet started programmatically from a
  `draft`/`debug`/`test`/`testdebug` job ran against committed `HEAD`
  instead of the working-tree code the parent was iterating on —
  defeating the point of drafting/debugging an orchestrating
  task/service. `create` now inherits the child kind from the calling
  job: a working-tree parent yields a `draft` child, `task`/`service`
  yield `task`; an explicit `kind=` argument overrides. To make this
  possible, `setup_job_globals` now injects a `kind` global (every
  scriptlet can introspect its own execution kind) and threads it into
  `Control`. The legacy `debug: bool` argument of `create` is now a
  documented no-op (the enqueue model never had a `debug` field).

### Added
- **`kind` global** in every scriptlet's namespace — the job's
  execution kind (`task`/`service`/`draft`/`debug`/`test`/`testdebug`).

### Documentation
- **Clarified `param` semantics in the user guide.** `--param` (and
  `control.job.create(..., param=...)`) is a Python code snippet that
  the runner runs via `exec(param, globals)` before the scriptlet body
  — not a JSON/argument payload, and there is no injected `param`
  global. Added an explicit callout under `pyopsctl job` and corrected
  the misleading `set_clone --param '<json>'` example to the
  code-snippet form (`url = "..."`).

## [3.3.1] — 2026-06-09

Patch release fixing the coverage path of the bundled `pyops::pytest`
library.

### Fixed
- **`pyops::pytest` coverage path crashed with `NameError: name
  'config' is not defined`.** `_write_coveragerc` and
  `_subprocess_coverage_pth` reached for a `config` global that
  `job_runner.setup_job_globals` injects **only** for the pyops set
  (`setname == "pyops"`). Loaded into a *user* set via
  `#!{pyops::pytest}`, no such global exists, so the crash fired
  unconditionally as soon as `pytest-cov` was importable from the
  per-set venv — breaking every subsequent `kind=test` run on that set
  until `pytest-cov` was removed or the caller passed `--no-cov`.
  `_write_coveragerc` now resolves the set's canonical git checkout
  from the universally-injected absolute `gitspace` global;
  `_subprocess_coverage_pth` locates the per-set venv via `sys.prefix`
  (the interpreter the job already runs in) instead of reconstructing
  it from `config`. No behaviour change for the pyops set; the
  documented "coverage auto-attaches iff `import pytest_cov` succeeds"
  contract is unchanged.

## [3.3.0] — 2026-05-23

Three additive set capabilities plus a public-namespace rename:

- **`pyops` set as public library namespace** (was `internal`). Until
  3.3.0 the bundled set was implementation-internal and user
  scriptlets never imported from it. Starting with 3.3.0 the bundle is
  a published library namespace authors are expected to import from
  via `#!{pyops::name}` — starting with `pyops::pytest`. URL moves
  from `/controller/internal/sets` to `/controller/pyops/sets`. No
  compat alias — clean break, nothing user-authored referenced
  `internal::*`.
- **Multi-page markdown per set.** `data/git/{set}/docs/{name}.md`
  joins the long-standing `README.md` at the top level. Authors can
  ship multi-page documentation alongside scriptlets; the docs/
  subdir keeps free-named markdown out of the scriptlet-name
  namespace. `list_scriptlets` walks both locations; `pull`'s
  working-tree restore picks `docs/*.md` up alongside the existing
  `.py/.html/.js/README` set.
- **Set-scoped YAML/JSON data with shared/exclusive locks.** Tasks
  and libraries in the same set can read/write small structured
  files under `data/git/{set}/data/{name}.{yaml,yml,json}` via the
  new `data` global (peer of `control` and `listener`). Shared
  `fcntl.flock` on read, exclusive on write, per-file lock files;
  unrelated data files in the same set don't serialise; different
  sets run fully in parallel by construction. Cross-set access
  raises; name-uniqueness across formats prevents
  `config.yaml`/`config.json` coexisting.
- **Test kind.** A `*.task.py` file can act as both production task
  and its own test suite, gated by `if __name__ == "__main__":` /
  `if __name__ == "__test__":`. Pyops core stays pytest-agnostic —
  the kind dispatch sets `__name__` to `"__test__"`, materialises
  source under `.test/src/{set}/`, exec's. The new `pyops::pytest`
  library does the work: AST-scan-driven class/method-id resolution
  (no filesystem discovery), programmatic `pytest.main()` against
  the named test classes, auto-attached coverage when `pytest-cov`
  is in the import graph, `.coveragerc` with `[paths]` mapping back
  to canonical source paths. Authors who want a different test
  framework skip the import and put whatever they need in
  `__test__`.
- **Testdebug kind (hybrid).** Run the scriptlet's `__test__` block
  under a debugger. Server adds `kind="testdebug"` to the Literal
  union; `run_debug_job` services it via a `kind=` parameter and
  flips `glob["__name__"] = "__test__"` before `exec(param)`.
  Sources materialise under `.debug/src/` (same root as `kind=debug`,
  so VSIX/pyopsctl path-mappings keep working unchanged); a
  `.test → .debug` symlink is added in the workspace so tooling
  that walks the canonical "test sources" path resolves too. The
  one-active-interactive-per-owner gate in `app/lib/runner.py` is
  shared between `debug` and `testdebug`. Client surfaces:
  `pyopsctl debug start --kind testdebug` (default `debug`); VSIX
  `pyops.startTestDebugJob` command alongside `pyops.startDebugJob`,
  both delegating to a shared `startDebugFamilyJob` helper.
  `DebugService.enqueueDebugJob(..., kind='debug' | 'testdebug')`.
  Attach treats `kind in ("debug", "testdebug")` identically.
- **`pyops::pytest` namespace bridge.** `pytest_run` captures the
  calling task's `f_globals` (every `#!{set::name}` library's
  top-level symbol + pyops-injected globals like `setname` /
  `workspace` / `data` / `listener` / `control`) and pre-registers
  each materialised test file referenced by the resolved test-set
  node IDs as a `sys.modules` entry with that namespace seeded into
  the module dict before the file body runs. Pytest's import then
  finds the pre-registered module and uses it as-is, so test classes
  / fixtures / module-level decorators reference pyops-side symbols
  by bare name — the same semantics as the task body itself. Test
  files never write `from <set-lib> import <symbol>` for pyops
  content; classical imports remain valid only for stdlib / venv
  packages, which live in `sys.modules` independently. Only files in
  the resolved IDs are pre-registered, so production libraries that
  happen to share their stem with a stdlib module (`os.py`, ...)
  cannot accidentally shadow the stdlib for tests doing `import os`.
- **Importlib resolution for directive-merged classes across all
  kinds.** `execute_job_code` calls a new `_register_user_module(glob)`
  helper after libraries are exec'd and before the main code runs.
  Two paths: when `glob['__name__']` is unique (today: `'__test__'`
  from kind=test / kind=testdebug), wrap `glob` into a fresh
  `types.ModuleType` and register it in `sys.modules`. When the name
  is already there (kind=task / service / debug / draft all keep
  `'__main__'`, owned by `job_runner.py`), stamp the user's
  non-dunder, non-conflicting globals onto the existing
  `__main__.__dict__` — leaves runner-owned bindings (`logger`,
  `job_lib`, stdlib imports) intact. Either way, directive-merged
  classes (`@vortex`, `@composite`, ...) — which inherit
  `__module__` from `glob['__name__']` at exec time — become
  resolvable via `importlib.import_module(__module__) + getattr()`,
  so `Vortex.from_dict` and any other library that round-trips
  class identity by `(__module__, __name__)` works inside the same
  job regardless of kind. The motivating regression was
  `cami::causal_service` crashing on draft start with a persisted
  realm because `CamiServiceVortex.__module__` was `__main__` but
  the runner's own `__main__` (job_runner.py) didn't carry the
  symbol.

### Added
- `app/lib/data.py` — `SetData` API for set-scoped YAML/JSON I/O
  with shared/exclusive locks and atomic writes.
- `app/pyops/pytest.library.py` — public `#!{pyops::pytest}`
  helper for programmatic pytest invocation in `kind=test` jobs.
- `app/sites/scriptlet/editor_data.html` — Ace-based editor for
  `data/*.{yaml,yml,json}` files with mode switching by extension.
  Routed at `/scriptlet/editor_data`.
- `data` type to the Manager's create dialog, `Markdown` already
  listed but now creates either `README.md` or `docs/{name}.md`
  depending on the chosen name (the "README only" restriction is
  gone).
- VSIX: `data` type in the Manager tree, with the on-disk extension
  (`yaml` | `yml` | `json`) propagating through `ScriptletInfo.ext`
  → `ScriptletTreeItem.scriptletExt` → `inferExt()` (local mirror
  filename matches remote format) → `pyops-remote://` diff URI's
  `?ext=...` query param → `RemoteContentProvider`'s language-mode
  setter (correct syntax highlighting). VSIX `CreateScriptletCommand`
  offers `Data` and accepts non-README markdown names.
- pyopsctl: `--type data` accepted by `scriptlet create`;
  `--kind {test,debug,draft,testdebug}` accepted by `job run`;
  `--overrides-dir DIR` accepted by `job run` when paired with
  `--kind {test,debug,testdebug}` (ships the working-copy
  scriptlets via `overrides`, mirroring the `debug start` path).
  `debug start --kind {debug,testdebug}` switches the kind tag
  in the body; everything else (port lease, bootstrap composition,
  attach, source-path resolution) is unchanged.
- E2E tests for the three new features plus the testdebug hybrid
  (`tests/e2e/test_multi_markdown.py`, `test_set_data.py`,
  `test_test_kind.py` — testdebug coverage added);
  `tests/pyopsctl/e2e/test_debug_actual_debugging.py` exercises a
  real-pdb attach + continue + verify-completion against a
  `kind=testdebug` job.

### Changed
- Directory rename `app/internal/` → `app/pyops/`. URL
  `/controller/internal/sets` → `/controller/pyops/sets`. JSlib
  `internal_job.js` → `sets_job.js` with global `runSetsJob` and test
  IDs `internal-job-*` → `sets-job-*`. The role-describing identifiers
  in code use the `bundled_*` prefix (`BUNDLED_SET_NAME`,
  `bundled_set_path`, `is_bundled_set`, `_reject_if_bundled`) so the
  question "is this the application-bundled set?" doesn't read as "is
  this a pyops set?" (every set is — the project's name is pyops).
- `pyops::pytest` pulls in pytest itself via a `#!pytest` directive at
  the top of its library file — consumers of `#!{pyops::pytest}` no
  longer need their own `#!pytest` line.
- `pyops::pytest.pytest_run` captures the calling task's `f_globals`
  via `sys._getframe(1)` and pre-registers each materialised test file
  it is about to invoke pytest against as a `sys.modules` entry whose
  `__dict__` is seeded with that namespace, so test bodies reference
  pyops-side symbols (directive-merged libraries + injected globals
  like `setname` / `workspace`) by bare name. Test files no longer
  carry `from <set-lib> import <symbol>` for pyops content. Convention:
  call `pytest_run` directly from `if __name__ == "__test__":` — a
  helper wrapper would shift the captured frame off the task and
  break the bridge. Production-library files outside the resolved
  node IDs are not pre-registered, so they cannot shadow stdlib
  modules like `os`.
- `app/job_runner.execute_job_code` calls a new
  `_register_user_module(glob)` after the library exec phase and
  before the main code runs. Two paths now: when `glob['__name__']`
  is unique to the job (today: `'__test__'` from kind=test /
  testdebug), the helper wraps `glob` in a fresh
  `types.ModuleType(name)` and registers it in `sys.modules`. When
  the name is already there (kind=task / service / debug / draft
  keep `'__main__'`), it stamps the user's non-dunder, non-conflicting
  globals onto the existing `__main__.__dict__` — leaves runner-owned
  bindings intact. Either way, directive-merged classes become
  reachable via `importlib.import_module(__module__) + getattr()`,
  so `Vortex.from_dict` and equivalent round-trip-by-identity
  patterns work for every kind. Without this,
  `cami::causal_service` (and any other service persisting a realm
  with @vortex classes from its libraries) failed on draft start
  with `AttributeError: module '__main__' has no attribute …`.
- Manager UI and task editor gain a **Run as Test** button (material
  `science` icon, info-blue) next to the existing Run-as-Draft. Both
  PUT `kind=test`. Operators can now invoke `kind=test` jobs from the
  WebUI without `pyopsctl` / API.
- The directive-syntax line in `docs/insights_app.md` is corrected:
  `#!{set::name}` with mandatory `::` (an empty `set` means current
  set, e.g. `#!{::lib}`), replacing the earlier
  `#!{[set::]name}` text that read the `::` as optional.
- Inventory routes (`code_get`, `draft_post`, `item_del`,
  `draft_del`) now use `git.list_scriptlet_files` for type
  resolution so they find `docs/*.md` and `data/*.{yaml,yml,json}`
  alongside top-level scriptlets.
- The Manager UI's create modal lists `Markdown` and `Data (YAML)`
  in addition to Task/Library/Controller/View.

### Fixed
- Inventory `code_get` previously globbed only the top level of a
  set's working tree; multi-markdown / data files were unreadable
  via the WebUI editor as a result.
- `app/lib/job.loadLib` excluded `kind=test` and `kind=testdebug`
  from the unstaged-read set; `loadJob` (in `app/runner.py`)
  included them. The mismatch made a `kind=test` job that depends
  on a drafted library run against the library's committed snapshot,
  silently testing the wrong code. Aligned `loadLib` to the same
  set `("debug", "draft", "test", "testdebug")`.
- `app/pyops/set_clone.task.py` baked HTTPS credentials into the
  cloned set's origin URL (`https://user:token@host/path.git`) and
  then wrote the same credentials to `.git/credentials`. Token
  rotation by editing `.git/credentials` had no effect because git
  resolves the password from the URL first; the helper's lookup
  never fired. After clone, set_clone now rewrites origin to the
  credential-free URL via `git remote set-url`, so the credential
  helper becomes the single source of truth. Plus a migration hook
  in `app/pyops/set.library.normalize_origin_url`, called by
  `set_commit_drafts` (before push) and `set_pull` (before fetch),
  that strips embedded HTTPS credentials from any legacy set's
  origin URL on the next push / pull. Idempotent for already-clean
  origins and a no-op for SSH / file:// schemes.
- `app/lib/git.list_scriptlets` now stamps `has_unstaged: bool` on
  every entry by running one `git status --porcelain` per set and
  matching scriptlet file paths against the dirty set. The Manager
  UI (`app/sites/scriptlet/manager.html`) renders the flag as a
  per-row icon (`check` / `change_history`, success / warning colour)
  so the operator distinguishes drafts from committed revisions at a
  glance — pre-3.3.0 the row markup carried no draft signal at all
  and the only way to tell was opening the editor or hitting
  `/scriptlet/inventory/status` per row. The icon carries
  `data-testid="scriptlet-status-{set}-{name}"` and
  `data-status="clean" | "dirty"` for tooling.
- `app/job_runner` now redirects job stdout / stderr at the file-
  descriptor level via `os.dup2` (new `_redirect_job_io` context
  manager) instead of `contextlib.redirect_stdout` /
  `redirect_stderr`. The previous Python-level redirect only swapped
  `sys.stdout` / `sys.stderr` and missed everything that wrote
  directly to fd1 / fd2: child processes inheriting the parent's
  fds, C-extension and native writers (HF tqdm download bars,
  transformers, torch native code). Those bytes used to land in the
  gunicorn worker's real stdout/stderr; with 3.3.0 they reach the
  per-job `stdout.log` / `stderr.log` like in pyops 2.1. Applied at
  all five job-kind sites (task, service, debug, test/testdebug,
  draft); `sys.stdout` / `sys.stderr` and the saved fds are restored
  in `finally` even on exception inside the block. Streaming
  behaviour preserved: log files stay opened with `buffering=1`
  (line-buffered) by `setup_execution_environment`, so `print(...)`
  flushes per newline and fd-level writes hit the file immediately
  — operators can `tail -f stdout.log` on long-running jobs.
  Regression coverage in `TestRedirectJobIo` includes three
  streaming tests (print / fd-write / subprocess all visible to a
  separate reader before the `with` block exits).
- `app/pyops/sets_job.js` job-tail modal (used by every action on
  the Sets controller — clone, create, pull, switch, remove,
  update-deps, commit-drafts) now scales to ~95vw / 95vh instead
  of Bootstrap's fixed `modal-lg`. Modal body is a flex column so
  the stdout / stderr panes grow proportionally with the viewport
  (3 : 1 split, stdout dominant); both panes scroll independently
  on overflow. Verbose jobs like `set_update_deps` against a
  large set are now readable without scrolling through a narrow
  column.
- `/scriptlet/job/list` now includes `set` and `name` columns in
  every row. Pre-3.3.0 the SELECT omitted both, so
  `pyopsctl job list --set <name>` (and the table's set / name
  columns) silently rendered empty even when matching jobs
  existed — the CLI filters `row.get("set") == args.set` which
  never matched because the field wasn't there. Regression
  coverage in `tests/integration/routers/test_job.py` and
  `tests/pyopsctl/unit/test_commands_job.py`.
- `app/pyops/set_update_deps.task` now matches the per-job directive
  processor's transitive semantics and refreshes git clones in two
  ways. Pre-3.3.0 it had two gaps that compounded into "Update
  Dependencies looks clean but transitive deps drift silently":
  1. `_install_git` went straight to `git checkout <branch>` /
     `git pull` against an existing local clone, so a directive flip
     from `main` to a freshly-created remote branch hit
     `pathspec 'newbranch' did not match any file(s)` — the local
     clone had no ref for it. Operators had to `git fetch` by hand
     in `data/pkg/<set>/<repo>`, bypassing the pyops-managed
     lifecycle. Fix: run `git fetch --prune --tags origin` ahead of
     the checkout when the clone already exists.
  2. `_scan(name)` only walked `data/git/{name}/*.py`, treating
     `#!{set::name}` library-include directives as "not a git
     directive" (line 73's `or "::" in rest: continue`). The per-job
     processor (`process_imports`) follows includes recursively, so
     a cami task that says `#!{system::causal}` correctly clones
     `causal.py` into `data/pkg/cami/causal.py/` from the directive
     that lives in `system/causal.library.py`. Update-Dependencies
     for cami silently skipped the include and never reported the
     transitive deps — operator ran "update cami", got "0
     conflict(s)" back, but cami's pkg/causal.py was untouched and
     stayed at whatever ref it was cloned with. Fix: `_scan` now
     follows `#!{set::name}` includes transitively (cross-set and
     same-set), cycle-safe via a per-(set,name) seen set, with
     directives attributed to the file they textually appear in.
  Per-job installs (`app/lib/job.process_install_lines`) stay
  strictly offline as before; only the explicit Update-Dependencies
  action touches the network. Regression coverage:
  `tests/unit/pyops/test_set_update_deps.py::TestInstallGitFetchBeforeCheckout`
  (fetch ordering / flags / no-fetch on initial clone) and
  `TestScanTransitiveLibraryIncludes` (cross-set + same-set
  resolution, cycle protection, missing-lib skip, multi-hop chain)
  plus the real-git integration suite at
  `tests/integration/test_set_update_deps_fetch.py` (branch-flip +
  tag-pickup against a temp bare remote).
- `app/job_runner.execute_job_code` now runs `exec(libs)` BEFORE
  `exec(param)` for `kind` in `("service", "draft", "debug", "test",
  "testdebug")` — task-kind keeps the inverted order because of its
  retry-loop contract. Library imports pulled in by `#!{set::lib}`
  directives can be heavy (cami's `llm_workers` pulls in `torch` +
  `transformers` through `causal.vortex.asyncio.llm.hf`, taking
  5-10 s on first import); the previous order ran those imports
  AFTER `_signal_running()` had already fired, so the row read
  `state=running` while the service's zmq listener was bound but
  not yet polled. Controller-proxy `ServiceClient.recv()` blocked
  on the first HTTP request through the warmup window, gunicorn
  worker_timeout (300 s by default) eventually killed the worker,
  and the operator saw `Empty reply from server`. After the fix the
  library import window lands during `state=loading`; `state=running`
  fires after `exec(param)` returns (preserving the debug-kind
  semantics verbatim — `exec(param)` is still the last step before
  the flip, so for kind=debug `running` still means "debugger
  attached + first continue issued"). Docs in `docs/insights_app.md`
  and `docs/insights_vsix.md` rephrased to be kind-aware:
  task fires after `exec(param)`; non-task fires after
  `exec(libs)` + `exec(param)` have both returned. Regression
  coverage updated at
  `tests/integration/runner/test_execution.py::test_run_debug_no_debugger_injection`.

### CI / Docs
- `pyyaml` added to the runner's image (`Dockerfile`) and the four
  pip-install lines in `.gitlab-ci.yml`.
- Insights kept in lockstep: `docs/insights_app.md`,
  `docs/insights_vsix.md`, `docs/insights_ctl.md`, plus
  `docs/overview.md`, `docs/pyops_guide.md`, `docs/pyops_rules.md`,
  `README.md`, and the project-root `CLAUDE.md` are all updated to
  reflect the new set/file layout, the new `pyops` namespace, the
  `data` and `test` features, and the corrected directive syntax.
- `docs/insights_app.md` "Test Jobs" and `docs/pyops_guide.md`
  "Writing Tests" sections rewritten to document the namespace
  bridge: test bodies use bare names for pyops content, classical
  Python imports apply only to stdlib / venv packages. Outdated
  guidance about importing production libs via
  `from production_lib import …` removed.
- `docs/insights_app.md` "Job Globals" section gained a paragraph
  on the `__name__` overlay per kind and the new
  `_register_user_module` registration that makes
  `importlib.import_module(glob['__name__'])` resolve to the job's
  exec dict for the kinds where that name is unique
  (`'__test__'`).

## [3.2.0] — 2026-05-19

Three debug-flow bugs and a vendored JS lib refresh:

- **Debug jobs no longer die with `Launch failed: [Errno 7] Argument
  list too long`.** `runner.py` used to pass the serialised job
  context (param + code + libs + VSIX overrides) as a single argv
  element to `Popen([venv_python, 'app/job_setup.py', json_kwargs])`.
  Sets with large overrides (one i-doit scriptlet alone is 84 KB) blew
  past Linux `ARG_MAX` and `execve(2)` returned `E2BIG` before the
  child started — no workspace, no logs, only a short `detail` from
  the local `except`. Kwargs now flow via stdin into `job_setup.py`
  and via `<workspace>/.kwargs.json` into `job_runner.py`.
- **VSIX no longer leaks debug ports on early failure.** When a polled
  job hit `state=failed` before the debugpy listener came up, the wait
  loop threw but never called `releasePort()` — `onDidTerminateDebugSession`
  is the only release path, and it never fires for a session that was
  never started. One failure permanently consumed a port; after the
  pool drained, every retry reported "fully leased". The catch block
  now releases on every early-exit path.
- **VSIX surfaces the server-side `detail` in the failure toast.** The
  wait loop was reading only `job.state`, so operators saw a bare
  `state=failed` with no cause. It now appends `job.detail` (the
  traceback `send_completion` already writes), so the same workflow
  would show `Launch failed: [Errno 7] Argument list too long` inline.

### Changed
- Vendored JS libs refreshed to current upstream stable: bootstrap
  5.2.3 → **5.3.8**, jQuery 3.6.4 → **3.7.1**, js-cookie 3.0.5 →
  **3.0.7**, ace-builds 1.5.0 → **1.44.0** (full `src-min-noconflict`
  tree, 36 new modes/themes, none removed). Material Icons font
  unchanged.

### Fixed
- Dead `completer:` option dropped from the Ace `ace.edit()` calls in
  `editor_task.html` / `editor_library.html`. Ace 1.44 warns on unknown
  constructor options; the custom Python completer is already
  registered via `langTools.addCompleter(pylintCompleter)`, so the
  constructor key was a no-op in 1.5.0 and is silently ignored in 1.44
   — the warning is just the new version surfacing always-dead code.
- `tests/pyopsctl/unit/test_config.py::test_server_flag_without_profile_match_works_with_explicit_creds`
  no longer flakes on dev machines that have a real
  `~/.config/pyops/config.toml`. The test now points `PYOPSCTL_CONFIG`
  at a non-existent path instead of just deleting the env var, so the
  loader's file branch is unconditionally skipped.

### CI
- New `verify:version_bumped` job (MR-only, last in the build stage)
  fails the pipeline when `VERSION` still matches an already-released
  tag, when `vsix/package.json` / `vsix/package-lock.json` disagree
  with `VERSION`, or when `CHANGELOG.md` has no `## [<VERSION>]`
  heading. Catches "forgot to bump VERSION" before the merge instead
  of after.

## [3.1.0] — 2026-05-18

Closes 3.0.0's open ends with three structural moves and one
shutdown-contract fix:

- **Debugger leaves the runner.** `kind=debug` jobs no longer have
  pyops inject debugpy; the client (VSIX, pyopsctl, or anything else)
  writes the debugger bootstrap into `param`.
- **Commit/push leaves the inventory router.** The whole
  `app.lib.git` commit surface is gone; the only sanctioned commit
  pathway is the new sets-view "commit drafts" action.
- **The operator surface VSIX can't reach is now its own terminal
  CLI: `pyopsctl`.** Detached debug, arbitrary multi-line `param`,
  interactive pdb sessions, scripted job/scriptlet ops. Distro
  python3 + python3-requests, no venv, no pip.
- **`ServiceListener.async_poll()` honours SIGTERM** (mirrors
  `listen()`'s "TERM" return). Causal-style services that polled
  past SIGTERM and got SIGKILL'd after the 15s grace window now exit
  on the first poll cycle.

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
- New `build:pyopsctl` stage parallel to `build:` (VSIX). Produces
  `ctl/dist/pyopsctl_<version>_all.deb` via `ctl/build.sh` (plain
  `dpkg-deb`, no fpm/Ruby, no debhelper/dh-python). Targets Ubuntu
  24.04 (noble) and Debian 13 (trixie) as the floor; Depends pins
  python3 (>= 3.12), python3-requests; Recommends python3-argcomplete.
  Ships the package source at `/usr/lib/python3/dist-packages/pyopsctl`,
  launcher at `/usr/bin/pyopsctl`, and pre-generated zsh + bash
  completion files at the apt-managed system paths
  (`/usr/share/zsh/vendor-completions/_pyopsctl`,
  `/usr/share/bash-completion/completions/pyopsctl`) — so an
  apt-install user gets tab completion with zero rc edits.
- `release` stage now uploads the deb alongside the VSIX as a Generic
  Package (`pyops-pyopsctl/<version>/`), tracked as an additional
  `--assets-link` on the GitLab Release.
- New `test:pyopsctl:install` stage installs the built deb in a clean
  apt environment and smoke-tests it: launcher resolves at `/usr/bin`
  with no `PYTHONPATH`, `pyopsctl version` matches the project VERSION
  file (proves the baked `_version.py` is the one imported), the
  argcomplete protocol returns top-level groups, bash + zsh completion
  files land at the apt-managed system paths, zsh `compinit` registers
  the completion from `$fpath`, and `apt-get remove` cleanly drops
  every shipped file.

### Operations
- **Container-wide pip configuration via compose bind.** Bind a host
  `pip.conf` to `/etc/pip.conf` in the pyops container to route every
  pip invocation — bootstrap pip in `ensure_set_environment`, every
  per-set venv's pip, every user `#!pip <pkg>` directive — through one
  config file. pip reads `/etc/pip.conf` as its site-wide config
  automatically, so no pyops code is involved; standard Linux
  subprocess + system-config mechanic. Documented in `README.md` §
  Docker Compose. No new pyops feature, just a deployment pattern
  that's now spelled out.

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

### Added (pyopsctl — operator CLI)

Designed to be driven from a terminal by both a human operator and
agents (LLM-driven or otherwise) and shell scripts — no venv, no
pip-installed Python deps, distribution packages only (`python3` +
`python3-requests`). Installed via `./ctl/install.sh` (symlinks the
launcher into `$XDG_BIN_HOME`, defaults to `~/.local/bin/`) or via
the released `.deb` (`sudo apt install ./pyopsctl_*.deb` — drops at
apt-managed system paths, completion wired without rc edits).

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

- **No re-attach after detach.** Once the operator detaches a pdb
  session the underlying TCP connection is gone; the running job
  doesn't re-listen. Recovery is to start a fresh debug job.
- **Manager-started controller previews** still depend on a single
  `draft`-kind controller service running; concurrent operators all
  hit the same one (no per-user preview isolation yet).

### Documentation

- `docs/pyops_guide.md` § pyopsctl — installation (checkout +
  `.deb`), config example, full command reference, worked operator +
  agent flows. (Supersedes the planned standalone `docs/pyopsctl.md`
  from the initial pyopsctl drop — content merged into the unified
  guide.)
- `docs/insights_ctl.md` — pyopsctl architecture deep-dive: session
  daemon, control protocol, port-pool leasing, source-path
  resolution.
- `docs/insights_app.md` § `ServiceListener` — async_poll's TERM
  sentinel contract.
- `docs/pyops_rules.md` § scriptlet service shutdown — operator-facing
  TERM-handling rule for causal-style services.
- `ctl/README.md` — prerequisites + install paths (deb + checkout).
- `README.md` § Docker Compose — pip.conf bind-mount pattern for
  container-wide pip configuration.

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
