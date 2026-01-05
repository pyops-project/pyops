import { DebugService } from '../../src/services/DebugService';

jest.mock('vscode');
jest.mock('fs');
jest.mock('path');

global.fetch = jest.fn();

const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();

describe('DebugService', () => {
  let mockContext: any;
  let mockOutputChannel: any;
  let service: DebugService;

  beforeEach(() => {
    jest.clearAllMocks();
    const mockFs = require('fs');
    const mockPath = require('path');
    const mockVscode = require('vscode');

    mockVscode.workspace.workspaceFolders = [{ uri: { fsPath: '/test/workspace' } }];
    mockVscode.workspace.getConfiguration.mockReturnValue({
      get: jest.fn((key: string) => {
        const configMap: Record<string, any> = {
          'localPyopsDir': 'pyops',
          'serverUrl': 'http://localhost:8000',
          'username': 'testuser',
          'debugPort': 5678,
        };
        return configMap[key as keyof typeof configMap] ?? '';
      }),
    });

    mockContext = {
      workspaceState: {
        get: jest.fn(() => 'testpass'),
      },
    };

    mockOutputChannel = {
      appendLine: jest.fn(),
    };

    service = new DebugService(mockContext as any, mockOutputChannel as any);
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
    it('enqueues job successfully with auth', async () => {
      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job123' }),
      } as Response);

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({ jobId: 'job123', port: 5678 });
      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/scriptlet/job/item',
        expect.objectContaining({
          method: 'PUT',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
            'Authorization': 'Basic ' + Buffer.from('testuser:testpass').toString('base64'),
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
    });

    it('enqueues without auth if no credentials', async () => {
      mockContext.workspaceState.get.mockReturnValue(undefined);
      (require('vscode').workspace.getConfiguration().get as jest.Mock).mockImplementation((key: string) => key === 'username' ? '' : undefined);

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job123' }),
      } as Response);

      await service.enqueueDebugJob('testset', 'testscript');

      expect(global.fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: { 'Content-Type': 'application/json' },
        })
      );
    });


    it('handles API failure', async () => {
      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: false,
        statusText: 'Bad Request',
        text: () => Promise.resolve('server error'),
      } as Response);

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG enqueueDebugJob] Failed with error: server error');
    });

    it('handles fetch error', async () => {
      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValue(new Error('network error'));

      const result = await service.enqueueDebugJob('testset', 'testscript');

      expect(result).toEqual({});
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[DEBUG enqueueDebugJob] Exception: network error');
    });

    it('passes overrides to request', async () => {
      const testOverrides = { 'set::test': 'code' };

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job: 'job123' }),
      } as Response);

      await service.enqueueDebugJob('testset', 'testscript', testOverrides);

      const call = (global.fetch as jest.MockedFunction<typeof fetch>).mock.calls[0][1] as any;
      expect(JSON.parse(call.body).overrides).toEqual(testOverrides);

    });
  });
});
