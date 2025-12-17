const { runTests } = require('@vscode/test-electron');
const path = require('path');
const os = require('os');
const fs = require('fs');

// Parse command line arguments
const args = process.argv.slice(2);
const serverUrl = args.find(arg => arg.startsWith('--server='))?.split('=')[1] || 'http://localhost:8000';
// Note: No username/password needed for localhost development (authenticationless)

async function main() {
  try {
    // The folder containing the Extension Manifest package.json
    const extensionDevelopmentPath = path.resolve(__dirname, '../..');

    // The path to the test runner script that will run in VS Code environment
    const extensionTestsPath = path.resolve(extensionDevelopmentPath, 'tests/integration/index.js');

    // Test workspace path (can be customized)
    const testWorkspace = path.resolve(__dirname, '../../test-workspace');

    // VS Code version to test against
    const version = 'stable'; // or 'insiders'

    // Use a unique user data directory to ensure clean settings for each test run
    const userDataDir = path.join(os.tmpdir(), 'vscode-test-userdata-' + Date.now());

    // Create clean user data directory with default settings
    fs.mkdirSync(path.join(userDataDir, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userDataDir, 'User', 'settings.json'), '{"pyops.serverUrl": "http://localhost:8000"}');

    // Download and unzip VS Code
    const vscodeExecutablePath = await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      testWorkspace,
      version,
      launchArgs: [
        '--user-data-dir', userDataDir,
        '--disable-extensions', // Disable other extensions
        '--disable-workspace-trust', // Disable workspace trust prompts
      ],
      // Pass environment variables to the test process
      extensionTestsEnv: {
        PYOPS_SERVER_URL: serverUrl,
        // No auth needed for localhost development
      },
    });

    console.log('Integration tests completed successfully');
  } catch (err) {
    console.error('Failed to run integration tests:', err);
    process.exit(1);
  }
}

main();
