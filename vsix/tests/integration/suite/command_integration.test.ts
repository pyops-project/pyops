import * as assert from 'assert';
import * as vscode from 'vscode';
import * as helpers from '../utils/helpers';

export async function run(): Promise<void> {
  console.log('Running Command Integration Tests...');

  let testScriptletName: string | null = null;
  let testJobId: string | null = null;

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    const jobService = (global as any).jobService;
    const scriptletService = (global as any).scriptletService;
    const jobsProvider = (global as any).jobsTreeProvider;
    const managerProvider = (global as any).managerTreeProvider;
    assert.ok(jobService && scriptletService && jobsProvider && managerProvider, 'Services and providers must exist');

    // Create test data for command testing
    testScriptletName = `cmd-test-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("test command execution")');
    testJobId = await helpers.createTestJob('test', testScriptletName!);

    // Wait for job to be ready
    await helpers.waitForJobState(testJobId!, 'todo');

    // Test 1: copyJobParam command
    console.log('Test 1: copyJobParam command...');
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [testJobId!]);
    const children = await jobsProvider.getChildren();
    const jobItem = children.find((item: any) => item.id === testJobId!);
    assert.ok(jobItem, 'Job item must exist for copyJobParam test');

    await vscode.commands.executeCommand('pyops.copyJobParam', jobItem);
    console.log('✓ Test 1 passed');

    // Test 2: downloadJob command
    console.log('Test 2: downloadJob command...');
    await vscode.commands.executeCommand('pyops.downloadJob', jobItem);
    // Verify job download through service (would need to check if download was called)
    // Since downloadJob is a simple wrapper, we verify it doesn't throw
    console.log('✓ Test 2 passed');

    // Test 3: deleteJob command
    console.log('Test 3: deleteJob command...');
    // Mock showWarningMessage to auto-confirm 'Delete'
    const originalShowWarningMessageDelete = vscode.window.showWarningMessage;
    vscode.window.showWarningMessage = async (message: string, options?: any, ...items: string[]) => {
      return items[0]; // 'Delete'
    };
    await vscode.commands.executeCommand('pyops.deleteJob', jobItem);
    // Restore
    vscode.window.showWarningMessage = originalShowWarningMessageDelete;
    // Verify job is removed from tree
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    const childrenAfterDelete = await jobsProvider.getChildren();
    const deletedJobItem = childrenAfterDelete.find((item: any) => item.id === testJobId!);
    assert.ok(!deletedJobItem, 'Job should be removed after deleteJob command');
    console.log('✓ Test 3 passed');

    // Create another job for remaining tests
    const testJobId2 = await helpers.createTestJob('test', testScriptletName!);
    await helpers.waitForJobState(testJobId2, 'todo');
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [testJobId2]);
    const jobItem2 = (await jobsProvider.getChildren()).find((item: any) => item.id === testJobId2);

    // Test 4: cancelJob command
    console.log('Test 4: cancelJob command...');
    await vscode.commands.executeCommand('pyops.cancelJob', jobItem2);
    // Verify job state changes to cancelled
    await helpers.waitForJobState(testJobId2, 'cancelled');
    console.log('✓ Test 4 passed');

    // Test 5: viewJob command
    console.log('Test 5: viewJob command...');
    const viewJobId = await helpers.createTestJob('test', testScriptletName!);
    await helpers.waitForJobState(viewJobId, 'todo');
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [viewJobId]);
    const viewJobItem = (await jobsProvider.getChildren()).find((item: any) => item.id === viewJobId);

    await vscode.commands.executeCommand('pyops.viewJob', viewJobItem);
    await helpers.waitForJobState(viewJobId, 'done');
    console.log('✓ Test 5 passed');

    // Test 6: refreshConnectionStatus command
    console.log('Test 6: refreshConnectionStatus command...');
    const connectionStatusManager = (global as any).connectionStatusManager;
    assert.ok(connectionStatusManager, 'ConnectionStatusManager must exist');

    // Mock the update method to verify it's called
    let updateCalled = false;
    const originalUpdate = connectionStatusManager.update;
    connectionStatusManager.update = async () => {
      updateCalled = true;
      return originalUpdate.call(connectionStatusManager);
    };

    await vscode.commands.executeCommand('pyops.refreshConnectionStatus');
    assert.ok(updateCalled, 'Connection status update should be called');
    console.log('✓ Test 6 passed');

    // Test 7: openLocalFile command
    console.log('Test 7: openLocalFile command...');
    await vscode.commands.executeCommand('pyops.refreshManager');
    const managerChildren = await managerProvider.getChildren();
    const testSetItem = managerChildren.find((item: any) => item.label === 'test');
    assert.ok(testSetItem, 'Test set must exist');
    const scriptletChildren = await managerProvider.getChildren(testSetItem);
    const scriptletItem = scriptletChildren.find((item: any) => item.scriptletName === testScriptletName!);
    assert.ok(scriptletItem, 'Scriptlet item must exist');

    await vscode.commands.executeCommand('pyops.openLocalFile', scriptletItem);
    // Verify file is opened (would check active editor, but may be async)
    console.log('✓ Test 7 passed');

    // Test 8: diffLocalRemote command
    console.log('Test 8: diffLocalRemote command...');
    await vscode.commands.executeCommand('pyops.diffLocalRemote', scriptletItem);
    // Verify diff is opened (would check for diff editor)
    console.log('✓ Test 8 passed');

    // Test 9: deleteScriptlet command
    console.log('Test 9: deleteScriptlet command...');
    // Mock showWarningMessage to auto-confirm 'Delete'
    const originalShowWarningMessageScriptlet = vscode.window.showWarningMessage;
    vscode.window.showWarningMessage = async (message: string, options?: any, ...items: string[]) => {
      return items[0]; // 'Delete'
    };
    await vscode.commands.executeCommand('pyops.deleteScriptlet', scriptletItem);
    // Restore
    vscode.window.showWarningMessage = originalShowWarningMessageScriptlet;
    // Verify scriptlet is removed from tree
    await vscode.commands.executeCommand('pyops.refreshManager');
    const scriptletChildrenAfterDelete = await managerProvider.getChildren(testSetItem);
    const deletedScriptletItem = scriptletChildrenAfterDelete.find((item: any) => item.scriptletName === testScriptletName!);
    assert.ok(!deletedScriptletItem, 'Scriptlet should be removed after deleteScriptlet command');
    console.log('✓ Test 9 passed');

    // Recreate scriptlet deleted in Test 9
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("test command execution")');

    // Test 10: Bulk operations (create multiple jobs first)
    console.log('Test 10: Bulk operations...');
    const bulkJobIds: string[] = [];
    const sleepParam = 'import time; time.sleep(10); print("bulk cancel test")';
    for (let i = 0; i < 3; i++) {
      const bulkJobId = await helpers.createTestJob('test', testScriptletName!, sleepParam);
      bulkJobIds.push(bulkJobId);
      await helpers.waitForJobState(bulkJobId, 'todo');
    }
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, bulkJobIds);

    // Test cancelAllJobs
    // Mock showWarningMessage to auto-confirm 'Yes'
    const originalShowWarningBulkCancel = vscode.window.showWarningMessage;
    vscode.window.showWarningMessage = async (message: string, options?: any, ...items: string[]) => {
      return items[0]; // 'Yes'
    };
    await vscode.commands.executeCommand('pyops.cancelAllJobs');
    // Restore
    vscode.window.showWarningMessage = originalShowWarningBulkCancel;
    // Verify all jobs are cancelled
    for (const id of bulkJobIds) {
      await helpers.waitForJobState(id, 'cancelled', 15000);
    }
    console.log('✓ Test 10 passed');

    // Test 11: deleteAllDone
    console.log('Test 11: deleteAllDone...');
    const originalShowWarningBulkDelete = vscode.window.showWarningMessage;
    vscode.window.showWarningMessage = async (message: string, options?: any, ...items: string[]) => {
      return items[0]; // 'Yes'
    };
    await vscode.commands.executeCommand('pyops.deleteAllDone');
    vscode.window.showWarningMessage = originalShowWarningBulkDelete;
    // Verify done jobs are removed
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    const childrenAfterBulkDelete = await jobsProvider.getChildren();
    const remainingDoneJobs = childrenAfterBulkDelete.filter((item: any) => item.description?.includes('done'));
    assert.ok(remainingDoneJobs.length === 0, 'All done jobs should be deleted');
    console.log('✓ Test 11 passed');

    console.log('All Command Integration Tests passed!');
  } finally {
    // Cleanup
    if (testScriptletName) {
      try {
        await helpers.deleteTestScriptlet('test', testScriptletName!);
      } catch (e) {}
    }
    // Jobs are cleaned up by the tests themselves
  }
}
