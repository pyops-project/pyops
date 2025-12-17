import * as path from 'path';

// Integration test runner for VS Code extension
// This runs the test suite directly in the VS Code environment

async function runIntegrationTests() {
  try {
    // Import and run the test suite
    const testSuite = await import('./suite/index');
    await testSuite.run();
    console.log('Integration tests completed successfully');
  } catch (err) {
    console.error('Failed to run integration tests:', err);
    process.exit(1);
  }
}

// Run the tests when this module is executed
runIntegrationTests();
