import { AuthService } from '../../src/services/AuthService';
import { createMockVscodeWindow, setupConfigMock, setProfileConfig, clearProfileConfig } from '../utils/helpers';

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
      secrets: {
        get: jest.fn(),
        store: jest.fn(),
        delete: jest.fn(),
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
      expect(mockContext.secrets.store).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('does nothing when username is empty string', async () => {
      require('../utils/helpers').setupConfigMock({username: ''});

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.secrets.store).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalled();
    });

    it('skips the password prompt in x-user mode', async () => {
      require('../utils/helpers').setupConfigMock({authMode: 'x-user'});
      mockContext.secrets.get.mockResolvedValue(undefined);

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.secrets.get).not.toHaveBeenCalled();
      expect(mockContext.secrets.store).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth mode: X-User (direct); no password required');
    });

    it('logs found credentials when username and password exist', async () => {
      require('../utils/helpers').setupConfigMock();
      mockContext.secrets.get.mockResolvedValue('testpass');

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
      expect(mockContext.secrets.store).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth credentials found in secret storage');
    });

    it('prompts for password, stores it, and logs success when user provides password', async () => {
      require('../utils/helpers').setupConfigMock();
      mockContext.secrets.get.mockResolvedValue(undefined);
      (require('vscode').window.showInputBox as jest.Mock).mockResolvedValue('newpass');

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).toHaveBeenCalledWith({
        prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
        password: true,
      });
      expect(mockContext.secrets.store).toHaveBeenCalledWith('pyops.password', 'newpass');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Auth password stored in secret storage');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalledWith('No auth password provided; Basic Auth may fail');
    });

    it('prompts for password and logs failure when user cancels', async () => {
      require('../utils/helpers').setupConfigMock();
      mockContext.secrets.get.mockResolvedValue(undefined);
      (require('vscode').window.showInputBox as jest.Mock).mockResolvedValue(undefined);

      await AuthService.setupAuth(mockContext, mockOutputChannel);

      expect(mockVscode.window.showInputBox).toHaveBeenCalledWith({
        prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
        password: true,
      });
      expect(mockContext.secrets.store).not.toHaveBeenCalled();
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('No auth password provided; Basic Auth may fail');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalledWith('Auth password stored in secret storage');
    });

    describe('with a PyOps client-config profile', () => {
      afterEach(() => clearProfileConfig());

      it("skips the prompt and logs when the profile api_key supplies the password", async () => {
        setProfileConfig(`
default_profile = "dev"
[profiles.dev]
url = "https://dev.example"
user = "profuser"
api_key = "profkey"
`);
        setupConfigMock({ serverUrl: undefined, username: undefined });
        mockContext.secrets.get.mockResolvedValue(undefined);

        await AuthService.setupAuth(mockContext, mockOutputChannel);

        expect(mockVscode.window.showInputBox).not.toHaveBeenCalled();
        expect(mockContext.secrets.store).not.toHaveBeenCalled();
        expect(mockOutputChannel.appendLine).toHaveBeenCalledWith("Auth: using api_key from profile 'dev'");
      });

      it('prompts when the user is remapped away from the profile identity', async () => {
        setProfileConfig(`
default_profile = "dev"
[profiles.dev]
url = "https://dev.example"
user = "profuser"
api_key = "profkey"
`);
        setupConfigMock({ serverUrl: undefined, username: 'mapped' });
        mockContext.secrets.get.mockResolvedValue(undefined);
        (require('vscode').window.showInputBox as jest.Mock).mockResolvedValue('mappedpass');

        await AuthService.setupAuth(mockContext, mockOutputChannel);

        expect(mockVscode.window.showInputBox).toHaveBeenCalled();
        expect(mockContext.secrets.store).toHaveBeenCalledWith('pyops.password', 'mappedpass');
      });
    });
  });
});
