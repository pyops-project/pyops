import { ManagerTreeDataProvider, ScriptletTreeItem } from '../src/managerTreeProvider';

jest.mock('vscode');

// Mock fs
jest.mock('fs', () => ({
  existsSync: jest.fn(),
  readFileSync: jest.fn(),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
  readdirSync: jest.fn(),
  statSync: jest.fn(() => ({ isDirectory: jest.fn(() => false) })),
}));

// Mock path
jest.mock('path', () => ({
  join: jest.fn((...args) => args.join('/')),
  relative: jest.fn(),
  basename: jest.fn(),
  dirname: jest.fn(),
}));

// Mock fetch
global.fetch = jest.fn();

// Mock ApiClient
jest.mock('../src/apiClient', () => ({
  ApiClient: jest.fn().mockImplementation(() => ({
    getHeaders: jest.fn().mockResolvedValue({ 'Authorization': 'Basic dGVzdHVzZXI6cGFzcw==' }),
    getServerUrl: jest.fn().mockReturnValue('http://localhost:8000'),
    hasValidConfig: jest.fn().mockReturnValue(true),
  })),
}));

describe('ManagerTreeDataProvider', () => {
  let provider: ManagerTreeDataProvider;
  let mockContext: any;
  let mockOutputChannel: any;
  let mockApiClient: any;

  beforeEach(() => {
    jest.clearAllMocks();
    (require('vscode').workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];
    (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'localPyopsDir') return 'pyops';
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'username') return 'testuser';
        return '';
      }),
    });
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: jest.fn().mockResolvedValue([]),
    });
    mockContext = {
      workspaceState: {
        get: jest.fn(),
      },
    };
    mockOutputChannel = {
      appendLine: jest.fn(),
    };
    const ApiClientMock = require('../src/apiClient').ApiClient;
    mockApiClient = new ApiClientMock();
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      const element = { contextValue: 'commands' };
      const children = await provider.getChildren(element);
      expect(children).toHaveLength(2);
      expect(children[0].label).toBe('Create Scriptlet');
      expect(children[1].label).toBe('Refresh');
    });

    it('should return set items', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
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

    it('should return empty array for pyopsSet with no items', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      mockContext.workspaceState.get.mockReturnValue('password');
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([{ set: 'set1' }]),
      });
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([{ set: 'set1' }]);
    });

    it('should handle fetch error', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: false,
        statusText: 'Error',
      });
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([]);
    });
  });

  describe('downloadScriptlets', () => {
    it('should download scriptlets', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
  });

  describe('fetchScriptlets', () => {
    it('should handle fetch errors', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));
      const result = await (provider as any).fetchScriptlets();
      expect(result).toEqual([]);
    });

    it('should handle non-ok responses', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));
      (require('fs').existsSync as jest.Mock).mockReturnValue(false);

      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Error downloading set1::script1:', 'Network error');
      consoleErrorSpy.mockRestore();
    });

    it('should handle non-ok responses during download', async () => {
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([]),
      });
      await provider.refresh();
      expect(global.fetch).toHaveBeenCalled();
    });

    it('should handle refresh errors gracefully', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

      // Should not throw
      await expect(provider.refresh()).resolves.not.toThrow();
    });

    it('should filter out excluded sets', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'excludedSets') return ['excludedSet'];
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      mockContext.workspaceState.get.mockReturnValue('password');
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      mockContext.workspaceState.get.mockReturnValue('password');
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValueOnce([
          { set: 'set1', name: 'script1', type: 'task' },
        ]),
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue({ code: 'remote code' }),
      });
      (require('fs').existsSync as jest.Mock).mockReturnValue(true);
      (require('fs').readFileSync as jest.Mock).mockReturnValue('remote code');
      await (provider as any).downloadScriptlets([{ set: 'set1', name: 'script1', type: 'task' }]);
      expect(require('fs').writeFileSync).not.toHaveBeenCalled();
    });

    it('should not write file when local file exists but differs from remote', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'localPyopsDir') return 'pyops';
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });
      (global.fetch as jest.Mock).mockResolvedValue({
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

  describe('fetchScriptlets error handling', () => {
    it('should handle non-ok responses with error message throttling', async () => {
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      mockContext.workspaceState.get.mockReturnValue('password');
      (global.fetch as jest.Mock).mockResolvedValue({
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
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn(() => 'http://localhost:8000'),
      });
      mockContext.workspaceState.get.mockReturnValue('password');
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

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
