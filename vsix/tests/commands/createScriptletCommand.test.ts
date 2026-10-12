import { CreateScriptletCommand } from '../../src/commands/CreateScriptletCommand';
import { createMockTreeProvider } from '../utils/helpers';

jest.mock('vscode');

jest.mock('fs', () => require('../utils/helpers').fsMockFactory());
jest.mock('path', () => require('../utils/helpers').pathMockFactory());



describe('CreateScriptletCommand', () => {
  // GET /scriptlet/inventory/sets answers `sets`, GET /scriptlet/inventory/list
  // answers `scriptlets`.
  const mockInventory = (sets: string[], scriptlets: any[]) => {
    mockApiClient.fetch.mockImplementation(async (url: string) => ({
      ok: true,
      json: jest.fn().mockResolvedValue(url === '/scriptlet/inventory/sets' ? { sets } : scriptlets),
    }));
  };

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
  let mockApiClient: any;

  let outputChannel: any;
  let managerTreeProvider: any;

  beforeEach(() => {
    jest.clearAllMocks();
    const vscode = require('vscode');
    const fs = require('fs');
    const path = require('path');

    require('../utils/helpers').setupConfigMock({serverUrl: 'http://localhost:8000', localPyopsDir: 'pyops', username: 'testuser'});
    const mockWindow = require('../utils/helpers').createMockVscodeWindow();
    (vscode.workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];
    mockShowQuickPick = mockWindow.showQuickPick;
    mockShowInputBox = mockWindow.showInputBox;
    mockShowErrorMessage = mockWindow.showErrorMessage;
    mockShowInformationMessage = mockWindow.showInformationMessage;
    mockShowWarningMessage = mockWindow.showWarningMessage;
    mockOpenTextDocument = vscode.workspace.openTextDocument as jest.Mock;
    mockOpenTextDocument.mockResolvedValue({});

    mockFsExistsSync = fs.existsSync as jest.Mock;
    mockFsMkdirSync = fs.mkdirSync as jest.Mock;
    mockFsWriteFileSync = fs.writeFileSync as jest.Mock;
    mockFsExistsSync.mockReturnValue(false);
    mockPathJoin = path.join as jest.Mock;
    mockPathDirname = path.dirname as jest.Mock;


    mockApiClient = {
      fetch: jest.fn(),
      getServerUrl: jest.fn(),
    };
    outputChannel = { appendLine: jest.fn() };
    managerTreeProvider = createMockTreeProvider('manager');
    mockApiClient.fetch.mockResolvedValue({
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
    mockInventory(['set1'], [{ set: 'set1', name: 'existing', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);


    expect(mockApiClient.fetch).toHaveBeenCalledTimes(2);
    expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/inventory/sets');
    expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/inventory/list');
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
    mockInventory(['set1'], [{ set: 'set1', name: 'existing', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

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
    mockInventory(['set1'], [{ set: 'set1', name: 'other', type: 'task' }]);


    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    expect(mockShowWarningMessage).toHaveBeenCalledWith("Local file 'dup.py' already exists. Please choose a different name.");
    expect(mockShowInputBox).toHaveBeenCalledTimes(2);
  });


  it('should handle no sets available', async () => {
    mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
    mockInventory([], []);


    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).toHaveBeenCalledWith('No existing sets found. Cannot create scriptlet without a set.');
  });

  it('accepts markdown with non-README name (docs/<name>.md)', async () => {
    // 3.3.0 supports multi-markdown: docs/<name>.md alongside README.md.
    // The old "Markdown only allowed for README" restriction was removed.
    const localPathMd = '/test/workspace/pyops/set1/markdown/intro.md';
    mockPathJoin.mockReturnValueOnce(localPathMd).mockReturnValueOnce(localPathMd);

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Markdown' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('intro');
    mockInventory(['set1'], [{ set: 'set1', name: 'other', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).not.toHaveBeenCalledWith(expect.stringContaining('Markdown only allowed'));
  });

  it('offers Data in the type picker (yaml default template)', async () => {
    const localPathData = '/test/workspace/pyops/set1/data/cfg.yaml';
    mockPathJoin.mockReturnValueOnce(localPathData).mockReturnValueOnce(localPathData);

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Data' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('cfg');
    mockInventory(['set1'], [{ set: 'set1', name: 'other', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    // First quickpick call's choices include a Data entry.
    const firstQuickPickArgs = mockShowQuickPick.mock.calls[0][0];
    expect(firstQuickPickArgs).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: 'Data' })
    ]));
    // File written empty (no `{}` placeholder; matches the server-side
    // type=data initial_content in inventory.py).
    expect(require('fs').writeFileSync).toHaveBeenCalledWith(localPathData, '');
  });


  it('should handle fetch error', async () => {
    mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' });
    mockApiClient.fetch.mockRejectedValue(new Error('Network error'));

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

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
    mockInventory(['set1'], [{ set: 'set1', name: 'other', type: 'task' }]);


    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    expect(mockFsWriteFileSync).toHaveBeenCalledWith(localPath, '');
  });

  it('should create a jslibrary scriptlet locally successfully', async () => {
    const localPath = '/test/workspace/pyops/set1/jslibrary/script1.js';
    const dirPath = '/test/workspace/pyops/set1/jslibrary';
    mockPathJoin
      .mockReturnValueOnce(localPath) // loop local check
      .mockReturnValueOnce(localPath); // create localPath
    mockPathDirname.mockReturnValue(dirPath);
    mockFsExistsSync
      .mockReturnValueOnce(false) // loop local check
      .mockReturnValueOnce(false); // dir check

    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'JS Library' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockShowInputBox.mockResolvedValue('script1');
    mockInventory(['set1'], [{ set: 'set1', name: 'existing', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);


    expect(mockApiClient.fetch).toHaveBeenCalledTimes(2);
    expect(mockFsMkdirSync).toHaveBeenCalledWith(dirPath, { recursive: true });
    expect(mockFsWriteFileSync).toHaveBeenCalledWith(localPath, '');
    expect(managerTreeProvider.refresh).toHaveBeenCalled();
    expect(mockShowInformationMessage).toHaveBeenCalledWith('Created local jslibrary set1::script1');
  });

  it('offers a set that has no scriptlets yet', async () => {
    mockShowQuickPick.mockResolvedValueOnce({ label: 'Task' }).mockResolvedValueOnce(undefined);
    mockInventory(['empty', 'set1'], [{ set: 'set1', name: 'existing', type: 'task' }]);

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    const setChoices = mockShowQuickPick.mock.calls[1][0];
    expect(setChoices).toEqual([{ label: 'empty' }, { label: 'set1' }]);
  });

  it('shows an error when the scriptlet list cannot be fetched', async () => {
    mockShowQuickPick
      .mockResolvedValueOnce({ label: 'Task' })
      .mockResolvedValueOnce({ label: 'set1' });
    mockApiClient.fetch.mockImplementation(async (url: string) => url === '/scriptlet/inventory/sets'
      ? { ok: true, json: jest.fn().mockResolvedValue({ sets: ['set1'] }) }
      : { ok: false, status: 500, statusText: 'Internal Server Error', json: jest.fn().mockResolvedValue({ detail: 'boom' }) });

    await CreateScriptletCommand.execute(mockApiClient, outputChannel, managerTreeProvider);

    expect(mockShowErrorMessage).toHaveBeenCalledWith('Failed to fetch scriptlets: 500 boom');
    expect(mockShowInputBox).not.toHaveBeenCalled();
  });
});
