import * as core from './core.test';
import * as helpers from './helpers.test';
import * as manager from './manager.test';
import * as monitor from './monitor.test';
import * as content from './content.test';
import * as debug from './debug.test';
import * as command_integration from './command_integration.test';

export async function run(): Promise<void> {
  console.log('Running PyOps Integration Tests...');

  await core.run();
  await helpers.run();
  await manager.run();
  await monitor.run();
  await content.run();
  await debug.run();
  await command_integration.run();

  console.log('All integration tests passed!');
}
