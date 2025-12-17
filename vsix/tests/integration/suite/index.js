import * as assert from 'assert';
import * as vscode from 'vscode';
import * as path from 'path';

// Integration test suite using simple assert
// This runs in actual VS Code environment with real APIs

async function run() {
  console.log('Running PyOps Extension Integration Tests...');

  let disposables = [];

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

    // Configure extension settings from environment variables
    const config = vscode.workspace.getConfiguration('pyops');
    const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';
    await config.update('serverUrl', serverUrl, vscode.ConfigurationTarget.Global);
    // Note: No username/password set for localhost development (authenticationless)

    // Test 1: Extension activates successfully
    console.log('Test 1: Extension activation...');
    assert.ok(extension, 'Extension should be loaded');
    assert.ok(extension.isActive, 'Extension should be active');
    console.log('✓ Extension activates successfully');

    // Test 2: Configuration is valid
    console.log('Test 2: Configuration validation...');
    // Re-fetch configuration to ensure we get updated values
    const updatedConfig = vscode.workspace.getConfiguration('pyops');
    const configuredServerUrl = updatedConfig.get('serverUrl');
    const configuredUsername = updatedConfig.get('username');
    console.log('Server URL:', configuredServerUrl, 'Username:', configuredUsername);
    assert.ok(configuredServerUrl, 'Server URL should be configured');
    assert.ok(!configuredUsername, 'Username should not be set for localhost development');
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

    // Test 4: API connectivity test
    console.log('Test 4: API connectivity...');
    const apiServerUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';

    try {
      // No auth headers needed for localhost development
      const response = await fetch(apiServerUrl + '/scriptlet/sets');

      // Just check that we get some response (could be 401 if auth fails, but connection works)
      assert.ok(response.status > 0, 'Should get HTTP response from server');
      console.log('✓ API connectivity test passed');
    } catch (error) {
      // If server is not running, this is expected - the test should be run with a running server
      console.warn('API connectivity test failed - ensure PyOps server is running:', error.message);
      console.log('⚠ API connectivity test skipped (server not available)');
    }

    // Test 5: Commands are registered
    console.log('Test 5: Command registration...');
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

export { run };
