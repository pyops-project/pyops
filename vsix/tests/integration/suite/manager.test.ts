import * as assert from 'assert';
import * as vscode from 'vscode';
import * as helpers from '../utils/helpers';
import * as fs from 'fs';
import * as path from 'path';
import { getExtensionState } from '../../../src/extension';

export async function run(): Promise<void> {
  console.log('Running Manager Tests...');

  let testScriptletName: string | undefined;
  let testScriptletCreated = false;
  let testScriptletDeleted = false;
  let localPushedName: string | undefined;

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    const wsRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const localPyopsDir = 'pyops';

    const state = getExtensionState();
    const managerProvider = state?.managerTreeProvider;
    const scriptletService = state?.scriptletService;
    assert.ok(managerProvider && scriptletService, 'ManagerTreeDataProvider and ScriptletService must exist');

    // Test 1: Create test scriptlet & visibility
    console.log('Test 1: Create test scriptlet & visibility...');
    testScriptletName = `test-scriptlet-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    const expectedLabel = `${testScriptletName} (task)`;
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("Hello from test")');
    testScriptletCreated = true;

    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLabel, 'check');

    // Get scriptletItem for service calls
    const managerChildren = await managerProvider.getChildren();
    const testSetItem = managerChildren.find((item: any) => item.label === 'test');
    const setChildren = await managerProvider.getChildren(testSetItem);
    const scriptletItem = setChildren.find((item: any) => item.label === expectedLabel);
    assert.ok(scriptletItem, 'Test scriptlet must be visible');
    const localPath = path.join(wsRoot, localPyopsDir, 'test', 'task', `${testScriptletName}.py`);
    console.log('✓ Test 1 passed');

    // Test 2: Download scriptlet
    console.log('Test 2: Download scriptlet...');
    await managerProvider.downloadScriptlet(scriptletItem);
    assert.ok(fs.existsSync(localPath), 'Scriptlet must be downloaded to local dir');
    console.log('✓ Test 2 passed');

    // Test 3: Local edit -> unsynced icon
    console.log('Test 3: Local edit -> unsynced icon...');
    helpers.editLocalFile(localPath);
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLabel, 'warning');
    console.log('✓ Test 3 passed');

    // Test 4: resetLocalFromRemote -> synced
    console.log('Test 4: resetLocalFromRemote -> synced...');
    await scriptletService.resetLocalFromRemote(scriptletItem);
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLabel, 'check');
    console.log('✓ Test 4 passed');

    // Test 5: writeLocalToRemoteDraft -> synced
    console.log('Test 5: writeLocalToRemoteDraft -> synced...');
    helpers.editLocalFile(localPath);
    await scriptletService.writeLocalToRemoteDraft(scriptletItem);
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLabel, 'check');
    console.log('✓ Test 5 passed');

    // Test 6: deleteScriptlet
    console.log('Test 6: deleteScriptlet...');
    await helpers.deleteTestScriptlet('test', testScriptletName!);
    if (fs.existsSync(localPath)) {
      fs.unlinkSync(localPath);
    }
    testScriptletDeleted = true;
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    const newSetChildren = await managerProvider.getChildren(testSetItem);
    const newScriptletItem = newSetChildren.find((item: any) => item.label === expectedLabel);
    assert.ok(!newScriptletItem, 'Scriptlet should be gone from tree');
    console.log('✓ Test 6 passed');

    // Test 7: Error handling (graceful on invalid item)
    console.log('Test 7: Error handling...');
    const fakeItem = {} as any;
    await scriptletService.resetLocalFromRemote(fakeItem); // no-op
    await scriptletService.deleteScriptlet(fakeItem); // skipped due to confirm undefined
    console.log('✓ Test 7 passed');

    // Test 8: Local-only scriptlet visibility & debug support
    console.log('Test 8: Local-only scriptlet...');
    const localName = `localtest-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    const localTestPath = path.join(wsRoot, localPyopsDir, 'test', 'task', `${localName}.py`);
    fs.mkdirSync(path.dirname(localTestPath), { recursive: true });
    fs.writeFileSync(localTestPath, 'print("Local only test")');
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    const expectedLocalLabel = `${localName} (task)`;
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLocalLabel, 'diff-added');
    const test9ManagerChildren = await managerProvider.getChildren();
    const test9TestSetItem = test9ManagerChildren.find((item: any) => item.label === 'test');
    const test9SetChildren = await managerProvider.getChildren(test9TestSetItem);
    const localTestItem = test9SetChildren.find((item: any) => item.label === expectedLocalLabel)!;
    // Verify properties
    assert.strictEqual(localTestItem.contextValue, 'pyopsItem-task-local');
    assert(String(localTestItem.description).endsWith('(local)'));
    assert.strictEqual((localTestItem.iconPath as vscode.ThemeIcon).id, 'diff-added');
    // Verify contextValue supports debug menu (matches package.json when regex)
    const debugRegex = /^pyopsItem-task(-local)?$/;
    assert.ok(debugRegex.test(localTestItem.contextValue!), 'ContextValue must support debug command');
    // Verify contextValue supports writeLocalToRemoteDraft & deleteScriptlet menus (matches package.json when regex)
    const writeRegex = /^pyopsItem-(task|service|library|controller|view|markdown|jslibrary|data)(-local)?$/;
    assert.ok(writeRegex.test(localTestItem.contextValue!), 'ContextValue must support writeLocalToRemoteDraft command');
    
    // Test writeLocalToRemoteDraft on local-only -> auto-create + draft -> no longer local-only
    console.log('Test writeLocalToRemoteDraft on local-only...');
    await scriptletService.writeLocalToRemoteDraft(localTestItem);
    localPushedName = localName;
    await vscode.commands.executeCommand('pyops.refreshManager');
    // Poll for icon change from diff-added to check (synced)
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLocalLabel, 'check');
    const postWriteManagerChildren = await managerProvider.getChildren();
    const postWriteTestSetItem = postWriteManagerChildren.find((item: any) => item.label === 'test');
    const postWriteChildren = await managerProvider.getChildren(postWriteTestSetItem);
    const postWriteItem = postWriteChildren?.find((item: any) => item.label === expectedLocalLabel);
    assert.ok(postWriteItem, 'writeLocalToRemoteDraft should succeed and change status');
    assert.notStrictEqual(postWriteItem.contextValue, 'pyopsItem-task-local', 'No longer local-only');
    console.log('✓ writeLocalToRemoteDraft on local-only passed');

    console.log('✓ Test 8 passed');

    // Test 9: JS Library support
    console.log('Test 9: JS Library support...');
    const jsName = `jslib-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    const jsTestPath = path.join(wsRoot, localPyopsDir, 'test', 'jslibrary', `${jsName}.js`);
    fs.mkdirSync(path.dirname(jsTestPath), { recursive: true });
    fs.writeFileSync(jsTestPath, 'console.log("JS Library test");');
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    const expectedJsLabel = `${jsName} (jslibrary)`;
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedJsLabel, 'diff-added');
    const test10ManagerChildren = await managerProvider.getChildren();
    const test10TestSetItem = test10ManagerChildren.find((item: any) => item.label === 'test');
    const test10SetChildren = await managerProvider.getChildren(test10TestSetItem);
    const jsTestItem = test10SetChildren.find((item: any) => item.label === expectedJsLabel)!;
    assert.strictEqual(jsTestItem.contextValue, 'pyopsItem-jslibrary-local');
    assert(String(jsTestItem.description).endsWith('(local)'));
    assert.strictEqual((jsTestItem.iconPath as vscode.ThemeIcon).id, 'diff-added');
    // Verify contextValue supports sync menus (matches package.json when regex)
    const jsWriteRegex = /^pyopsItem-(task|service|library|controller|view|markdown|jslibrary|data)(-local)?$/;
    assert.ok(jsWriteRegex.test(jsTestItem.contextValue!), 'ContextValue must support writeLocalToRemoteDraft command');

    console.log('✓ Test 10 passed');

    // Cleanup local
    fs.unlinkSync(localTestPath);
    fs.unlinkSync(jsTestPath);

    console.log('All Manager Tests passed!');
  } finally {
    // Cleanup only if not already deleted by test
    if (testScriptletCreated && testScriptletName && !testScriptletDeleted) {
      try { await helpers.deleteTestScriptlet('test', testScriptletName!); } catch (e) {}
    }
    // Cleanup remote scriptlet created by writeLocalToRemoteDraft in Test 9
    if (localPushedName) {
      try { await helpers.deleteTestScriptlet('test', localPushedName); } catch (e) {}
    }
  }
}
