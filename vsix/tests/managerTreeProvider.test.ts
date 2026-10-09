import { ManagerTreeDataProvider, ScriptletTreeItem } from '../src/managerTreeProvider';
import { createMockVscodeWindow, createMockApiClient } from './utils/helpers';

jest.mock('vscode');

jest.mock('fs', () => require('./utils/helpers').fsMockFactory());
jest.mock('path', () => require('./utils/helpers').pathMockFactory());


jest.mock('../src/apiClient', () => ({ ApiClient: jest.fn() }));

describe('ManagerTreeDataProvider', () => {
  let provider: ManagerTreeDataProvider;
  let mockContext: any;
  let mockOutputChannel: any;
  let mockApiClient: any;

  beforeEach(() => {
    jest.clearAllMocks();
    createMockVscodeWindow();
    (require('vscode').workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];
    require('./utils/helpers').setupConfigMock();
    mockContext = {
      workspaceState: {
        get: jest.fn(),
      },
    };
    mockOutputChannel = {
      appendLine: jest.fn(),
    };
    const ApiClientClass = require('../src/apiClient').ApiClient;
    mockApiClient = createMockApiClient(ApiClientClass, { headers: { 'Authorization': 'Basic dGVzdHVzZXI6cGFzcw==' } });
    mockApiClient.fetch.mockResolvedValue({
      ok: true,
      json: jest.fn().mockResolvedValue([]),
    });

    const mockFs = require('fs');

    (mockFs.readdirSync as jest.Mock).mockReturnValue([]);
    (mockFs.existsSync as jest.Mock).mockReturnValue(false);

    provider = new ManagerTreeDataProvider(mockContext, mockOutputChannel, mockApiClient);
  });

  describe('getTreeItem', () => {
    it('should return the element', () => {
      const element = { label: 'test' };
      expect(provider.getTreeItem(element)).toBe(element);
    });
  });

  describe('getChildren', () => {
    it('should return no workspace item when no workspace', async () => {
      (require('vscode').workspace.workspaceFolders as any) = undefined;
      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].label).toBe('No Workspace Open');
    });

    it('should return no workspace item when workspace folders is empty', async () => {
      (require('vscode').workspace.workspaceFolders as any) = [];
      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].label).toBe('No Workspace Open');
    });

    it('should return config warning when no valid config', async () => {
      mockApiClient.hasValidConfig.mockReturnValue(false);
      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].contextValue).toBe('noConfig');
    });

    it('should return commands children', async () => {
      require('./utils/helpers').setupConfigMock();
      const element = { contextValue: 'commands' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(2);
      expect(children[0].label).toBe('Create Scriptlet');
      expect(children[1].label).toBe('Refresh');
    });

    it('should return set items', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'mockcode' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('code');
      await provider.refresh();
      const element = { label: 'set1', contextValue: 'pyopsSet' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(1);
      expect((children[0] as ScriptletTreeItem).set).toBe('set1');
    });

    it('should return jslibrary items with correct contextValue', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'jslibrary' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'mockcode' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('code');
      await provider.refresh();
      const element = { label: 'set1', contextValue: 'pyopsSet' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(1);
      expect((children[0] as ScriptletTreeItem).contextValue).toBe('pyopsItem-jslibrary');
    });

    it('should return empty array for pyopsSet with no items', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'mockcode' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('code');
      await provider.refresh();
      const element = { label: 'set2', contextValue: 'pyopsSet' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(0);
    });

    it('should return root sets', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'mockcode' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('code');
      await provider.refresh();
      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].label).toBe('set1');
    });


  });

  describe('fetchScriptlets', () => {
    it('should fetch scriptlets successfully', async () => {
      require('./utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('password');
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([{ set: 'set1' }]),
      });
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([{ set: 'set1' }]);
    });

    it('should handle fetch error', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Error',
      });
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([]);
    });
  });

  describe('downloadScriptlets', () => {
    it('should download scriptlets', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);
      expect(require('fs').writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/task/script1.py',
        'remote code'
      );
    });

    it('mirrors a data/yaml file with .yaml extension locally', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'foo: 1\n' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'config', type: 'data', ext: 'yaml' }]);
      expect(require('fs').writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/data/config.yaml',
        'foo: 1\n'
      );
    });

    it('mirrors a data/json file with .json extension locally', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: '{"k": 1}\n' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'state', type: 'data', ext: 'json' }]);
      expect(require('fs').writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/data/state.json',
        '{"k": 1}\n'
      );
    });

    it('mirrors a docs/*.md markdown file under pyops/{set}/markdown/', async () => {
      // Server stores at docs/intro.md but reports `{type: markdown, name: intro}`
      // — the client mirror uses the per-type folder convention.
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: '# Intro\n' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'intro', type: 'markdown' }]);
      expect(require('fs').writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/markdown/intro.md',
        '# Intro\n'
      );
    });
  });

  describe('fetchScriptlets', () => {
    it('should handle fetch errors', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockRejectedValue(new Error('Network error'));
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([]);
    });

    it('should handle non-ok responses', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Server Error',
      });
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([]);
    });
  });

  describe('downloadScriptlets', () => {
    it('should handle fetch errors during download', async () => {
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockRejectedValue(new Error('Network error'));
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);

      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Error downloading set1::script1:', 'Network error');
      consoleErrorSpy.mockRestore();
    });

    it('should handle non-ok responses during download', async () => {
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Not Found',
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);

      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to fetch set1::script1: Not Found');
      consoleErrorSpy.mockRestore();
    });

    it('should handle file system errors during write', async () => {
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      (require('fs').writeFileSync as jest.Mock).mockImplementation(() => {
        throw new Error('File system error');
      });

      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Error downloading set1::script1:', 'File system error');
      consoleErrorSpy.mockRestore();
    });
  });

  describe('refresh', () => {
    it('should refresh scriptlets', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([]),
      });
      await provider.refresh();
      expect(mockApiClient.fetch).toHaveBeenCalled();
    });

    it('should handle refresh errors gracefully', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockRejectedValue(new Error('Network error'));

      // Should not throw
      await expect(provider.refresh()).resolves.not.toThrow();
    });

    it('should filter out excluded sets', async () => {
      require('./utils/helpers').setupConfigMock({excludedSets: ['excludedSet']});
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([
          { set: 'includedSet', name: 'script1', type: 'task' },
          { set: 'excludedSet', name: 'script2', type: 'task' },
        ]),
      });
      await provider.refresh();
      expect((provider as any)._scriptlets).toEqual([
        { set: 'includedSet', name: 'script1', type: 'task' },
      ]);
    });

    it('should show only whitelisted sets when includedSets is set', async () => {
      require('./utils/helpers').setupConfigMock({includedSets: ['keep']});
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([
          { set: 'keep', name: 'script1', type: 'task' },
          { set: 'drop', name: 'script2', type: 'task' },
        ]),
      });
      await provider.refresh();
      expect((provider as any)._scriptlets).toEqual([
        { set: 'keep', name: 'script1', type: 'task' },
      ]);
    });

    it('should ignore excludedSets when includedSets is non-empty', async () => {
      require('./utils/helpers').setupConfigMock({includedSets: ['keep'], excludedSets: ['keep']});
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([
          { set: 'keep', name: 'script1', type: 'task' },
          { set: 'other', name: 'script2', type: 'task' },
        ]),
      });
      await provider.refresh();
      // 'keep' appears despite being in excludedSets; 'other' is not whitelisted.
      expect((provider as any)._scriptlets).toEqual([
        { set: 'keep', name: 'script1', type: 'task' },
      ]);
    });

    it('should fall back to excludedSets when includedSets is empty', async () => {
      require('./utils/helpers').setupConfigMock({includedSets: [], excludedSets: ['drop']});
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([
          { set: 'keep', name: 'script1', type: 'task' },
          { set: 'drop', name: 'script2', type: 'task' },
        ]),
      });
      await provider.refresh();
      expect((provider as any)._scriptlets).toEqual([
        { set: 'keep', name: 'script1', type: 'task' },
      ]);
    });

    it('applies the set filter to local-only scriptlets and downloads only kept sets', async () => {
      require('./utils/helpers').setupConfigMock({includedSets: ['keep']});
      mockApiClient.fetch.mockImplementation(async (url: string) => ({
        ok: true,
        json: jest.fn().mockResolvedValue(
          url === '/scriptlet/inventory/list'
            ? [{ set: 'keep', name: 's1', type: 'task' }, { set: 'drop', name: 's2', type: 'task' }]
            : { code: 'print(1)' }
        ),
      }));
      // Workspace holds a local-only file of a kept set, one of a non-whitelisted
      // set, and a previously synced copy of the filtered remote drop/s2.
      jest.spyOn(provider as any, 'scanLocalScriptlets').mockResolvedValue([
        { set: 'keep', name: 'local1', type: 'task', isLocalOnly: true },
        { set: 'other', name: 'local2', type: 'task', isLocalOnly: true },
        { set: 'drop', name: 's2', type: 'task', isLocalOnly: true },
      ]);

      await provider.refresh();

      expect((provider as any)._scriptlets).toEqual([
        { set: 'keep', name: 's1', type: 'task' },
        { set: 'keep', name: 'local1', type: 'task', isLocalOnly: true },
      ]);
      const fetchedUrls = mockApiClient.fetch.mock.calls.map((c: any[]) => c[0]);
      expect(fetchedUrls).toContain('/scriptlet/inventory/code?set=keep&name=s1');
      expect(fetchedUrls.filter((u: string) => u.includes('set=drop'))).toEqual([]);
      const writtenPaths = (require('fs').writeFileSync as jest.Mock).mock.calls.map((c: any[]) => c[0]);
      expect(writtenPaths).toContain('/test/workspace/pyops/keep/task/s1.py');
      expect(writtenPaths.filter((p: string) => p.includes('/pyops/drop/'))).toEqual([]);
    });
  });

  describe('getChildren with empty workspaceFolders array', () => {
    it('should return no workspace item when workspace folders is empty array', async () => {
      (require('vscode').workspace.workspaceFolders as any) = [];
      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].label).toBe('No Workspace Open');
    });
  });

  describe('getChildren with pyopsSet filtering', () => {
    it('should return empty array for pyopsSet with no matching items', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'mockcode' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('code');
      await provider.refresh();
      const element = { label: 'set2', contextValue: 'pyopsSet' }; // No items for set2
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(0);
    });
  });

  describe('fetchScriptlets error throttling', () => {
    it('should throttle error messages for non-ok responses', async () => {
      require('./utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('password');
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Server Error',
      });

      // First call should show error message
      await (provider as any).fetchScriptlets();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Failed to fetch scriptlets: Server Error');

      // Reset mocks
      jest.clearAllMocks();
      mockContext.workspaceState.get.mockReturnValue('password');

      // Second call within 30 seconds should not show error message again
      await (provider as any).fetchScriptlets();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(0);
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Failed to fetch scriptlets: Server Error');
    });

    it('should throttle error messages for exceptions', async () => {
      require('./utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('password');
      mockApiClient.fetch.mockRejectedValue(new Error('Network error'));

      // First call should show error message
      await (provider as any).fetchScriptlets();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Error fetching scriptlets: Network error');

      // Reset mocks
      jest.clearAllMocks();
      mockContext.workspaceState.get.mockReturnValue('password');

      // Second call within 30 seconds should not show error message again
      await (provider as any).fetchScriptlets();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(0);
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Error fetching scriptlets: Network error');
    });
  });

  describe('getChildren with unsynced files', () => {
    it('should return scriptlet items with warning icon when unsynced', async () => {
      require('./utils/helpers').setupConfigMock();

      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('different local code');
      await provider.refresh();
      const element = { label: 'set1', contextValue: 'pyopsSet' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(1);
      expect(children[0].iconPath).toEqual(new (require('vscode').ThemeIcon)('warning'));
      expect((children[0] as any).tooltip).toContain('Sync: Unsynced');
    });
  });

  describe('downloadScriptlets file operations', () => {
    it('should write file when local file does not exist', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);
      expect(require('fs').writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/task/script1.py',
        'remote code'
      );
    });

    it('should not write file when local file exists and is synced', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('remote code');
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);
      expect(require('fs').writeFileSync).not.toHaveBeenCalled();
    });

    it('should not write file when local file exists but differs from remote', async () => {
      require('./utils/helpers').setupConfigMock();
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('different local code');
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);
      expect(require('fs').writeFileSync).not.toHaveBeenCalled();
      expect((provider as any)._syncStatus.get('set1-script1-task')).toBe(false);
    });
  });

  describe('scanLocalScriptlets', () => {
    it('returns empty if base path does not exist', async () => {
      require('./utils/helpers').setupConfigMock();
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);

      const result = await (provider as any).scanLocalScriptlets();
      expect(result).toEqual([]);
    });

    it('discovers local-only .py files in type directories', async () => {
      require('./utils/helpers').setupConfigMock();
      const mockFs = require('fs');
      const mockPath = require('path');

      mockPath.join.mockImplementation((...args: string[]) => args.join('/'));
      mockPath.extname = jest.fn((f: string) => {
        const idx = f.lastIndexOf('.');
        return idx >= 0 ? f.substring(idx) : '';
      });
      mockPath.basename.mockImplementation((f: string, ext?: string) => {
        const base = f.split('/').pop() || '';
        return ext ? base.replace(new RegExp(ext.replace(/\./g, '\\.') + '$'), '') : base;
      });

      // Base path exists
      mockFs.existsSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return true;
        if (p === '/test/workspace/pyops/set1') return true;
        if (p === '/test/workspace/pyops/set1/task') return true;
        return false;
      });
      mockFs.readdirSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return ['set1'];
        if (p === '/test/workspace/pyops/set1/task') return ['myscript.py'];
        return [];
      });
      mockFs.statSync.mockImplementation((p: string) => ({
        isDirectory: () => p === '/test/workspace/pyops/set1' || p === '/test/workspace/pyops/set1/task',
      }));

      const result = await (provider as any).scanLocalScriptlets();
      expect(result).toEqual([{ set: 'set1', name: 'myscript', type: 'task', isLocalOnly: true }]);
    });

    it('ignores files with wrong extension', async () => {
      require('./utils/helpers').setupConfigMock();
      const mockFs = require('fs');
      const mockPath = require('path');

      mockPath.join.mockImplementation((...args: string[]) => args.join('/'));
      mockPath.extname = jest.fn((f: string) => {
        const idx = f.lastIndexOf('.');
        return idx >= 0 ? f.substring(idx) : '';
      });

      mockFs.existsSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return true;
        if (p === '/test/workspace/pyops/set1/task') return true;
        return false;
      });
      mockFs.readdirSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return ['set1'];
        if (p === '/test/workspace/pyops/set1/task') return ['notes.txt'];
        return [];
      });
      mockFs.statSync.mockImplementation((p: string) => ({
        isDirectory: () => p === '/test/workspace/pyops/set1' || p === '/test/workspace/pyops/set1/task',
      }));

      const result = await (provider as any).scanLocalScriptlets();
      expect(result).toEqual([]);
    });

    it('discovers yaml and json files under data/ with correct ext tag', async () => {
      require('./utils/helpers').setupConfigMock();
      const mockFs = require('fs');
      const mockPath = require('path');

      mockPath.join.mockImplementation((...args: string[]) => args.join('/'));
      mockPath.extname = jest.fn((f: string) => {
        const idx = f.lastIndexOf('.');
        return idx >= 0 ? f.substring(idx) : '';
      });
      mockPath.basename.mockImplementation((f: string, ext?: string) => {
        const base = f.split('/').pop() || '';
        return ext ? base.replace(new RegExp(ext.replace(/\./g, '\\.') + '$'), '') : base;
      });

      mockFs.existsSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return true;
        if (p === '/test/workspace/pyops/set1') return true;
        if (p === '/test/workspace/pyops/set1/data') return true;
        return false;
      });
      mockFs.readdirSync.mockImplementation((p: string) => {
        if (p === '/test/workspace/pyops') return ['set1'];
        if (p === '/test/workspace/pyops/set1/data') return ['config.yaml', 'state.json', 'ignore.txt'];
        return [];
      });
      mockFs.statSync.mockImplementation((p: string) => ({
        isDirectory: () => p === '/test/workspace/pyops/set1' || p === '/test/workspace/pyops/set1/data',
      }));

      const result = await (provider as any).scanLocalScriptlets();
      expect(result).toEqual([
        { set: 'set1', name: 'config', type: 'data', ext: 'yaml', isLocalOnly: true },
        { set: 'set1', name: 'state', type: 'data', ext: 'json', isLocalOnly: true },
      ]);
    });
  });

  describe('downloadScriptlet', () => {
    it('downloads and writes a single scriptlet', async () => {
      require('./utils/helpers').setupConfigMock();
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'downloaded code' }),
      });

      const treeItem = { set: 'set1', scriptletName: 'script1', scriptletType: 'task' } as any;
      await provider.downloadScriptlet(treeItem);

      expect(mockFs.writeFileSync).toHaveBeenCalledWith(
        '/test/workspace/pyops/set1/task/script1.py',
        'downloaded code'
      );
    });

    it('does nothing for invalid treeItem', async () => {
      jest.clearAllMocks();
      const treeItem = { set: undefined, scriptletName: undefined, scriptletType: undefined } as any;
      await provider.downloadScriptlet(treeItem);
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('handles fetch error', async () => {
      require('./utils/helpers').setupConfigMock();
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      mockApiClient.fetch.mockRejectedValueOnce(new Error('Network error'));

      const treeItem = { set: 'set1', scriptletName: 'script1', scriptletType: 'task' } as any;
      await provider.downloadScriptlet(treeItem);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Error downloading set1::script1:', 'Network error');
      consoleErrorSpy.mockRestore();
    });

    it('handles non-ok response', async () => {
      require('./utils/helpers').setupConfigMock();
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      mockApiClient.fetch.mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      });

      const treeItem = { set: 'set1', scriptletName: 'script1', scriptletType: 'task' } as any;
      await provider.downloadScriptlet(treeItem);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to fetch set1::script1: Not Found');
      consoleErrorSpy.mockRestore();
    });
  });

  describe('getChildren with local-only items', () => {
    it('shows local-only items with diff-added icon', async () => {
      require('./utils/helpers').setupConfigMock();

      // Make refresh return a local-only item
      (provider as any)._scriptlets = [
        { set: 'set1', name: 'localscript', type: 'task', isLocalOnly: true },
      ];

      const element = { label: 'set1', contextValue: 'pyopsSet' };
      const children = await provider.getChildren(element);

      expect(children).toHaveLength(1);
      expect(children[0].iconPath).toEqual(new (require('vscode').ThemeIcon)('diff-added'));
      expect((children[0] as any).contextValue).toBe('pyopsItem-task-local');
      expect((children[0] as any).tooltip).toContain('Local only');
    });
  });

  describe('getChildren root with no scriptlets', () => {
    it('shows no scriptlets available message', async () => {
      require('./utils/helpers').setupConfigMock();
      (provider as any)._scriptlets = [];

      // Mock refresh to keep scriptlets empty
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([]),
      });

      const children = await provider.getChildren();
      expect(children).toHaveLength(1);
      expect(children[0].label).toBe('No scriptlets available');
    });
  });

  describe('fetchScriptlets error handling', () => {
    it('should handle non-ok responses with error message throttling', async () => {
      require('./utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('password');
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Server Error',
      });

      // First call should show error message
      await (provider as any).fetchScriptlets();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Failed to fetch scriptlets: Server Error');

      // Reset mocks
      jest.clearAllMocks();
      mockContext.workspaceState.get.mockReturnValue('password');

      // Second call within 30 seconds should not show error message again
      await (provider as any).fetchScriptlets();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(0);
    });

    it('should handle fetch exceptions with error message throttling', async () => {
      require('./utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('password');
      mockApiClient.fetch.mockRejectedValue(new Error('Network error'));

      // First call should show error message
      await (provider as any).fetchScriptlets();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Error fetching scriptlets: Network error');

      // Reset mocks
      jest.clearAllMocks();
      mockContext.workspaceState.get.mockReturnValue('password');

      // Second call within 30 seconds should not show error message again
      await (provider as any).fetchScriptlets();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(0);
    });
  });
});
