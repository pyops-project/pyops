import { ScriptletService } from '../../src/services/ScriptletService';
import { ApiClient } from '../../src/apiClient';
import { ManagerTreeDataProvider, ScriptletTreeItem } from '../../src/managerTreeProvider';
import { createMockVscodeWindow, createMockApiClient, createMockTreeProvider } from '../utils/helpers';

jest.mock('vscode');
jest.mock('../../src/utils');
jest.mock('../../src/apiClient', () => ({ ApiClient: jest.fn() }));

jest.mock('fs', () => require('../utils/helpers').fsMockFactory());
jest.mock('path', () => require('../utils/helpers').pathMockFactory());



describe('ScriptletService', () => {
  let mockApiClient: jest.Mocked<ApiClient>;
  let mockManagerTreeProvider: jest.Mocked<ManagerTreeDataProvider>;
  let mockRemoteProvider: any;
  let mockVscode: any;
  let service: ScriptletService;

  beforeEach(() => {
    jest.clearAllMocks();
    mockVscode = require('vscode');
    const mockFs = require('fs');
    const mockPath = require('path');
    const mockUtils = require('../../src/utils');

    mockVscode.workspace.workspaceFolders = [{ uri: { fsPath: '/workspace' } }];
    require('../utils/helpers').setupConfigMock({localPyopsDir: 'pyops'});
    createMockVscodeWindow();
    mockVscode.commands.executeCommand = jest.fn();
    mockVscode.workspace.openTextDocument = jest.fn().mockResolvedValue({});
    mockVscode.window.showTextDocument = jest.fn();
    mockVscode.Uri = {
      parse: jest.fn().mockReturnValue({}),
      file: jest.fn().mockReturnValue({}),
    };

    mockUtils.inferExt.mockReturnValue('py');

    mockPath.join.mockImplementation((...args: string[]) => args.join('/'));
    mockPath.dirname.mockImplementation((path: string) => path.substring(0, path.lastIndexOf('/')));

    mockApiClient = createMockApiClient(ApiClient, { headers: { 'Authorization': 'Basic test' }, serverUrl: 'http://localhost:8000' }) as any;

    mockManagerTreeProvider = createMockTreeProvider('manager');

    mockRemoteProvider = { setContent: jest.fn() } as any;

    service = new ScriptletService(mockApiClient, mockManagerTreeProvider, mockRemoteProvider);
  });

  describe('resetLocalFromRemote', () => {
    it('resets local from remote successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'test code' }),
      } as Response);

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);
      mockFs.mkdirSync = jest.fn();
      mockFs.writeFileSync = jest.fn();

      await service.resetLocalFromRemote(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/inventory/code?set=testSet&name=testName');
      expect(mockFs.mkdirSync).toHaveBeenCalledWith('/workspace/pyops/testSet/task', { recursive: true });
      expect(mockFs.writeFileSync).toHaveBeenCalledWith('/workspace/pyops/testSet/task/testName.py', 'test code');
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('handles fetch failure', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to fetch remote: Not Found');
    });
  });

  describe('writeLocalToRemoteDraft', () => {
    it('writes local to remote draft successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true } as Response)  // check exists
        .mockResolvedValueOnce({ ok: true } as Response); // draft

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledTimes(2);
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(1, expect.stringContaining('/inventory/code?set=testSet&name=testName'));
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(2, '/scriptlet/inventory/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set: 'testSet', name: 'testName', code: 'test code' }),
      });
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('auto-creates remote if 404 then writes draft', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({
          status: 404,
          ok: false,
        } as Response) // check /code 404
        .mockResolvedValueOnce({
          ok: true,
        } as Response) // create PUT
        .mockResolvedValueOnce({
          ok: true,
        } as Response); // draft POST

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledTimes(3);
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(1, expect.stringContaining('/inventory/code?set=testSet&name=testName'));
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(2, '/scriptlet/inventory/item', expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
        body: expect.stringContaining('"type":"task"'),
      }));
      expect(mockApiClient.fetch).toHaveBeenNthCalledWith(3, '/scriptlet/inventory/draft', expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ set: 'testSet', name: 'testName', code: 'test code' }),
      }));
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Auto-created remote testName; writing draft.');
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('shows warning if no valid config', async () => {
      mockApiClient.hasValidConfig.mockReturnValue(false);

      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
    });
  });


  describe('diffLocalRemote', () => {
    it('diffs local and remote successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'remote code' }),
      } as Response);

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.commands.executeCommand).toHaveBeenCalledWith('vscode.diff', expect.any(Object), expect.any(Object), 'Remote vs Local');
    });
  });

  describe('deleteScriptlet', () => {
    it('deletes scriptlet after confirmation', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.unlinkSync = jest.fn();

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(''),
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/inventory/item?set=testSet&name=testName', {
        method: 'DELETE',
      });
      expect(mockFs.unlinkSync).toHaveBeenCalled();
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('does not delete if not confirmed', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue(undefined);

      await service.deleteScriptlet(treeItem);

      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });
  });

  describe('resetLocalFromRemote', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error resetting local: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to fetch remote: Not Found');
    });
  });

  describe('writeLocalToRemoteDraft', () => {
    it('handles network error on draft fetch', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true } as Response)  // check exists
        .mockRejectedValueOnce(new Error('Network error')); // draft fetch

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error writing draft: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true } as Response)  // check exists
        .mockResolvedValueOnce({
          ok: false,
          statusText: 'Server Error',
        } as Response); // draft non-ok

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to write draft: Server Error');
    });
  });

  describe('diffLocalRemote', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error in diff: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to fetch remote for diff: Not Found');
    });
  });

  describe('deleteScriptlet', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error deleting scriptlet: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: false,
          text: () => Promise.resolve('Delete failed'),
        } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to delete remote: Delete failed');
    });
  });

  describe('openLocalFile', () => {
    it('opens local file successfully', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      await service.openLocalFile(treeItem);

      expect(mockVscode.workspace.openTextDocument).toHaveBeenCalled();
      expect(mockVscode.window.showTextDocument).toHaveBeenCalled();
    });

    it('shows warning if file not found', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);

      await service.openLocalFile(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('Local file not found: /workspace/pyops/testSet/task/testName.py. Run refresh to download.');
    });

    it('does nothing for invalid treeItem', async () => {
      await service.openLocalFile(undefined);
      expect(mockVscode.workspace.openTextDocument).not.toHaveBeenCalled();
    });

    it('shows warning when no workspace folder open', async () => {
      mockVscode.workspace.workspaceFolders = undefined;
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.openLocalFile(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No workspace folder open');
    });
  });

  describe('resetLocalFromRemote edge cases', () => {
    it('handles response code missing', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);
      mockFs.mkdirSync = jest.fn();
      mockFs.writeFileSync = jest.fn();

      await service.resetLocalFromRemote(treeItem);

      expect(mockFs.writeFileSync).toHaveBeenCalledWith('/workspace/pyops/testSet/task/testName.py', '');
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('does nothing for invalid treeItem', async () => {
      await service.resetLocalFromRemote(undefined);
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('shows warning when no workspace folder open', async () => {
      mockVscode.workspace.workspaceFolders = undefined;
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'test code' }),
      } as Response);

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No workspace folder open');
    });
  });

  describe('writeLocalToRemoteDraft edge cases', () => {
    it('handles local file read failure', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockImplementation(() => {
        throw new Error('File read error');
      });

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error reading local file');
    });

    it('does nothing for invalid treeItem', async () => {
      await service.writeLocalToRemoteDraft(undefined);
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('shows warning when no workspace folder open', async () => {
      mockVscode.workspace.workspaceFolders = undefined;
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No workspace folder open');
    });

    it('shows error when local file not found', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Local file not found');
    });

    it('handles network error checking remote existence', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Connection refused'));

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Network error checking remote existence: Connection refused');
    });

    it('handles non-ok non-404 check response', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      } as Response);

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to check remote existence: Internal Server Error');
    });

    it('handles auto-create failure', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ status: 404, ok: false } as Response) // check 404
        .mockResolvedValueOnce({ ok: false, statusText: 'Conflict' } as Response); // create fail

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to auto-create remote scriptlet testName: Conflict');
    });

    it('handles auto-create network error', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;
      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ status: 404, ok: false } as Response)
        .mockRejectedValueOnce(new Error('Network error'));

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Network error auto-creating remote scriptlet testName: Network error');
    });
  });

  describe('diffLocalRemote edge cases', () => {
    it('shows warning when local file is missing', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'remote code' }),
      } as Response);

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('Local file not found; create first');
    });

    it('shows warning for invalid config', async () => {
      mockApiClient.hasValidConfig.mockReturnValue(false);
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
    });

    it('does nothing for invalid treeItem', async () => {
      await service.diffLocalRemote(undefined);
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('shows warning when no workspace folder open', async () => {
      mockVscode.workspace.workspaceFolders = undefined;
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'remote code' }),
      } as Response);

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No workspace folder open');
    });
  });

  describe('deleteScriptlet edge cases', () => {
    it('handles local file unlink failure gracefully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.unlinkSync.mockImplementation(() => {
        throw new Error('Unlink failed');
      });

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(''),
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted remote and local testSet::testName (task)');
    });

    it('shows warning for invalid config', async () => {
      mockApiClient.hasValidConfig.mockReturnValue(false);
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
    });

    it('does nothing for invalid treeItem', async () => {
      await service.deleteScriptlet(undefined);
      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('shows warning when no workspace folder open', async () => {
      mockVscode.workspace.workspaceFolders = undefined;
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No workspace folder open');
    });

    it('handles 404 response (local-only delete)', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.unlinkSync = jest.fn();

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 404,
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockFs.unlinkSync).toHaveBeenCalled();
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted local testSet::testName (task)');
    });

    it('handles 404 with unlink failure', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.unlinkSync.mockImplementation(() => { throw new Error('Permission denied'); });

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 404,
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted local testSet::testName (task)');
    });
  });
});
