import { DebugService } from '../../src/services/DebugService';



jest.mock('vscode');

jest.mock('fs', () => require('../utils/helpers').fsMockFactory());
jest.mock('path', () => require('../utils/helpers').pathMockFactory());



const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();

describe('DebugService', () => {
  let mockApiClient: any;
  let mockOutputChannel: any;
  let service: DebugService;

  beforeEach(() => {
    jest.clearAllMocks();
    const mockFs = require('fs');
    const mockPath = require('path');
    const mockVscode = require('vscode');

    mockVscode.workspace.workspaceFolders = [{ uri: { fsPath: '/test/workspace' } }];
    require('../utils/helpers').setupConfigMock({localPyopsDir: 'pyops', serverUrl: 'http://localhost:8000', username: 'testuser'});

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
        .mockReturnValueOnce(['lib.py', 'subdir']) // root
        .mockReturnValueOnce(['sub.py']); // subdir
      mockFs.statSync
        .mockReturnValueOnce({ isDirectory: () => false }) // lib.py
        .mockReturnValueOnce({ isDirectory: () => true }) // subdir
        .mockReturnValueOnce({ isDirectory: () => false }); // sub.py
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

  describe('enqueueDebugJob', () => {
    it('enqueues job successfully', async () => {
      mockApiClient.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ port: 5678 }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ job: 'job123' }),
        });

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({ jobId: 'job123', port: 5678 });
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(
        1,
        '/debug/port',
        expect.objectContaining({
          method: 'GET',
        })
      );
      expect(mockApiClient.fetch).toHaveBeenCalledWith(
        '/scriptlet/job/item',
        expect.objectContaining({
          method: 'PUT',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
          body: JSON.stringify({
            set: 'testset',
            name: 'testscript',
            drop_done: false,
            param: '#>AWAIT_DEBUG',
            kind: 'debug',
            overrides: {},
          }),
        })
      );
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG getDebugPort] Success: 5678');
    });

    it('handles job API failure after port fetch', async () => {
      mockApiClient.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ port: 5678 }),
        })
        .mockResolvedValueOnce({
          ok: false,
          statusText: 'Bad Request',
          text: () => Promise.resolve('server error'),
        });

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith(expect.stringContaining('[DEBUG enqueueDebugJob] Failed with error: server error'));
    });

    it('handles job fetch network error after port', async () => {
      mockApiClient.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ port: 5678 }),
        })
        .mockRejectedValue(new Error('network error'));

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG enqueueDebugJob] Exception: network error');
    });

    it('passes overrides to request', async () => {
      const testOverrides = { 'set::test': 'code' };

      mockApiClient.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ port: 5678 }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ job: 'job123' }),
        });

      await service.enqueueDebugJob('testset', 'testscript', testOverrides);

      const call = mockApiClient.fetch.mock.calls[1][1] as any;
      expect(JSON.parse(call.body).overrides).toEqual(testOverrides);
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG enqueueDebugJob] Overrides: ' + JSON.stringify(testOverrides));
    });

    it('throws on debug port fetch fail', async () => {
        mockApiClient.fetch.mockResolvedValueOnce({
          ok: false,
          status: 400,
          statusText: 'Bad Request',
          text: () => Promise.resolve('no port configured'),
        });

        await expect(service.enqueueDebugJob('testset', 'testscript')).rejects.toThrow('Failed to fetch debug port');
        expect(mockOutputChannel.appendLine).toHaveBeenCalledWith(expect.stringContaining('[DEBUG getDebugPort] Failed: no port configured'));
    });

    it('throws on invalid port from server', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ port: 0 }),
      });

      await expect(service.enqueueDebugJob('testset', 'testscript')).rejects.toThrow('Invalid port');
    });
  });

  describe('getDebugPort', () => {
    it('fetches port successfully', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ port: 5678 }),
      });

      const port = await service.getDebugPort();
      expect(port).toBe(5678);
      expect(mockApiClient.fetch).toHaveBeenCalledWith(
        '/debug/port',
        expect.objectContaining({
          method: 'GET',
        })
      );
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG getDebugPort] Success: 5678');
    });

    it('throws on port fetch fail', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: false,
        text: () => Promise.resolve('No debug_port configured')
      });

      await expect(service.getDebugPort()).rejects.toThrow('Failed to fetch debug port');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith(expect.stringContaining('[DEBUG getDebugPort] Failed'));
    });

    it('throws on invalid port value', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ port: 0 })
      });

      await expect(service.getDebugPort()).rejects.toThrow('Invalid port');
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
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG getDebugHost] Invalid serverUrl invalid, defaulting to localhost');
    });

    it('returns localhost if serverUrl is undefined', () => {
      mockApiClient.getServerUrl.mockReturnValue(undefined);
      expect(service.getDebugHost()).toBe('localhost');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG getDebugHost] Invalid serverUrl undefined, defaulting to localhost');
    });
  });
});
