import { AuthService } from '../../src/services/AuthService';
import { createMockVscodeWindow } from '../utils/helpers';

jest.mock('vscode');

describe('AuthService', () => {
  let mockContext: any;
  let mockOutputChannel: any;
  let mockVscode: any;

  beforeEach(() => {
    jest.clearAllMocks();
    createMockVscodeWindow();
    mockVscode = require('vscode');

    mockVscode.workspace.getConfiguration = jest.fn();

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
      require('../utils/helpers').setupConfigMock({username: undefined});

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('does nothing when username is empty string', async () => {
      require('../utils/helpers').setupConfigMock({username: ''});

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('logs found credentials when username and password exist', async () => {
      require('../utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue('testpass');

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.workspaceState.update).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth credentials found in workspace state');
    });

    it('prompts for password, stores it, and logs success when user provides password', async () => {
      require('../utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue(undefined);
      (require('vscode').window.showInputBox as jest.Mock).mockResolvedValue('newpass');

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
      require('../utils/helpers').setupConfigMock();
      mockContext.workspaceState.get.mockReturnValue(undefined);
      (require('vscode').window.showInputBox as jest.Mock).mockResolvedValue(undefined);

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
