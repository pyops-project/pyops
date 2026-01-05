import * as assert from 'assert';
import * as vscode from 'vscode';
import * as helpers from '../utils/helpers';
import * as fs from 'fs';
import * as path from 'path';

export async function run(): Promise<void> {
  console.log('Running Manager Tests...');

  let testScriptletName: string | undefined;
  let testScriptletCreated = false;
  let testScriptletDeleted = false;

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    const wsRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const localPyopsDir = 'pyops';

    const managerProvider = (global as any).managerTreeProvider;
    const scriptletService = (global as any).scriptletService;
    assert.ok(managerProvider && scriptletService, 'ManagerTreeDataProvider and ScriptletService must exist');

    // Test 1: Create test scriptlet & visibility
    console.log('Test 1: Create test scriptlet & visibility...');
    testScriptletName = `test-scriptlet-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
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
    console.log('✓ Test 1 passed');

    // Test 2: Download scriptlet
    console.log('Test 2: Download scriptlet...');
    await managerProvider.downloadScriptlet(scriptletItem);
    const localPath = path.join(wsRoot, localPyopsDir, 'test', 'task', `${testScriptletName}.py`);
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

    // Test 6: saveRemoteDraft (commit)
    console.log('Test 6: saveRemoteDraft...');
    await scriptletService.saveRemoteDraft(scriptletItem);
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTreeItemIcon(managerProvider, 'test', expectedLabel, 'check');
    console.log('✓ Test 6 passed');

    // Test 7: deleteScriptlet
    console.log('Test 7: deleteScriptlet...');
    await helpers.deleteTestScriptlet('test', testScriptletName!);
    testScriptletDeleted = true;
    await vscode.commands.executeCommand('pyops.refreshManager');
    await helpers.pollTree(managerProvider, ['test']);
    const newSetChildren = await managerProvider.getChildren(testSetItem);
    const newScriptletItem = newSetChildren.find((item: any) => item.label === expectedLabel);
    assert.ok(!newScriptletItem, 'Scriptlet should be gone from tree');
    console.log('✓ Test 7 passed');

    // Test 8: Error handling (graceful on invalid item)
    console.log('Test 8: Error handling...');
    const fakeItem = {} as any;
    await scriptletService.resetLocalFromRemote(fakeItem); // no-op
    await scriptletService.deleteScriptlet(fakeItem); // skipped due to confirm undefined
    console.log('✓ Test 8 passed');

    console.log('All Manager Tests passed!');
  } finally {
    // Cleanup only if not already deleted by test
    if (testScriptletCreated && testScriptletName && !testScriptletDeleted) {
      await helpers.deleteTestScriptlet('test', testScriptletName!);
    }
  }
}
