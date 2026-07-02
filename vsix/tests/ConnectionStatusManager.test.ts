import { ConnectionStatusManager } from '../src/ConnectionStatusManager';

jest.mock('vscode');
jest.mock('../src/services/AuthService');

describe('ConnectionStatusManager', () => {
  let manager: ConnectionStatusManager;
  let mockApiClient: any;
  let mockContext: any;
  let mockOutputChannel: any;
  let mockStatusBarItem: any;

  beforeEach(() => {
    jest.clearAllMocks();
    mockStatusBarItem = {
      text: '',
      color: undefined,
      tooltip: '',
      command: '',
      show: jest.fn(),
    };
    (require('vscode').window.createStatusBarItem as jest.Mock).mockReturnValue(mockStatusBarItem);
    require('./utils/helpers').setupConfigMock();
    (require('vscode').commands.executeCommand as jest.Mock).mockResolvedValue(undefined);
    mockContext = {
      secrets: {
        get: jest.fn().mockResolvedValue('password'),
        store: jest.fn(),
        delete: jest.fn(),
      },
    };
    mockOutputChannel = {
      appendLine: jest.fn(),
    };
    mockApiClient = {
      getServerUrl: jest.fn().mockReturnValue('http://localhost:8000'),
      getHeaders: jest.fn().mockResolvedValue({ 'Authorization': 'Basic test' }),
      fetch: jest.fn(),
      hasValidConfig: jest.fn().mockReturnValue(true),
    };
    const mockAuthService = require('../src/services/AuthService');
    mockAuthService.AuthService.setupAuth = jest.fn().mockResolvedValue(undefined);
    manager = new ConnectionStatusManager(mockApiClient, mockContext, mockOutputChannel);
  });

  describe('createStatusBarItem', () => {
    it('should create and return a status bar item with correct properties', () => {
      const item = manager.createStatusBarItem();
      expect(require('vscode').window.createStatusBarItem).toHaveBeenCalledWith(
        require('vscode').StatusBarAlignment.Right,
        100
      );
      expect(item.command).toBe('pyops.refreshConnectionStatus');
      expect(item).toBe(mockStatusBarItem);
    });
  });

  describe('update', () => {
    beforeEach(() => {
      manager.createStatusBarItem(); // Ensure statusBarItem is set
    });

    it('should set status bar to no serverUrl when config has no serverUrl', async () => {
      mockApiClient.getServerUrl.mockReturnValue('');

      await manager.update();

      expect(mockStatusBarItem.text).toBe('$(circle-slash) PyOps: No serverUrl');
      expect(mockStatusBarItem.color).toEqual(
        new (require('vscode').ThemeColor)('statusBarItem.errorForeground')
      );
      expect(mockStatusBarItem.tooltip).toBe(
        'No PyOps serverUrl configured. Open settings (Ctrl+,) to configure serverUrl.'
      );
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Status: No serverUrl available');
      expect(mockStatusBarItem.show).toHaveBeenCalled();
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', false);
    });

    it('should set status bar to connected when fetch succeeds', async () => {
      mockApiClient.fetch.mockResolvedValue({
        ok: true,
      });

      await manager.update();

      expect(mockStatusBarItem.text).toBe('$(check) PyOps: Connected');
      expect(mockStatusBarItem.color).toEqual(
        new (require('vscode').ThemeColor)('statusBarItem.foreground')
      );
      expect(mockStatusBarItem.tooltip).toBe('PyOps connected successfully.');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Status: Connected to http://localhost:8000');
      expect(mockStatusBarItem.show).toHaveBeenCalled();
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', true);
      expect(mockApiClient.fetch).toHaveBeenCalledWith('/');
    });

    it('should set status bar to failed when fetch returns error status', async () => {
      mockApiClient.fetch.mockResolvedValue({
        ok: false,
        status: 500,
      });

      await manager.update();

      expect(mockStatusBarItem.text).toBe('$(circle-slash) PyOps: Failed (500)');
      expect(mockStatusBarItem.color).toEqual(
        new (require('vscode').ThemeColor)('statusBarItem.errorForeground')
      );
      expect(mockStatusBarItem.tooltip).toBe(
        'Connection failed (500). Check server, credentials, and network.'
      );
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Status: Failed to connect to http://localhost:8000 (500)');
      expect(mockStatusBarItem.show).toHaveBeenCalled();
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', false);
    });

    it('should set status bar to error when fetch throws', async () => {
      const error = new Error('Network error');
      mockApiClient.fetch.mockRejectedValue(error);

      await manager.update();

      expect(mockStatusBarItem.text).toBe('$(circle-slash) PyOps: Error (Network error)');
      expect(mockStatusBarItem.color).toEqual(
        new (require('vscode').ThemeColor)('statusBarItem.errorForeground')
      );
      expect(mockStatusBarItem.tooltip).toBe(
        'Error connecting to server: Network error. Check configuration and network.'
      );
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Status: Error connecting to server: Network error');
      expect(mockStatusBarItem.show).toHaveBeenCalled();
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', false);
    });

    it('should handle 401 by clearing password, prompting, and connecting on retry', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({ ok: false, status: 401 }).mockResolvedValueOnce({ ok: true });

      await manager.update();

      expect(mockContext.secrets.delete).toHaveBeenCalledWith('pyops.password');
      expect(require('../src/services/AuthService').AuthService.setupAuth).toHaveBeenCalledWith(mockContext, mockOutputChannel);
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[AUTH] Invalid credentials (401), clearing stored password and prompting for new one.');
      expect(mockStatusBarItem.text).toBe('$(check) PyOps: Connected');
      expect(mockApiClient.fetch).toHaveBeenCalledTimes(2);
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(1, '/');
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(2, '/');
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', true);
    });

    it('should handle 401 and show failed if retry also fails', async () => {
      mockApiClient.fetch.mockResolvedValueOnce({ ok: false, status: 401 }).mockResolvedValueOnce({ ok: false, status: 401 });

      await manager.update();

      expect(mockContext.secrets.delete).toHaveBeenCalledWith('pyops.password');
      expect(require('../src/services/AuthService').AuthService.setupAuth).toHaveBeenCalledWith(mockContext, mockOutputChannel);
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[AUTH] Invalid credentials (401), clearing stored password and prompting for new one.');
      expect(mockStatusBarItem.text).toBe('$(circle-slash) PyOps: Failed (401)');
      expect(mockApiClient.fetch).toHaveBeenCalledTimes(2);
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(1, '/');
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(2, '/');
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', false);
    });

    it('does not clear password or re-prompt on 401 in x-user mode', async () => {
      require('./utils/helpers').setupConfigMock({authMode: 'x-user'});
      mockApiClient.fetch.mockResolvedValueOnce({ ok: false, status: 401 });

      await manager.update();

      expect(mockContext.secrets.delete).not.toHaveBeenCalled();
      expect(require('../src/services/AuthService').AuthService.setupAuth).not.toHaveBeenCalled();
      expect(mockStatusBarItem.text).toBe('$(circle-slash) PyOps: Failed (401)');
      expect(mockApiClient.fetch).toHaveBeenCalledTimes(1);
      expect(require('vscode').commands.executeCommand).toHaveBeenCalledWith('setContext', 'pyops.hasValidConfig', false);
    });

    it('should throw error if statusBarItem not created', async () => {
      manager = new ConnectionStatusManager(mockApiClient, mockContext, mockOutputChannel); // New instance without createStatusBarItem

      await expect(manager.update()).rejects.toThrow('StatusBarItem not created');
    });
  });
});
