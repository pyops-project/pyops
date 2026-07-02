// E2E test runner for VS Code extension (VSIX client axis)
// This runs the test suite directly in the VS Code environment

export async function run(): Promise<void> {
  try {
    // Import and run the test suite
    const testSuite = await import('./suite/index');
    await testSuite.run();
    console.log('E2E tests completed successfully');
  } catch (err) {
    console.error('Failed to run e2e tests:', err);
    throw err; // Let VSCode handle the error
  }
}
