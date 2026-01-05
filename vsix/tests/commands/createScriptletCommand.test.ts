import { CreateScriptletCommand } from '../../src/commands/CreateScriptletCommand';

jest.mock('vscode');
jest.mock('fs');
jest.mock('path', () => ({
  join: jest.fn(),
  dirname: jest.fn()
}));


(global as any).fetch = jest.fn();

describe('CreateScriptletCommand', () => {
  let mockShowQuickPick: jest.Mock;
  let mockShowInputBox: jest.Mock;
  let mockShowErrorMessage: jest.Mock;
  let mockShowInformationMessage: jest.Mock;
  let mockShowWarningMessage: jest.Mock;
  let mockOpenTextDocument: jest.Mock;
  let mockFsExistsSync: jest.Mock;
  let mockFsMkdirSync: jest.Mock;
  let mockFsWriteFileSync: jest.Mock;
  let mockPathJoin: jest.Mock;
  let mockPathDirname: jest.Mock;
  let context: any;

  let outputChannel: any;
  let managerTreeProvider: any;

  beforeEach(() => {
    jest.clearAllMocks();
    const vscode = require('vscode');
    const fs = require('fs');
    const path = require('path');

    (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'localPyopsDir') return 'pyops';
        if (key === 'username') return 'testuser';
        return undefined;
      }),
    });
    (vscode.workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];
    mockShowQuickPick = vscode.window.showQuickPick as jest.Mock;
    mockShowInputBox = vscode.window.showInputBox as jest.Mock;
    mockShowErrorMessage = vscode.window.showErrorMessage as jest.Mock;
    mockShowInformationMessage = vscode.window.showInformationMessage as jest.Mock;
    mockShowWarningMessage = vscode.window.showWarningMessage as jest.Mock;
    mockOpenTextDocument = vscode.workspace.openTextDocument as jest.Mock;
    mockOpenTextDocument.mockResolvedValue({});

    mockFsExistsSync = fs.existsSync as jest.Mock;
    mockFsMkdirSync = fs.mkdirSync as jest.Mock;
    mockFsWriteFileSync = fs.writeFileSync as jest.Mock;
    mockFsExistsSync.mockReturnValue(false);
    mockPathJoin = path.join as jest.Mock;
    mockPathDirname = path.dirname as jest.Mock;


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

  it('should create a task scriptlet locally successfully', async () => {
    const localPath = '/test/workspace/pyops/set1/task/script1.py';
    const dirPath = '/test/workspace/pyops/set1/task';
    mockPathJoin
      .mockReturnValueOnce(localPath) // loop local check
      .mockReturnValueOnce(localPath); // create localPath
    mockPathDirname.mockReturnValue(dirPath);
    mockFsExistsSync
      .mockReturnValueOnce(false) // loop local check
      .mockReturnValueOnce(false); // dir check

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Task' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('script1');
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
    });

    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);


    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(mockFsMkdirSync).toHaveBeenCalledWith(dirPath, { recursive: true });
    expect(mockFsWriteFileSync).toHaveBeenCalledWith(localPath, 'pass');
    expect(managerTreeProvider.refresh).toHaveBeenCalled();
    expect(mockShowInformationMessage).toHaveBeenCalledWith('Created local task set1::script1');
  });

  it('should handle remote duplicate name warn and retry', async () => {
    const localPathGood = '/test/workspace/pyops/set1/task/script1.py';
    const dirPathGood = '/test/workspace/pyops/set1/task';
    mockPathJoin
      .mockReturnValueOnce(localPathGood) // good loop check
      .mockReturnValue(localPathGood); // create
    mockPathDirname.mockReturnValue(dirPathGood);
    mockFsExistsSync
      .mockReturnValueOnce(false) // good local
      .mockReturnValue(false); // dir

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Task' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox
      .mockResolvedValueOnce('existing') // dup remote
      .mockResolvedValueOnce('script1'); // good
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'existing', type: 'task' }]),
    });

    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockShowWarningMessage).toHaveBeenCalledWith("Name 'existing' already exists. Please choose a different name.");
    expect(mockShowInputBox).toHaveBeenCalledTimes(2);
  });


  it('should handle local duplicate name warn and retry', async () => {
    const localPathDup = '/test/workspace/pyops/set1/task/dup.py';
    const localPathGood = '/test/workspace/pyops/set1/task/script1.py';
    const dirPathGood = '/test/workspace/pyops/set1/task';
    mockPathJoin
      .mockReturnValueOnce(localPathDup) // dup loop check
      .mockReturnValueOnce(localPathGood) // good loop check
      .mockReturnValueOnce(localPathGood); // create
    mockPathDirname.mockReturnValue(dirPathGood);
    mockFsExistsSync
      .mockReturnValueOnce(true) // dup local
      .mockReturnValueOnce(false) // good local
      .mockReturnValueOnce(false); // dir

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Task' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox
      .mockResolvedValueOnce('dup') // local dup
      .mockResolvedValueOnce('script1'); // good
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'other', type: 'task' }]),
    });


    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockShowWarningMessage).toHaveBeenCalledWith("Local file 'dup.py' already exists. Please choose a different name.");
    expect(mockShowInputBox).toHaveBeenCalledTimes(2);
  });


  it('should handle no sets available', async () => {
    mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([]),
    });


    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).toHaveBeenCalledWith('No existing sets found. Cannot create scriptlet without a set.');
  });

  it('should reject markdown not README', async () => {
    const localPathMd = '/test/workspace/pyops/set1/markdown/notreadme.md';
    mockPathJoin.mockReturnValueOnce(localPathMd);

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Markdown' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('notreadme');
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'other', type: 'task' }]),
    });

    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).toHaveBeenCalledWith('Markdown only allowed for README');
  });


  it('should handle fetch error', async () => {
    mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
    (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).toHaveBeenCalledWith(expect.stringContaining('Error fetching sets: Network error'));
  });

  it('should create view with empty template', async () => {
    const localPath = '/test/workspace/pyops/set1/view/script1.html';
    const dirPath = '/test/workspace/pyops/set1/view';
    mockPathJoin
      .mockReturnValueOnce(localPath)
      .mockReturnValue(localPath);
    mockPathDirname.mockReturnValue(dirPath);
    mockFsExistsSync
      .mockReturnValueOnce(false)
      .mockReturnValue(false);

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'View' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('script1');
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue([{ set: 'set1', name: 'other', type: 'task' }]),
    });


    await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider);

    expect(mockFsWriteFileSync).toHaveBeenCalledWith(localPath, '');
  });

});
