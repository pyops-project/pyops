import { DebugService } from '../../src/services/DebugService';

jest.mock('vscode');
jest.mock('fs', () => require('../utils/helpers').fsMockFactory());
jest.mock('path', () => require('../utils/helpers').pathMockFactory());


describe('DebugService', () => {
  let mockApiClient: any;
  let mockOutputChannel: any;
  let service: DebugService;

  beforeEach(() => {
    jest.clearAllMocks();
    // The fs mock also answers the PyOps client config lookup; clearAllMocks
    // keeps an earlier test's statSync/readFileSync answers, which would then
    // read as an (unparseable) config. No config file here.
    require('fs').statSync.mockImplementation(() => {
      throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
    });
    const mockVscode = require('vscode');

    mockVscode.workspace.workspaceFolders = [{ uri: { fsPath: '/test/workspace' } }];
    require('../utils/helpers').setupConfigMock({
      localPyopsDir: 'pyops',
      serverUrl: 'http://localhost:8000',
      username: 'testuser',
      debugPorts: [5678, 5679, 5680],
    });

    mockApiClient = {
      getServerUrl: jest.fn().mockReturnValue('http://localhost:8000'),
      getHeaders: jest.fn().mockResolvedValue({ 'Authorization': 'Basic ...' }),
      fetch: jest.fn(),
      hasValidConfig: jest.fn().mockReturnValue(true),
    };

    mockOutputChannel = {
      appendLine: jest.fn(),
    };

    service = new DebugService(mockApiClient as any, mockOutputChannel as any);

    // Reset the static lease table between tests — it's shared across
    // DebugService instances (per-VSCode-session).
    const leases = (DebugService as any)._leases as Map<number, string>;
    leases.clear();
  });

  describe('buildOverrides', () => {
    it('returns empty object if setPath does not exist', () => {
      const mockFs = require('fs');
      const mockPath = require('path');
      mockFs.existsSync.mockReturnValue(false);
      mockPath.join.mockReturnValue('/test/workspace/pyops/testset');

      const result = DebugService.buildOverrides('testset', '/test/workspace');

      expect(result).toEqual({ overrides: {}, pathMappings: [] });
      expect(mockFs.existsSync).toHaveBeenCalledWith('/test/workspace/pyops/testset');
    });

    it('builds overrides from .py files in set directory', () => {
      const mockFs = require('fs');
      const mockPath = require('path');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readdirSync.mockReturnValue(['lib.py']);
      mockFs.statSync.mockReturnValue({ isDirectory: () => false });
      mockFs.readFileSync.mockReturnValue('py code');
      mockPath.join
        .mockReturnValueOnce('/test/workspace/pyops/testset')
        .mockReturnValueOnce('/test/workspace/pyops/testset/lib.py');
      mockPath.relative.mockReturnValue('lib.py');
      mockPath.basename.mockReturnValue('lib');

      const result = DebugService.buildOverrides('testset', '/test/workspace');

      expect(result).toEqual({
        overrides: { 'testset::lib': 'py code' },
        pathMappings: [{ localRoot: '${workspaceFolder}/pyops', remoteRoot: './.debug/src' }]
      });
      expect(mockFs.readFileSync).toHaveBeenCalledWith('/test/workspace/pyops/testset/lib.py', 'utf8');
    });

    it('recursively walks subdirectories for .py files', () => {
      const mockFs = require('fs');
      const mockPath = require('path');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readdirSync
        .mockReturnValueOnce(['lib.py', 'subdir'])
        .mockReturnValueOnce(['sub.py']);
      mockFs.statSync
        .mockReturnValueOnce({ isDirectory: () => false })
        .mockReturnValueOnce({ isDirectory: () => true })
        .mockReturnValueOnce({ isDirectory: () => false });
      mockFs.readFileSync
        .mockReturnValueOnce('lib code')
        .mockReturnValueOnce('sub code');
      mockPath.join
        .mockReturnValueOnce('/test/workspace/pyops/testset')
        .mockReturnValueOnce('/test/workspace/pyops/testset/lib.py')
        .mockReturnValueOnce('/test/workspace/pyops/testset/subdir')
        .mockReturnValueOnce('/test/workspace/pyops/testset/subdir/sub.py');
      mockPath.relative
        .mockReturnValueOnce('lib.py')
        .mockReturnValueOnce('subdir/sub.py');
      mockPath.basename
        .mockReturnValueOnce('lib')
        .mockReturnValueOnce('sub');

      const result = DebugService.buildOverrides('testset', '/test/workspace');

      expect(result).toEqual({
        overrides: {
          'testset::lib': 'lib code',
          'testset::sub': 'sub code',
        },
        pathMappings: [{ localRoot: '${workspaceFolder}/pyops', remoteRoot: './.debug/src' }]
      });
    });

    it('overwrites duplicates with last found file', () => {
      const mockFs = require('fs');
      const mockPath = require('path');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readdirSync.mockReturnValue(['lib.py', 'lib2.py']);
      mockFs.statSync.mockReturnValue({ isDirectory: () => false });
      mockFs.readFileSync
        .mockReturnValueOnce('first code')
        .mockReturnValueOnce('second code');
      mockPath.join
        .mockReturnValueOnce('/test/workspace/pyops/testset')
        .mockReturnValueOnce('/test/workspace/pyops/testset/lib.py')
        .mockReturnValueOnce('/test/workspace/pyops/testset/lib2.py');
      mockPath.relative.mockReturnValue('lib.py');
      mockPath.basename.mockReturnValue('lib');

      const result = DebugService.buildOverrides('testset', '/test/workspace');

      expect(result).toEqual({
        overrides: { 'testset::lib': 'second code' },
        pathMappings: [{ localRoot: '${workspaceFolder}/pyops', remoteRoot: './.debug/src' }]
      });
    });
  });

  describe('composeDebugpyBootstrap', () => {
    it('emits #!pip + debugpy.listen (in-process) + wait_for_client + breakpoint', () => {
      const out = DebugService.composeDebugpyBootstrap(45678);
      expect(out).toContain('#!pip debugpy');
      expect(out).toContain('import debugpy');
      // In-process adapter is non-negotiable — without it debugpy
      // daemonises a child adapter process that survives the job and
      // poisons the port for subsequent debug runs.
      expect(out).toContain('in_process_debug_adapter=True');
      expect(out).toContain('debugpy.listen(("0.0.0.0", 45678)');
      expect(out).toContain('debugpy.wait_for_client()');
      expect(out).toContain('debugpy.breakpoint()');
    });

    it('plugs the leased port verbatim into the bootstrap', () => {
      expect(DebugService.composeDebugpyBootstrap(45679)).toContain('45679');
      expect(DebugService.composeDebugpyBootstrap(60000)).toContain('60000');
    });
  });

  describe('port leasing', () => {
    it('leases the first free port from the pool', () => {
      const lease = service.leasePort('job-a');
      expect(lease.port).toBe(5678);
      expect(lease.jobId).toBe('job-a');
    });

    it('skips occupied ports', () => {
      service.leasePort('job-a');
      const lease = service.leasePort('job-b');
      expect(lease.port).toBe(5679);
    });

    it('throws when the pool is empty', () => {
      require('../utils/helpers').setupConfigMock({
        localPyopsDir: 'pyops', serverUrl: 'http://localhost:8000',
        debugPorts: [],
      });
      expect(() => service.leasePort('job-a')).toThrow(/empty/);
    });

    it('throws when the pool is fully leased', () => {
      service.leasePort('job-a');
      service.leasePort('job-b');
      service.leasePort('job-c');
      expect(() => service.leasePort('job-d')).toThrow(/fully leased/);
    });

    it('release returns the port to the pool', () => {
      const lease = service.leasePort('job-a');
      service.releasePort(lease.port);
      const second = service.leasePort('job-b');
      expect(second.port).toBe(lease.port);
    });

    it('release is idempotent', () => {
      service.releasePort(9999);
      service.releasePort(9999);
      // No throw.
    });

    it('rebindLease updates the holder for an existing lease', () => {
      service.leasePort('placeholder');
      service.rebindLease(5678, 'real-job-id');
      expect(service.findPortForJob('real-job-id')).toBe(5678);
      expect(service.findPortForJob('placeholder')).toBeUndefined();
    });

    it('findPortForJob returns undefined when no lease matches', () => {
      expect(service.findPortForJob('never-leased')).toBeUndefined();
    });
  });

  describe('enqueueDebugJob', () => {
    it('leases a port, composes the debugpy bootstrap, PUTs the job, rebinds the lease', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job123' }),
      });

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({ jobId: 'job123', port: 5678 });
      expect(mockApiClient.fetch).toHaveBeenCalledTimes(1);
      const call = mockApiClient.fetch.mock.calls[0];
      expect(call[0]).toBe('/scriptlet/job/item');
      expect(call[1].method).toBe('PUT');
      const body = JSON.parse(call[1].body);
      expect(body.set).toBe('testset');
      expect(body.name).toBe('testscript');
      expect(body.kind).toBe('debug');
      expect(body.drop_done).toBe(false);
      expect(body.overrides).toEqual({});
      // Composed param has the debugpy bootstrap with the leased port.
      expect(body.param).toContain('#!pip debugpy');
      expect(body.param).toContain('debugpy.listen(("0.0.0.0", 5678)');
      expect(body.param).toContain('in_process_debug_adapter=True');

      // Lease is rebound from the placeholder to the real job id.
      expect(service.findPortForJob('job123')).toBe(5678);
    });

    it('releases the lease on HTTP failure so the port is reusable', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Server Error',
        text: () => Promise.resolve('boom'),
      });

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      // Port returned to the pool.
      const second = service.leasePort('next');
      expect(second.port).toBe(5678);
    });

    it('releases the lease on network exception so the port is reusable', async () => {
      mockApiClient.fetch.mockRejectedValue(new Error('network down'));

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      const second = service.leasePort('next');
      expect(second.port).toBe(5678);
    });

    it('returns {} without hitting the network when the pool is empty', async () => {
      require('../utils/helpers').setupConfigMock({
        localPyopsDir: 'pyops', serverUrl: 'http://localhost:8000',
        debugPorts: [],
      });

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('passes overrides verbatim to the PUT body', async () => {
      const testOverrides = { 'set::test': 'code' };
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job123' }),
      });

      await service.enqueueDebugJob('testset', 'testscript', testOverrides);

      const body = JSON.parse(mockApiClient.fetch.mock.calls[0][1].body);
      expect(body.overrides).toEqual(testOverrides);
    });

    it('defaults kind to "debug" when not specified (backwards compat)', async () => {
      // F5-driven and the older `pyops.startDebugJob` command path
      // both omit the kind argument. They must keep landing on
      // kind=debug — the testdebug variant is opt-in.
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job-default-debug' }),
      });

      await service.enqueueDebugJob('testset', 'testscript');

      const body = JSON.parse(mockApiClient.fetch.mock.calls[0][1].body);
      expect(body.kind).toBe('debug');
    });

    it('passes kind=testdebug through to the PUT body when requested', async () => {
      // The pyops.startTestDebugJob command path supplies
      // kind='testdebug'. The bootstrap, port lease, attach, and
      // pathMappings stay byte-for-byte identical with kind=debug
      // — the only wire-level difference is this string.
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job-td' }),
      });

      const result = await service.enqueueDebugJob('testset', 'testscript', {}, 'testdebug');

      expect(result).toEqual({ jobId: 'job-td', port: 5678 });
      const body = JSON.parse(mockApiClient.fetch.mock.calls[0][1].body);
      expect(body.kind).toBe('testdebug');
      // Bootstrap composition stays the same — debugpy.listen on the leased port.
      expect(body.param).toContain('debugpy.listen(("0.0.0.0", 5678)');
    });
  });

  describe('getDebugHost', () => {
    it('returns hostname from valid serverUrl', () => {
      mockApiClient.getServerUrl.mockReturnValue('http://example.com:8000');
      expect(service.getDebugHost()).toBe('example.com');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('logs warning and returns localhost for invalid serverUrl', () => {
      mockApiClient.getServerUrl.mockReturnValue('invalid');
      expect(service.getDebugHost()).toBe('localhost');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith(
        expect.stringContaining('defaulting to localhost'),
      );
    });

    it('returns localhost if serverUrl is undefined', () => {
      mockApiClient.getServerUrl.mockReturnValue(undefined);
      expect(service.getDebugHost()).toBe('localhost');
    });
  });
});
