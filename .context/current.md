# Finalized Refactoring Plan for PyOps VSIX Extension

All approvals incorporated: Encapsulate `activeJobs` polling and globals (channels) in `state/jobManager.ts` class for centralized management (Map, polling start/stop/append, channel creation/dispose). Tests: Greenfield with mocks (no real deps); new insight `.context/insights/architectural/vsix_testing.md` post-refactor. Plan complete; content below ready for `.context/plan.md`. Toggle to Act mode to write file and execute step-by-step.

## Key Assumptions (Final)
- Globals encapsulated in `state/jobManager.ts`: Class holds activeJobs Map, jobOutputChannel, jobErrOutputChannel, jobViewOutputChannel; methods for poll start (debug/view), stop, append output, cleanup. Injected into commands/views via activate orchestration.
- Tests: Isolation via mocks (sinon for VSCode stubs, fetch mocks); no real server/FS. Coverage: nyc post-full (>80%).
- Insights: Update `.context/insights/architectural/vsix_extension_substructure.md` (add modules/state); create `.context/insights/architectural/vsix_testing.md` (Mocha setup, mocks, suite structure).
- No func changes: Exact logic preservation; tests validate equivalence.

## Overall Refactoring Strategy (Final)
- **Modular Structure**: `utils/`, `config/`, `providers/`, `commands/` (sub: manager/, monitor/, bulk/), `status/`, `views/`, `state/`. `index.ts` barrel exports. `extension.ts`: Orchestration only.
- **Extraction**: Copy-paste; param injection for deps (context, config, jobManager).
- **Test Gates**: After every step: `npm run compile && npm run lint && npm test` (100% pass). Manual F5 load for UI after batches.
- **Deps Update**: Add to `package.json` devDependencies: `"sinon": "^15.0.0"`, `"nyc": "^15.1.0"`. Scripts: `"test": "mocha out/tests/**/*.test.js --reporter spec"`, `"coverage": "nyc --reporter=lcov --reporter=text npm test"`.

## Step-by-Step Refactoring Plan (Final)
1. **Prepare Structure**:
   - Create subdirs + `index.ts` (exports).
   - Move `providers/jobsTreeProvider.ts`.
   - Extract `utils/inferExt.ts`, `utils/getIconForType.ts`, `utils/headers.ts`.
   - **Test Gate**: Units (utils.test.ts); pass. Compile/lint.

2. **Extract Config**:
   - `config/configLoader.ts` (YAML load/merge/update/watcher), `config/auth.ts` (prompt/storage).
   - **Test Gate**: Units (config.test.ts: merge/auth/watcher reload); integration (loader flow, mock onDidChange). Pass; automated YAML trigger test.

3. **Extract Providers** (split into sub-steps):

   3.1 **Extract RemoteContentProvider**:
      - Create `providers/remoteContentProvider.ts`.
      - Move the `RemoteContentProvider` class from `extension.ts`.
      - Add necessary imports (vscode, EventEmitter).
      - Export the class.
      - **Test Gate**: Unit test for provideTextDocumentContent. Pass.

   3.2 **Extract PyOpsWebviewProvider**:
      - Create `providers/webviewProvider.ts`.
      - Move the `PyOpsWebviewViewProvider` class from `extension.ts` (rename to `PyOpsWebviewProvider`).
      - Add necessary imports (vscode, fetch, Buffer).
      - Export the class.
      - **Test Gate**: Unit test for HTML rewriting. Pass.

   3.3 **Extract ManagerTreeDataProvider**:
      - Create `providers/managerTreeProvider.ts`.
      - Move the `ManagerTreeDataProvider` class from `extension.ts`.
      - Add necessary imports (vscode, fs, path, fetch, Buffer, inferExt, getIconForType, getHeaders).
      - Export the class and `ScriptletTreeItem` interface.
      - **Test Gate**: Unit test for tree building and fetch/download. Pass.

   3.4 **Update jobsTreeProvider.ts**:
      - Replace inline header logic with `getHeaders` utility.
      - Update imports to include `getHeaders` from `utils/headers.ts`.
      - Ensure `fetchJobs` method uses the utility.
      - **Test Gate**: Update existing tests. Pass.

   3.5 **Update extension.ts**:
      - Remove the inline provider classes.
      - Add imports for the extracted providers from `providers/`.
      - Update instantiation and registration.
      - **Test Gate**: Integration test for registrations. Pass; manual trees.

   3.6 **Final Test Gate for Providers**:
      - Run full `npm run compile && npm run lint && npm run test`.
      - Manual verification: F5 load and test tree views.

