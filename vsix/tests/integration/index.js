import * as path from 'path';

// Integration test runner for VS Code extension
// This runs the test suite directly in the VS Code environment

export async function run() {
  try {
    // Import and run the test suite
    const testSuite = await import('./suite/index.js');
    await testSuite.run();
    console.log('Integration tests completed successfully');
  } catch (err) {
    console.error('Failed to run integration tests:', err);
    throw err;
  }
}
