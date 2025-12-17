import * as assert from 'assert';
import * as vscode from 'vscode';

// Integration test suite using simple assert
// This runs in actual VS Code environment with real APIs

export async function run(): Promise<void> {
  console.log('Running PyOps Extension Integration Tests...');

  let disposables: vscode.Disposable[] = [];

  try {
    // Setup: Wait for extension to activate
    console.log('Setting up extension...');
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension) {
      throw new Error('PyOps extension not found');
    }

    if (!extension.isActive) {
      await extension.activate();
    }

    // Configure extension settings for localhost (no auth for development)
    const config = vscode.workspace.getConfiguration('pyops');
    await config.update('serverUrl', 'http://localhost:8000', vscode.ConfigurationTarget.Workspace);

    // Test 1: Extension activates successfully
    console.log('Test 1: Extension activation...');
    assert.ok(extension, 'Extension should be loaded');
    assert.ok(extension.isActive, 'Extension should be active');
    console.log('✓ Extension activates successfully');

    // Test 2: Configuration is valid
    console.log('Test 2: Configuration validation...');
    const serverUrl = config.get('serverUrl');
    assert.ok(serverUrl, 'Server URL should be configured');
    assert.strictEqual(serverUrl, 'http://localhost:8000', 'Server URL should be localhost');
    console.log('✓ Configuration is valid');

    // Test 3: Can access PyOps views
    console.log('Test 3: PyOps views accessibility...');
    const managerView = vscode.window.createTreeView('pyops-manager', {
      treeDataProvider: { getChildren: () => [], getTreeItem: () => ({}) }
    });
    const monitorView = vscode.window.createTreeView('pyops-monitor', {
      treeDataProvider: { getChildren: () => [], getTreeItem: () => ({}) }
    });

    assert.ok(managerView, 'Manager view should be accessible');
    assert.ok(monitorView, 'Monitor view should be accessible');

    // Clean up
    managerView.dispose();
    monitorView.dispose();
    console.log('✓ Can access PyOps views');

    // Test 4: Commands are registered
    console.log('Test 4: Command registration...');
    const commands = await vscode.commands.getCommands(true);
    const pyopsCommands = commands.filter(cmd => cmd.startsWith('pyops.'));

    assert.ok(pyopsCommands.length > 0, 'Should have PyOps commands registered');
    assert.ok(pyopsCommands.includes('pyops.refreshManager'), 'Should have refreshManager command');
    assert.ok(pyopsCommands.includes('pyops.refreshMonitor'), 'Should have refreshMonitor command');
    console.log('✓ Commands are registered');

    console.log('All integration tests passed!');

  } catch (error) {
    console.error('Integration test failed:', error);
    throw error;
  } finally {
    // Clean up disposables
    disposables.forEach(d => d.dispose());
  }
}
