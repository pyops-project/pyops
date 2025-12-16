import { CreateScriptletCommand } from '../../src/commands/CreateScriptletCommand';

jest.mock('vscode');

global.fetch = jest.fn();

describe('CreateScriptletCommand', () => {
  let mockShowQuickPick: jest.Mock;
  let mockShowInputBox: jest.Mock;
  let mockShowErrorMessage: jest.Mock;
  let mockShowInformationMessage: jest.Mock;
  let mockShowWarningMessage: jest.Mock;
  let mockWorkspaceOpenTextDocument: jest.Mock;
  let context: any;
  let outputChannel: any;
  let managerTreeProvider: any;

  beforeEach(() => {
    jest.clearAllMocks();
    const vscode = require('vscode');
    (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'localPyopsDir') return 'pyops';
        if (key === 'username') return 'testuser';
        return undefined;
      }),
    });
    (vscode.workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];
    (vscode.workspace.openTextDocument as jest.Mock).mockResolvedValue({});
    (vscode.commands.executeCommand as jest.Mock);
    (vscode.Uri.file as jest.Mock).mockImplementation((path) => ({ fsPath: path }));
    mockShowQuickPick = vscode.window.showQuickPick as jest.Mock;
    mockShowInputBox = vscode.window.showInputBox as jest.Mock;
    mockShowErrorMessage = vscode.window.showErrorMessage as jest.Mock;
    mockShowInformationMessage = vscode.window.showInformationMessage as jest.Mock;
    mockShowWarningMessage = vscode.window.showWarningMessage as jest.Mock;
    mockWorkspaceOpenTextDocument = vscode.workspace.openTextDocument as jest.Mock;
    context = {
      workspaceState: {
        get: jest.fn((key) => key === 'pyops.password' ? 'password' : undefined),
      },
    };
    outputChannel = { appendLine: jest.fn() };
    managerTreeProvider = { refresh: jest.fn() };
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: jest.fn().mockResolvedValue([]),
      text: jest.fn().mockResolvedValue(''),
    });
  });

  describe('execute', () => {
    it('should create a task scriptlet successfully', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: true,
          text: jest.fn().mockResolvedValue(''),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'script1', type: 'task' }]),
        });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowQuickPick).toHaveBeenCalledWith(
        [
          { label: 'Task', description: 'Python task script' },
          { label: 'Library', description: 'Python library module' },
          { label: 'Controller', description: 'Python controller' },
          { label: 'View', description: 'HTML view template' },
          { label: 'Markdown', description: 'Markdown document (README only)' }
        ],
        { placeHolder: 'Select scriptlet type' }
      );
      expect(mockShowQuickPick).toHaveBeenCalledWith([{ label: 'set1' }], { placeHolder: 'Select set' });
      expect(mockShowInputBox).toHaveBeenCalledWith({
        prompt: 'Enter unique name',
      });
      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/inventory/list', {
        headers: { 'Authorization': 'Basic ' + Buffer.from('testuser:password').toString('base64') },
      });
      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/inventory/item', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Basic ' + Buffer.from('testuser:password').toString('base64'),
        },
        body: JSON.stringify({ set: 'set1', name: 'script1', type: 'task' }),
      });
      expect(managerTreeProvider.refresh).toHaveBeenCalled();
      expect(mockShowInformationMessage).toHaveBeenCalledWith('Created task set1::script1');
      expect(mockWorkspaceOpenTextDocument).toHaveBeenCalledWith({ fsPath: '/test/workspace/pyops/set1/task/script1.py' });
    });

    it('should handle no sets available', async () => {
      mockShowQuickPick.mockResolvedValue({ label: 'Task' });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue([]),
      });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('No existing sets found. Cannot create scriptlet without a set.');
    });

    it('should validate duplicate name', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValueOnce('existing').mockResolvedValueOnce('newname');
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
      });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowWarningMessage).toHaveBeenCalledWith('Name \'existing\' already exists. Please choose a different name.');
      expect(mockShowInputBox).toHaveBeenCalledTimes(2);
    });

    it('should reject markdown not README', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Markdown' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('notreadme');
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue([{ set: 'set1' }]),
      });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('Markdown only allowed for README');
    });

    it('should handle fetch error', async () => {
      mockShowQuickPick.mockResolvedValue({ label: 'Task' });
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('Error fetching sets: Network error');
    });

    it('should handle creation PUT request failure', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: false,
          text: jest.fn().mockResolvedValue('Creation failed'),
        });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('Failed to create: Creation failed');
    });

    it('should handle verification fetch failure', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: true,
          text: jest.fn().mockResolvedValue(''),
        })
        .mockRejectedValueOnce(new Error('Verification network error'));

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowInformationMessage).toHaveBeenCalledWith('Created task set1::script1 (verification failed)');
    });

    it('should handle workspace opening failure', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      mockWorkspaceOpenTextDocument.mockRejectedValue(new Error('Open document failed'));
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: true,
          text: jest.fn().mockResolvedValue(''),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'script1', type: 'task' }]),
        });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowInformationMessage).toHaveBeenCalledWith('Created task set1::script1');
      expect(managerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('should handle creation network error', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockRejectedValueOnce(new Error('Creation network error'));

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('Error creating scriptlet: Creation network error');
    });

    it('should handle verification fetch failure', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: true,
          text: jest.fn().mockResolvedValue(''),
        })
        .mockResolvedValueOnce({
          ok: false,
          statusText: 'Verification failed',
        });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowInformationMessage).toHaveBeenCalledWith('Created task set1::script1 (verification failed)');
      expect(managerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('should handle verification succeeds but scriptlet not found', async () => {
      mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
      mockShowQuickPick.mockResolvedValueOnce({ label: 'set1' });
      mockShowInputBox.mockResolvedValue('script1');
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
        })
        .mockResolvedValueOnce({
          ok: true,
          text: jest.fn().mockResolvedValue(''),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]), // scriptlet not in list
        });

      await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

      expect(mockShowErrorMessage).toHaveBeenCalledWith('Creation response OK but scriptlet \'set1::script1\' not found after attempt. Server may not have created it.');
      expect(managerTreeProvider.refresh).not.toHaveBeenCalled();
    });
  });
});
