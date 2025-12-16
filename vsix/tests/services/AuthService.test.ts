import { AuthService } from '../../src/services/AuthService';

jest.mock('vscode');

describe('AuthService', () => {
  let mockContext: any;
  let mockOutputChannel: any;

  beforeEach(() => {
    jest.clearAllMocks();
    const mockVscode = require('vscode');

    mockVscode.workspace.getConfiguration = jest.fn();
    mockVscode.window.showInputBox = jest.fn();

    mockContext = {
      workspaceState: {
        get: jest.fn(),
        update: jest.fn(),
      },
    };

    mockOutputChannel = {
      appendLine: jest.fn(),
    };
  });

  describe('setupAuth', () => {
    it('does nothing when no username configured', async () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(undefined),
      });

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('does nothing when username is empty string', async () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(''),
      });

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('logs found credentials when username and password exist', async () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue('testuser'),
      });
      mockContext.workspaceState.get.mockReturnValue('testpass');

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth credentials found in workspace state');
    });

    it('prompts for password, stores it, and logs success when user provides password', async () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue('testuser'),
      });
      mockContext.workspaceState.get.mockReturnValue(undefined);
      mockVscode.window.showInputBox.mockResolvedValue('newpass');

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).toHaveBeenCalledWith({
        prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
        password: true,
      });
      expect(mockContext.workspaceState.update).toHaveBeenCalledWith('pyops.password', 'newpass');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth password stored in workspace state');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalledWith('No auth password provided; Basic Auth may fail');
    });

    it('prompts for password and logs failure when user cancels', async () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue('testuser'),
      });
      mockContext.workspaceState.get.mockReturnValue(undefined);
      mockVscode.window.showInputBox.mockResolvedValue(undefined);

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).toHaveBeenCalledWith({
        prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
        password: true,
      });
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('No auth password provided; Basic Auth may fail');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalledWith('Auth password stored in workspace state');
    });
  });
});
