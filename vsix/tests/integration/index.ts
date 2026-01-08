import * as path from 'path';

// Integration test runner for VS Code extension
// This runs the test suite directly in the VS Code environment

export async function run(): Promise<void> {
  try {
    // Import and run the test suite
    const testSuite = await import('./suite/index');
    await testSuite.run();
    console.log('Integration tests completed successfully');
  } catch (err) {
    console.error('Failed to run integration tests:', err);
    throw err; // Let VSCode handle the error
  }
}
