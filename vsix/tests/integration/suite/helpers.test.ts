import * as assert from 'assert';
import * as fs from 'fs';
import * as helpers from '../utils/helpers';

export async function run(): Promise<void> {
  console.log('Running Helpers Tests...');

  // Test 1: createTempDir
  console.log('Test 1: Temp dir creation...');
  const dir = helpers.createTempDir();
  assert.ok(fs.existsSync(dir), 'Temp dir must exist');
  console.log('✓ Test 1 passed');

  // Test 2: pollTree function
  console.log('Test 2: pollTree function...');
  const mockProvider = {
    getChildren: async () => [{ label: 'test' }]
  };
  await helpers.pollTree(mockProvider, ['test'], 1000);
  console.log('✓ Test 2 passed');

  // Test 3: waitForJobState function
  console.log('Test 3: waitForJobState function...');
  assert.ok(typeof helpers.waitForJobState === 'function', 'waitForJobState must be a function');
  console.log('✓ Test 3 passed');

  console.log('Helpers Tests passed!');
}
