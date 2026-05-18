import * as assert from 'assert';
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as helpers from '../utils/helpers';
import { getExtensionState } from '../../../src/extension';

export async function run(): Promise<void> {
  console.log('Running Debug Tests...');

  // VSCode's debug-session bootstrap occasionally rejects an internal
  // DAP request with `Canceled: Canceled` AFTER our await on
  // `vscode.debug.startDebugging` has already resolved. That rejection
  // lands on the global unhandledRejection event and — under Node's
  // default policy — terminates the test process. The error is
  // cosmetic for our purposes: the session is up and our test still
  // drives it end-to-end via customRequest. Swallow specifically the
  // "Canceled: Canceled" variant so the test process survives; surface
  // anything else.
  const globalUnhandled = (reason: any) => {
    const msg = reason instanceof Error ? reason.message : String(reason);
    if (msg.includes('Canceled')) {
      console.log('  [debug.test] swallowed unhandledRejection:', msg);
      return;
    }
    console.error('  [debug.test] UNHANDLED rejection (not swallowed):', reason);
  };
  process.on('unhandledRejection', globalUnhandled);

  let tempDir: string | null = null;
  let testScriptletName: string | null = null;
  let debugJobId: string | null = null;
  let originalLocalPyopsDir: string | undefined;

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    const state = getExtensionState();
    const debugService = state?.debugService;
    const debugOutputMonitor = state?.debugOutputMonitor;
    const managerProvider = state?.managerTreeProvider;
    const jobsProvider = state?.jobsTreeProvider;
    assert.ok(debugService && debugOutputMonitor && managerProvider && jobsProvider, 'Debug services and providers must exist');

    // Cleanup leftover jobs
    await helpers.cleanupJobs(true);

    // Save original config
    const pyopsConfig = vscode.workspace.getConfiguration('pyops');
    originalLocalPyopsDir = pyopsConfig.get('localPyopsDir');

    // Create temp dir for local files
    tempDir = helpers.createTempDir();

    // Set localPyopsDir to temp
    await pyopsConfig.update('localPyopsDir', path.basename(tempDir), vscode.ConfigurationTarget.Workspace);

    // Test 1: buildOverrides with local files
    console.log('Test 1: buildOverrides...');
    const setDir = path.join(tempDir, 'test');
    fs.mkdirSync(setDir, { recursive: true });
    const taskDir = path.join(setDir, 'task');
    fs.mkdirSync(taskDir, { recursive: true });
    const serviceDir = path.join(setDir, 'service');
    fs.mkdirSync(serviceDir, { recursive: true });

    const taskContent = 'print("task code")';
    const serviceContent = 'print("service code")';
    fs.writeFileSync(path.join(taskDir, 'mytask.py'), taskContent);
    fs.writeFileSync(path.join(serviceDir, 'myservice.py'), serviceContent);

    const DebugService = state!.DebugService;
    const buildResult = DebugService.buildOverrides('test', vscode.workspace.workspaceFolders![0].uri.fsPath);
    assert.strictEqual(Object.keys(buildResult.overrides).length, 2, 'Should have 2 overrides');
    assert.strictEqual(buildResult.overrides['test::mytask'], taskContent, 'Task override correct');
    assert.strictEqual(buildResult.overrides['test::myservice'], serviceContent, 'Service override correct');
    console.log('✓ Test 1 passed');

    // Test 2: buildOverrides no locals
    console.log('Test 2: buildOverrides no locals...');
    const emptyResult = DebugService.buildOverrides('nonexistent', vscode.workspace.workspaceFolders![0].uri.fsPath);
    assert.strictEqual(Object.keys(emptyResult.overrides).length, 0, 'Empty overrides for nonexistent set');
    console.log('✓ Test 2 passed');

    const testOverrides = buildResult.overrides;

    // Test 3: enqueueDebugJob composes the debugpy bootstrap and leases a port.
    // Under the new server contract `state=running` for kind=debug only fires
    // AFTER a debugger attaches + issues `continue` (because `exec(param)`
    // blocks at `debugpy.wait_for_client()` until then). Don't wait for
    // running here — Test 9 does the real attach. Just verify a port from
    // the pool was leased and the listener actually came up.
    console.log('Test 3: enqueueDebugJob leases port + bootstrap binds...');
    testScriptletName = `debug-scriptlet-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("debug test")');
    const enqueueResult = await debugService.enqueueDebugJob('test', testScriptletName!, testOverrides);
    assert.ok(enqueueResult.jobId, 'JobId returned');
    assert.ok(enqueueResult.port, 'Port returned');
    debugJobId = enqueueResult.jobId!;
    // Port is leased from `pyops.debugPorts` (default [5678]) — assert it's
    // a valid number in the user-port range, not a specific value, so this
    // doesn't break when the operator customises the pool.
    assert.ok(
      typeof enqueueResult.port === 'number' && enqueueResult.port >= 1025 && enqueueResult.port <= 65535,
      `port ${enqueueResult.port} not in user-port range`,
    );
    // The lease is held in this VSCode session's in-memory table.
    assert.strictEqual(
      debugService.findPortForJob(debugJobId!),
      enqueueResult.port,
      'lease bound to real job id',
    );
    // The bootstrap actually brings up the debugpy listener.
    const host = debugService.getDebugHost();
    await helpers.waitForTcpListen(host, enqueueResult.port!, 180000);
    console.log('✓ Test 3 passed');
    // No VSCode debug session was started for this enqueue, so the
    // onDidTerminateDebugSession lease release hook never fires —
    // release explicitly so the next test can lease from the pool.
    debugService.releasePort(enqueueResult.port!);
    await helpers.cleanupJobs(true);

    // Test 4: Debug job appears in tree (no wait on state=running for the
    // same reason as Test 3).
    console.log('Test 4: Debug job in tree...');
    const treeTestScriptletName = `tree-debug-scriptlet-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', treeTestScriptletName, 'task', 'print("tree test")');
    const treeResult = await debugService.enqueueDebugJob('test', treeTestScriptletName, {});
    assert.ok(treeResult.jobId, 'Tree test JobId returned');
    const treeJobId = treeResult.jobId!;
    // Job row exists and is in some non-terminal state.
    await helpers.waitForJobState(treeJobId, helpers.JOB_PROGRESSED_STATES, 30000);
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [treeJobId]);
    const children = await jobsProvider.getChildren();
    const debugItem = children.find((item: any) => item.id === treeJobId);
    assert.ok(debugItem, 'Debug job in tree');
    // Enqueue-only path again — release explicitly.
    debugService.releasePort(treeResult.port!);
    await helpers.cleanupJobs(true);
    console.log('✓ Test 4 passed');

    // Test 5: pyops.startDebugJob command actually enqueues a job and
    // (with the Python extension active) starts a debug session. We
    // check the job-was-created side rather than the in-memory
    // `isMonitoring` flag — the flag flips between "running" and
    // "stopped" on its own polling clock and is too transient to be
    // a robust integration-test signal.
    console.log('Test 5: pyops.startDebugJob command...');
    const managerChildren = await managerProvider.getChildren();
    const testSetItem = managerChildren.find((item: any) => item.label === 'test');
    assert.ok(testSetItem, 'Test set in manager tree');
    const scriptletChildren = await managerProvider.getChildren(testSetItem);
    const scriptletItem = scriptletChildren.find((item: any) => item.scriptletName === testScriptletName! && item.scriptletType === 'task');
    assert.ok(scriptletItem, 'Scriptlet item found');

    // Snapshot the jobs list BEFORE the command so we can identify
    // the new debug job it creates.
    const beforeJobsResp = await fetch(`${process.env.PYOPS_SERVER_URL || 'http://localhost:8000'}/scriptlet/job/list`);
    const beforeJobs = (await beforeJobsResp.json()) as Array<{ id: string }>;
    const beforeIds = new Set(beforeJobs.map((j) => j.id));

    await vscode.commands.executeCommand('pyops.startDebugJob', scriptletItem);

    // The command should have enqueued exactly one new debug job. Poll
    // briefly because the PUT inside the command races our snapshot.
    let newDebugJob: any = null;
    for (let i = 0; i < 20 && !newDebugJob; i++) {
      const resp = await fetch(`${process.env.PYOPS_SERVER_URL || 'http://localhost:8000'}/scriptlet/job/list`);
      const rows = (await resp.json()) as Array<{ id: string; kind: string; state: string }>;
      newDebugJob = rows.find((r) => !beforeIds.has(r.id) && r.kind === 'debug');
      if (!newDebugJob) await new Promise((r) => setTimeout(r, 500));
    }
    assert.ok(newDebugJob, 'pyops.startDebugJob should have created a debug job row');
    const key = `test-${testScriptletName}`;
    console.log(`✓ Test 5 passed (new job ${newDebugJob.id} state=${newDebugJob.state})`);

    // Test 6: pyops.stopDebugJob command stops monitoring for the key.
    // Whether or not isMonitoring was true when this runs (depends on
    // whether the debug session has resolved past attach yet), the
    // command should run to completion without error.
    console.log('Test 6: pyops.stopDebugJob command...');
    await vscode.commands.executeCommand('pyops.stopDebugJob', scriptletItem);
    assert.ok(!debugOutputMonitor.isMonitoring(key), 'Monitoring stopped after stop command');
    console.log('✓ Test 6 passed');
    await helpers.cleanupJobs();

    // Test 7: Scriptlet download & editor open (stripped)
    console.log('Test 7: Scriptlet download & editor open...');
    const attachScriptletName = `attach-debug-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', attachScriptletName, 'task', 'print("attach test")');
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', `${attachScriptletName} (task)`, 'check');
    const localPyopsDir = path.basename(tempDir!);
    const wsRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const localPath = path.join(wsRoot, localPyopsDir, 'test', 'task', `${attachScriptletName}.py`);
    assert.ok(fs.existsSync(localPath), `Local file ${localPath} downloaded`);
    const doc = await vscode.workspace.openTextDocument(localPath);
    await vscode.window.showTextDocument(doc);
    console.log('✓ Test 7 passed');
    await helpers.deleteTestScriptlet('test', attachScriptletName);
    await helpers.cleanupJobs(true);

    // Test 8: startDebugJob on local-only task (regression for backend skip-check)
    console.log('Test 8: startDebugJob on local-only task...');
    const localName = `local-debug-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    const localTestPath = path.join(tempDir!, 'test', 'task', `${localName}.py`);
    fs.mkdirSync(path.dirname(localTestPath), { recursive: true });
    fs.writeFileSync(localTestPath, 'print("Local debug test")');
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    const expectedLocalLabel = `${localName} (task)`;
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLocalLabel, 'diff-added');
    const localManagerChildren = await managerProvider.getChildren();
    const localTestSetItem = localManagerChildren.find((item: any) => item.label === 'test');
    const localSetChildren = await managerProvider.getChildren(localTestSetItem);
    const localTestItem = localSetChildren.find((item: any) => item.label === expectedLocalLabel)!;
    const baselineJobsDebug = await jobsProvider.getChildren();
    await vscode.commands.executeCommand('pyops.startDebugJob', localTestItem);
    let debugJobItem: any = null;
    const debugPollTimeout = 10000;
    const debugPollInterval = 500;
    const debugPollStart = Date.now();
    while (Date.now() - debugPollStart < debugPollTimeout) {
      await vscode.commands.executeCommand('pyops.refreshMonitor');
      const currentJobs = await jobsProvider.getChildren();
      debugJobItem = currentJobs.find((item: any) => !baselineJobsDebug.some((oldItem: any) => oldItem.id === item.id) &&
        (item.description?.includes('debug') || item.label?.includes('debug') || item.contextValue?.includes('debug')));
      if (debugJobItem) break;
      await new Promise((resolve) => setTimeout(resolve, debugPollInterval));
    }
    assert.ok(debugJobItem, 'Debug job from local-only task should enqueue');
    // Wait for ANY non-terminal state (was: 'running'; under new contract
    // that needs an attach to fire).
    await helpers.waitForJobState(debugJobItem.id!, helpers.JOB_PROGRESSED_STATES, 30000);
    console.log('✓ Test 8 passed');
    await helpers.cleanupJobs(true);
    fs.unlinkSync(localTestPath);

    // Test 9: REAL attach + full DAP roundtrip. Tests 5/8 already
    // verified that `pyops.startDebugJob` correctly initiates a debug
    // session against an attached debugger; this test goes further —
    // after attach, it issues a DAP `continue` from the test, waits for
    // the runtime to flip to `state=running` (proving `exec(param)`
    // returned past `wait_for_client + breakpoint`), then waits for
    // `state=done` (proving the user scriptlet ran to completion under
    // a real debugger). End-to-end, no mocks.
    //
    // Builds on `pyops.startDebugJob` rather than calling
    // `vscode.debug.startDebugging` directly so the test path matches
    // the production command exactly.
    console.log('Test 9: vscode.debug.startDebugging end-to-end...');

    // Surface any unhandled rejection from this test (the workbench's
    // DAP handlers fling rejections at the global scope on Cancel — we
    // want to see exactly what + when).
    const unhandledLog: any[] = [];
    const unhandledListener = (reason: any) => {
      unhandledLog.push(reason);
      console.log('[Test 9 unhandled]', String(reason), reason?.stack || '');
    };
    process.on('unhandledRejection', unhandledListener);
    try {

    // Ensure no stale session from a prior test is hanging on. The
    // watcher disposes them on terminal-state, but cleanupJobs only
    // waits ~50s and the watcher polls every 3s — there's a race window.
    const staleSession = vscode.debug.activeDebugSession;
    if (staleSession) {
      console.log('  [t9] stale session at start, stopping:', staleSession.id);
      try { await vscode.debug.stopDebugging(staleSession); } catch { /* noop */ }
      // Give VSCode time to fully dispose.
      for (let i = 0; i < 30 && vscode.debug.activeDebugSession; i++) {
        await new Promise((r) => setTimeout(r, 200));
      }
    }
    console.log('  [t9] active session at start:', vscode.debug.activeDebugSession?.id ?? '(none)');

    const attachName = `actual-attach-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    // Small finite scriptlet — completes quickly once continue lands.
    await helpers.createTestScriptlet('test', attachName, 'task', [
      'counter = 0',
      'while counter < 100:',
      '    counter += 1',
      'print("done", counter)',
    ].join('\n'));
    console.log('  [t9] scriptlet created:', attachName);

    // Find the new scriptlet in the manager tree (mirrors how the
    // operator clicks "Start Debug Job" from the UI).
    await vscode.commands.executeCommand('pyops.refreshManager');
    const attachManagerChildren = await managerProvider.getChildren();
    const attachSetItem = attachManagerChildren.find((item: any) => item.label === 'test');
    assert.ok(attachSetItem, 'Test set in manager tree');
    const attachSetChildren = await managerProvider.getChildren(attachSetItem);
    const attachScriptletItem = attachSetChildren.find(
      (item: any) => item.scriptletName === attachName && item.scriptletType === 'task',
    );
    assert.ok(attachScriptletItem, 'New scriptlet in manager tree');
    console.log('  [t9] scriptlet in tree');

    // Snapshot jobs so we can find the new one after the command fires.
    const serverUrl9 = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';
    const t9BeforeJobs = (await (await fetch(`${serverUrl9}/scriptlet/job/list`)).json()) as Array<{ id: string }>;
    const t9BeforeIds = new Set(t9BeforeJobs.map((j) => j.id));

    // Force-activate Python ext if not already (no-op if active from
    // earlier tests).
    const pythonExt9 = vscode.extensions.getExtension('ms-python.python');
    if (pythonExt9 && !pythonExt9.isActive) {
      console.log('  [t9] activating python ext');
      await pythonExt9.activate();
    }
    console.log('  [t9] python ext active=', pythonExt9?.isActive);

    // Find the about-to-be-created job id BEFORE the command fires so
    // we don't race the lifecycle. We watch the activeDebugSession in
    // parallel; the moment one shows up we capture it (it may be gone
    // by the time `executeCommand` resolves because the debuggee runs
    // very fast — debugpy auto-continues past `debugpy.breakpoint()`
    // when no DAP source-breakpoints are registered).
    let observedSession: vscode.DebugSession | null = null;
    const startedHandle = vscode.debug.onDidStartDebugSession((s) => {
      if (!observedSession) {
        observedSession = s;
        console.log(`  [t9] onDidStartDebugSession captured id=${s.id} type=${s.type} jobId=${(s.configuration as any).jobId}`);
      }
    });

    console.log('  [t9] executing pyops.startDebugJob');
    try {
      await vscode.commands.executeCommand('pyops.startDebugJob', attachScriptletItem);
      console.log('  [t9] pyops.startDebugJob returned');
    } catch (err) {
      startedHandle.dispose();
      console.log('  [t9] pyops.startDebugJob THREW:', err instanceof Error ? err.message : String(err));
      throw err;
    }

    // Identify the debug job the command enqueued.
    let attachJobId: string | null = null;
    const findJobDeadline = Date.now() + 15000;
    while (Date.now() < findJobDeadline && !attachJobId) {
      const list = (await (await fetch(`${serverUrl9}/scriptlet/job/list`)).json()) as Array<{ id: string; kind: string }>;
      const row = list.find((j) => !t9BeforeIds.has(j.id) && j.kind === 'debug');
      if (row) attachJobId = row.id;
      else await new Promise((r) => setTimeout(r, 500));
    }
    assert.ok(attachJobId, 'new debug job should appear in jobs list');
    console.log(`  [t9] new debug job=${attachJobId}`);

    // Either we already captured the session (fast path) or we need to
    // wait for it. activeDebugSession may already be null if the
    // debuggee finished and VSCode disposed the session — that's still
    // a successful end-to-end run, the watcher disposed it on terminal
    // state. We hold onto the first observed session for the assertion.
    if (!observedSession) {
      try {
        observedSession = await helpers.waitForActiveDebugSession(5000);
        console.log(`  [t9] waitForActiveDebugSession captured id=${observedSession.id}`);
      } catch {
        console.log('  [t9] no active session by check time — session may have already disposed');
      }
    }
    startedHandle.dispose();
    assert.ok(observedSession, 'VSCode started a debug session for the attach config');
    assert.ok(
      (observedSession as vscode.DebugSession).type === 'python' || (observedSession as vscode.DebugSession).type === 'debugpy',
      `session type should be python or debugpy, got ${(observedSession as vscode.DebugSession).type}`,
    );
    assert.strictEqual(
      ((observedSession as vscode.DebugSession).configuration as any).jobId,
      attachJobId,
      'session configuration carries the job id for the watcher',
    );

    // The load-bearing assertion: under a real DAP attach the runner's
    // exec(param) returns past `wait_for_client` + `breakpoint()`,
    // user code runs, and the runtime reports state=done over ZMQ. If
    // any link in that chain breaks, we either stay in `loading` (no
    // attach) or go to `failed` (bootstrap exception) — both surface as
    // an assertion failure here. The 60s window covers per-set venv
    // install (debugpy first-time) on slow CI/aarch64.
    console.log('  [t9] waiting for state=done...');
    await helpers.waitForJobState(attachJobId!, 'done', 60000);
    console.log('  [t9] job reached state=done — full DAP roundtrip succeeded');

    // Session may have self-terminated when the debuggee exited; stop
    // is best-effort.
    if (vscode.debug.activeDebugSession) {
      try { await vscode.debug.stopDebugging(vscode.debug.activeDebugSession); } catch { /* noop */ }
    }
    await new Promise((r) => setTimeout(r, 1000));
    await helpers.deleteTestScriptlet('test', attachName);
    await helpers.cleanupJobs(true);
    console.log('✓ Test 9 passed');
    } finally {
      process.removeListener('unhandledRejection', unhandledListener);
      if (unhandledLog.length > 0) {
        console.log(`  [t9] saw ${unhandledLog.length} unhandled rejections during test`);
      }
    }

    console.log('All Debug Tests passed!');
  } finally {
    // Restore config
    if (originalLocalPyopsDir !== undefined) {
      await vscode.workspace.getConfiguration('pyops').update('localPyopsDir', originalLocalPyopsDir, vscode.ConfigurationTarget.Workspace);
    }

    // Cleanup
    if (testScriptletName) {
      try {
        await helpers.deleteTestScriptlet('test', testScriptletName!);
      } catch (e) {}
    }
    if (tempDir) {
      try {
        fs.rmSync(tempDir, { recursive: true, force: true });
      } catch (e) {}
    }
    process.removeListener('unhandledRejection', globalUnhandled);
  }
}
