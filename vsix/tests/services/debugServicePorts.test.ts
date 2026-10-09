import { DebugService } from '../../src/services/DebugService';
import { setupConfigMock, setProfileConfig, clearProfileConfig } from '../utils/helpers';

jest.mock('vscode');

// Real fs (unlike debugService.test.ts) so ProfileService reads the profile fixture.
describe('DebugService port pool from a PyOps client-config profile', () => {
  afterEach(() => {
    (DebugService as any)._leases.clear();
    clearProfileConfig();
  });

  it('leases from the profile debug_ports when pyops.debugPorts is unset', () => {
    setProfileConfig(`
default_profile = "dev"
[profiles.dev]
url = "https://dev.example"
debug_ports = [6000, 6001]
`);
    setupConfigMock({ serverUrl: undefined, username: undefined });
    const service = new DebugService({} as any, { appendLine: jest.fn() } as any);

    expect(service.leasePort('job-a').port).toBe(6000);
    expect(service.leasePort('job-b').port).toBe(6001);
  });
});
