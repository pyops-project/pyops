import * as assert from 'assert';
import * as vscode from 'vscode';
import { getExtensionState } from '../../../src/extension';

export async function run(): Promise<void> {
  console.log('Running Core Tests...');

  // Setup: Wait for extension to activate
  console.log('Setting up extension...');
  const extension = vscode.extensions.getExtension('pyops-team.pyops');
  if (!extension) {
    throw new Error('PyOps extension not found');
  }
  if (!extension.isActive) {
    await extension.activate();
  }

  // Wait for workspace to be ready
  let attempts = 0;
  while ((!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) && attempts < 50) {
    await new Promise(resolve => setTimeout(resolve, 100));
    attempts++;
  }
  if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
    throw new Error('Workspace not available after waiting');
  }

  // Configure extension settings for localhost (no auth for development)
  const config = vscode.workspace.getConfiguration('pyops');
  await config.update('serverUrl', 'http://localhost:8000', vscode.ConfigurationTarget.Global);

  // Ensure manager is refreshed with the new config to load existing sets
  const managerProvider = getExtensionState()?.managerTreeProvider;
  if (managerProvider) {
    await managerProvider.refresh();
  }

  // Test 1: Extension activates successfully
  console.log('Test 1: Extension activation...');
  assert.ok(extension, 'Extension should be loaded');
  assert.ok(extension.isActive, 'Extension should be active');
  console.log('✓ Test 1 passed');

  // Test 2: Configuration is valid
  console.log('Test 2: Configuration validation...');
  const serverUrl = config.get('serverUrl');
  assert.ok(serverUrl, 'Server URL should be configured');
  assert.strictEqual(serverUrl, 'http://localhost:8000', 'Server URL should be localhost');
  console.log('✓ Test 2 passed');

  // Test 3: Status bar connection
  console.log('Test 3: Status bar connection...');
  assert.ok(getExtensionState()?.connectionStatusManager, 'ConnectionStatusManager must be available');
  console.log('✓ Test 3 passed');

  // Test 4: Can access PyOps views
  console.log('Test 4: PyOps views accessibility...');
  const allCommands = await vscode.commands.getCommands(true);
  const hasManagerView = allCommands.some(cmd => cmd.includes('manager'));
  const hasMonitorView = allCommands.some(cmd => cmd.includes('monitor'));
  assert.ok(hasManagerView, 'Manager view commands should be available');
  assert.ok(hasMonitorView, 'Monitor view commands should be available');
  console.log('✓ Test 4 passed');

  // Test 5: Commands are registered
  console.log('Test 5: Command registration...');
  const commands = await vscode.commands.getCommands(true);
  const pyopsCommands = commands.filter(cmd => cmd.startsWith('pyops.'));
  assert.ok(pyopsCommands.length > 0, 'Should have PyOps commands registered');
  assert.ok(pyopsCommands.includes('pyops.refreshManager'), 'Should have refreshManager command');
  assert.ok(pyopsCommands.includes('pyops.refreshMonitor'), 'Should have refreshMonitor command');
  console.log('✓ Test 5 passed');

  console.log('Core Tests passed!');
}
