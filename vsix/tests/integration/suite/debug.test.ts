import * as assert from 'assert';
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as helpers from '../utils/helpers';
import { getExtensionState } from '../../../src/extension';

export async function run(): Promise<void> {
  console.log('Running Debug Tests...');

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

    // Test 3: enqueueDebugJob
    console.log('Test 3: enqueueDebugJob...');
    testScriptletName = `debug-scriptlet-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("debug test")');
    const enqueueResult = await debugService.enqueueDebugJob('test', testScriptletName!, testOverrides);
    assert.ok(enqueueResult.jobId, 'JobId returned');
    debugJobId = enqueueResult.jobId!;
    assert.strictEqual(enqueueResult.port, 15678, 'Default port');
    // Wait for job to start
    await helpers.waitForJobState(debugJobId!, 'running');
    console.log('✓ Test 3 passed');
    // Cleanup jobs after test
    await helpers.cleanupJobs(true);

    // Test 4: Debug job in tree
    console.log('Test 4: Debug job in tree...');
    const treeTestScriptletName = `tree-debug-scriptlet-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', treeTestScriptletName, 'task', 'print("tree test")');
    const treeResult = await debugService.enqueueDebugJob('test', treeTestScriptletName, {});
    assert.ok(treeResult.jobId, 'Tree test JobId returned');
    const treeJobId = treeResult.jobId!;
    await helpers.waitForJobState(treeJobId, 'running');
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [treeJobId]);
    const children = await jobsProvider.getChildren();
    const debugItem = children.find((item: any) => item.id === treeJobId);
    assert.ok(debugItem, 'Debug job in tree');
    // Clean up this test's job
    await helpers.cleanupJobs(true);
    console.log('✓ Test 4 passed');

    // Test 5: pyops.startDebugJob command
    console.log('Test 5: pyops.startDebugJob command...');
    const managerChildren = await managerProvider.getChildren();
    const testSetItem = managerChildren.find((item: any) => item.label === 'test');
    assert.ok(testSetItem, 'Test set in manager tree');
    const scriptletChildren = await managerProvider.getChildren(testSetItem);
    const scriptletItem = scriptletChildren.find((item: any) => item.scriptletName === testScriptletName! && item.scriptletType === 'task');
    assert.ok(scriptletItem, 'Scriptlet item found');
    await vscode.commands.executeCommand('pyops.startDebugJob', scriptletItem);
    // Check monitoring started
    const key = `test-${testScriptletName}`;
    assert.ok(debugOutputMonitor.isMonitoring(key), 'Monitoring started');
    console.log('✓ Test 5 passed');

    // Test 6: pyops.stopDebugJob command
    console.log('Test 6: pyops.stopDebugJob command...');
    await vscode.commands.executeCommand('pyops.stopDebugJob', scriptletItem);
    assert.ok(!debugOutputMonitor.isMonitoring(key), 'Monitoring stopped');
    console.log('✓ Test 6 passed');
    // Cleanup jobs after tests using debugJobId
    await helpers.cleanupJobs();

    // Test 7: Scriptlet download & editor open (stripped)
    console.log('Test 7: Scriptlet download & editor open...');
    const attachScriptletName = `attach-debug-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    await helpers.createTestScriptlet('test', attachScriptletName, 'task', 'print("attach test")');

    // Refresh manager tree to fetch and download scriptlet
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', `${attachScriptletName} (task)`, 'check');

    // Get local path
    const localPyopsDir = path.basename(tempDir!);
    const wsRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const localPath = path.join(wsRoot, localPyopsDir, 'test', 'task', `${attachScriptletName}.py`);
    assert.ok(fs.existsSync(localPath), `Local file ${localPath} downloaded`);

    // Open as active editor (ready for provider/manual attach)
    const doc = await vscode.workspace.openTextDocument(localPath);
    await vscode.window.showTextDocument(doc);
    console.log('✓ Test 7 passed');

    // Cleanup
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

    await helpers.waitForJobState(debugJobItem.id!, 'running', 15000);

    console.log('✓ Test 8 passed');

    await helpers.cleanupJobs(true);

    // Cleanup local-only
    fs.unlinkSync(localTestPath);

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
  }
}