4. **Extract Status** (sliced into testable substeps):

   4.1 **Create status/statusBar.ts**:
      - Extract the status bar item creation (vscode.window.createStatusBarItem), updateConnection function, listener setup (e.g., workspace.onDidChangeConfiguration), and refreshStatus command logic from extension.ts into a new StatusBarManager class or exported functions in status/statusBar.ts.
      - Include necessary imports (vscode) and handle connection checks (mockable fetch or config-based).
      - Export the manager instance or functions for use in extension.ts.
      - **Test Gate**: Compile the new file (npm run compile) and lint (npm run lint); ensure no errors in isolation.

   4.2 **Update extension.ts**:
      - Remove the inline status bar code from extension.ts.
      - Add import for StatusBarManager from '../status/statusBar'.
      - Instantiate the manager in activate() and call its init/update methods; register the refreshStatus command via the manager.
      - **Test Gate**: Run full compile, lint, and existing tests (npm run test) to check for regressions. Add unit tests in a new or existing test file (e.g., status.test.ts) for updateConnection (using sinon mocks for vscode.statusBar, fetch for connection states). All pass (100% for new units).

   4.3 **Add Integration Tests**:
      - Extend or create integration tests (e.g., in providers.test.ts or new status.integration.test.ts) to cover the full flow: activate -> status update on config change -> refresh command execution.
      - Mock vscode events (onDidChangeConfiguration) and fetch responses.
      - **Test Gate**: Run integration tests; ensure they pass and cover status listener triggers.

   4.4 **Manual Verification**:
      - Reload the extension via F5 in VS Code.
      - Verify status bar item appears, updates correctly on connection changes (e.g., toggle config), and refreshStatus command works without errors.
      - **Test Gate**: Manual checks confirm expected UI behavior; no console errors.

5. **Extract State (New for Globals)**:
   - `state/jobManager.ts`: Class with Map, channels; startPoll (setInterval, append slice), stopPoll (clear), getActive, dispose.
   - **Test Gate**: Units (poll setup/append/cleanup, mock setInterval). Pass.

6. **Extract Commands** (Batches):
   - Batch 1: manager/createScriptlet.ts, sync/* (reset/write/save), openLocalFile.ts (use config/utils).
   - Batch 2: manager/debug/* (start/stop, use jobManager), diffLocalRemote.ts, deleteScriptlet.ts.
   - Batch 3: monitor/refreshMonitor.ts, copyJobParam.ts, cancelJob.ts, downloadJob.ts, deleteJob.ts, viewJob.ts (use jobManager, fetchJobs util).
   - Batch 4: bulk/* (performBulkAction, fetchJobs shared).
   - **Test Gates**: Per batch – units (mocks for QuickPick/fetch/clipboard/dialogs); integration flows (e.g., create->debug poll). Pass; manual commands.

7. **Extract Views**:
   - `views/treeViews.ts` (register providers, timers; use jobManager for monitor).
   - **Test Gate**: Integration (mock providers/timers). Pass; manual refresh.

8. **Slim extension.ts/deactivate**:
   - Orchestrate: config.activate, status.activate, providers register, views.activate, commands register, jobManager = new JobManager(context).
   - deactivate: Modules deactivate, jobManager.dispose.
   - **Test Gate**: Full activate/integration; end-to-end. Pass; manual full.

9. **Finalization**:
   - index.ts imports; deps/scripts update.
   - nyc coverage.
   - **Test Gate**: Full suite + coverage; manual all (edges: no auth, errors).
   - Insights: Update substructure.md; create vsix_testing.md (setup, mocks, structure).

## Testing Plan (Final)
- **Setup**: Direct mocha run on compiled tests; mocks in test files (sinon for vscode, fetch). CI/CD compatible, no VSCode instance.
- **Units**: Per module (e.g., jobManager.startPoll asserts interval/append; sinon.spy(setInterval)).
- **Integration**: Flows (activate->command->provider); mock events (onDidChange).
- **Regression**: Baseline pre; gates ensure no breaks. Post: nyc report, manual (F5: views/commands/status/polling).
- **Watcher/Webview**: Manual trigger mocks; fetch mock static responses.
- **Polling**: Units mock timers/append; integration verifies start/stop without real intervals.
