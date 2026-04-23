const { runTests, downloadAndUnzipVSCode, resolveCliPathFromVSCodeExecutablePath } = require('@vscode/test-electron');
const path = require('path');
const os = require('os');
const fs = require('fs');
const { execSync } = require('child_process');

// Parse command line arguments
const args = process.argv.slice(2);
const serverUrl = args.find(arg => arg.startsWith('--server='))?.split('=')[1] || 'http://localhost:8000';
// Note: No username/password needed for localhost development (authenticationless)

async function main() {
  try {
    console.log('Starting integration test setup...');

    // The folder containing the Extension Manifest package.json
    const extensionDevelopmentPath = path.resolve(__dirname, '../..');

    // The path to the test runner script that will run in VS Code environment
    const extensionTestsPath = path.resolve(extensionDevelopmentPath, 'out/tests/integration/suite/index.js');

    // Create .tmp directory in vsix
    const tmpDir = path.resolve(__dirname, '../../.tmp');
    fs.mkdirSync(tmpDir, { recursive: true });
    console.log('Created tmp directory:', tmpDir);

    // Setup test git repo
    const gitBase = path.resolve(__dirname, '../../../data/git');
    const testRepo = path.join(gitBase, 'test');
    fs.mkdirSync(testRepo, { recursive: true });
    const gitDir = path.join(testRepo, '.git');
    if (!fs.existsSync(gitDir)) {
      execSync('git init', { cwd: testRepo, stdio: 'inherit' });
    }
    // Set config (local)
    execSync('git config user.name "PyOps Integration Test"', { cwd: testRepo });
    execSync('git config user.email "integration@pyops.local"', { cwd: testRepo });
    // Validate
    try {
      execSync('git status --porcelain', { cwd: testRepo });
      execSync('git config --get user.name', { cwd: testRepo });
    } catch (e) {
      throw new Error(`Test repo ${testRepo} invalid Git repo or missing config: ${e.message}`);
    }
    console.log(`✓ Test repo ${testRepo} validated and configured`);

    // Create a unique temporary workspace folder for testing
    const workspaceId = 'workspace-' + Date.now();
    const testWorkspace = path.join(tmpDir, workspaceId);
    fs.mkdirSync(testWorkspace, { recursive: true });
    console.log('Created test workspace:', testWorkspace);

    // Create some dummy files to make it a recognizable workspace
    fs.writeFileSync(path.join(testWorkspace, 'README.md'), '# Test Workspace');
    const vscodeDir = path.join(testWorkspace, '.vscode');
    fs.mkdirSync(vscodeDir, { recursive: true });
    fs.writeFileSync(path.join(vscodeDir, 'settings.json'), JSON.stringify({
      "pyops.serverUrl": "http://localhost:8000",
      "python.defaultInterpreterPath": "/usr/bin/python3"
    }, null, 2));
    console.log('Created workspace files: README.md and .vscode/settings.json');

    // VS Code version to test against
    const version = 'stable'; // or 'insiders'

    // Use a unique user data directory to ensure clean settings for each test run
    const userDataDir = path.join(tmpDir, 'userdata-' + Date.now());
    fs.mkdirSync(path.join(userDataDir, 'User'), { recursive: true });
    console.log('Created user data directory:', userDataDir);

    console.log('Launching VS Code with the following configuration:');
    console.log('- Extension development path:', extensionDevelopmentPath);
    console.log('- Extension tests path:', extensionTestsPath);
    console.log('- Test workspace:', testWorkspace);
    console.log('- User data dir:', userDataDir);
    console.log('- Version:', version);

    // Install Python extension for debug tests
    const vscodeExecutablePathEarly = await downloadAndUnzipVSCode({ version, timeout: 60000 });
    const cliPath = resolveCliPathFromVSCodeExecutablePath(vscodeExecutablePathEarly);
    const cliArgs = ['--install-extension', 'ms-python.python', '--force', '--user-data-dir', userDataDir];
    if (process.getuid && process.getuid() === 0) {
      cliArgs.push('--no-sandbox');
    }
    execSync(`${cliPath} ${cliArgs.join(' ')}`, { cwd: testWorkspace, stdio: 'inherit' });
    console.log('✓ Installed Python extension for debug tests');

    // Download and unzip VS Code
    const vscodeExecutablePath = await runTests({
      vscodeExecutablePath: vscodeExecutablePathEarly,
      extensionDevelopmentPath,
      extensionTestsPath,
      testWorkspace,
      version,
      launchArgs: [
        testWorkspace, // Explicitly open the workspace folder
        '--user-data-dir', userDataDir,
        // '--disable-extensions', // Disabled to allow Python extension
        '--disable-workspace-trust', // Disable workspace trust prompts
        '--no-sandbox', // Required for headless environments
        '--disable-gpu', // Disable GPU acceleration
        '--disable-dev-shm-usage', // Overcome limited resource problems
      ],
      // Pass environment variables to the test process
      extensionTestsEnv: {
        PYOPS_SERVER_URL: serverUrl
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
