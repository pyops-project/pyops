import * as assert from 'assert';
import * as vscode from 'vscode';
import * as helpers from '../utils/helpers';
import { getExtensionState } from '../../../src/extension';

export async function run(): Promise<void> {
  console.log('Running Monitor Tests...');

  let testJobId: string | null = null;
  let testScriptletName: string | null = null;
  let viewJobId: string | null = null;
  let viewScriptletName: string | null = null;
  let bulkIds: string[] = [];

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    const state = getExtensionState();
    const jobsProvider = state?.jobsTreeProvider;
    const jobService = state?.jobService;
    assert.ok(jobsProvider && jobService, 'Jobs provider and service must exist');

    // Cleanup leftover jobs
    await helpers.cleanupJobs();

    // Test 1: Create test job
    console.log('Test 1: Create test job...');
    testScriptletName = `test-scriptlet-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    await helpers.createTestScriptlet('test', testScriptletName!, 'task', 'print("Hello from test")');
    testJobId = await helpers.createTestJob('test', testScriptletName!);

    await vscode.commands.executeCommand('pyops.refreshMonitor');
    console.log('✓ Test 1 passed');

    // Test 2: Tree shows todo job
    console.log('Test 2: Tree shows todo job...');
    await helpers.pollTree(jobsProvider, [testJobId!]);
    console.log('✓ Test 2 passed');

    // Test 3: Job to done & description
    console.log('Test 3: Job tree updates on refresh...');
    await helpers.waitForJobState(testJobId!, 'done');
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    const doneChildren = await jobsProvider.getChildren();
    const doneItem = doneChildren.find((item: any) => item.id === testJobId!);
    assert.ok(doneItem, 'Job visible after refresh');
    assert.ok((doneItem.description || '').includes('done - task'), `Desc: ${doneItem.description || ''}`);

    console.log('✓ Test 3 passed');


    // Test 4: viewJob
    console.log('Test 4: viewJob...');
    viewScriptletName = `view-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    await helpers.createTestScriptlet('test', viewScriptletName!, 'task', 'print("viewJob test")');
    viewJobId = await helpers.createTestJob('test', viewScriptletName!);
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, [viewJobId!]);
    const viewChildren = await jobsProvider.getChildren();
    const viewItem = viewChildren.find((item: any) => item.id === viewJobId!);
    jobService.viewJob(viewItem);
    await helpers.waitForJobState(viewJobId!, 'done');
    console.log('✓ Test 4 passed');



    // Test 5: Bulk cancel
    console.log('Test 5: Bulk cancel...');
    bulkIds = [];
    for(let i = 0; i < 3; i++) {
      bulkIds.push(await helpers.createTestJob('test', testScriptletName!, 'import time\ntime.sleep(5)'));
    }
    await vscode.commands.executeCommand('pyops.refreshMonitor');
    await helpers.pollTree(jobsProvider, bulkIds.slice(0,3));
    // Mock dialog to accept cancel
    const originalShowWarningMessage = vscode.window.showWarningMessage;
    vscode.window.showWarningMessage = async () => 'Yes' as any;
    await jobService.cancelAllJobs();
    vscode.window.showWarningMessage = originalShowWarningMessage;
    // Wait for jobs to be cancelled
    for (const id of bulkIds) {
      await helpers.waitForJobState(id, 'cancelled', 15000);
    }
    console.log('✓ Test 5 passed');

    // Test 6: Graceful error
    console.log('Test 6: Error handling...');
    const fakeItem = undefined as any;
    await jobService.deleteJob(fakeItem);
    console.log('✓ Test 6 passed');


    console.log('All Monitor Tests passed!');
  } finally {
    if (testScriptletName) await helpers.deleteTestScriptlet('test', testScriptletName!);
    if (viewScriptletName) await helpers.deleteTestScriptlet('test', viewScriptletName!);
  }
}
