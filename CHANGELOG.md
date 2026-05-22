# Changelog

All notable changes to PyOps. Versions prior to 3.0.0 were tech preview
and are not documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from 3.0.0 onward.

## [3.3.0] — 2026-05-21

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
