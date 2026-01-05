import { ScriptletService } from '../../src/services/ScriptletService';
import { ApiClient } from '../../src/apiClient';
import { ManagerTreeDataProvider, ScriptletTreeItem } from '../../src/managerTreeProvider';

jest.mock('vscode');
jest.mock('../../src/apiClient');
jest.mock('../../src/managerTreeProvider');
jest.mock('fs');
jest.mock('path');
jest.mock('../../src/utils');

global.fetch = jest.fn();

const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();

describe('ScriptletService', () => {
  let mockApiClient: jest.Mocked<ApiClient>;
  let mockManagerTreeProvider: jest.Mocked<ManagerTreeDataProvider>;
  let mockVscode: any;
  let service: ScriptletService;

  beforeEach(() => {
    jest.clearAllMocks();
    mockVscode = require('vscode');
    const mockFs = require('fs');
    const mockPath = require('path');
    const mockUtils = require('../../src/utils');

    mockVscode.workspace.workspaceFolders = [{ uri: { fsPath: '/workspace' } }];
    mockVscode.workspace.getConfiguration = jest.fn().mockReturnValue({
      get: jest.fn((key) => {
        if (key === 'localPyopsDir') return 'pyops';
        return undefined;
      }),
    });
    mockVscode.window.showWarningMessage = jest.fn();
    mockVscode.window.showInformationMessage = jest.fn();
    mockVscode.window.showErrorMessage = jest.fn();
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

    mockApiClient = new ApiClient({} as any) as jest.Mocked<ApiClient>;
    mockApiClient.getHeaders.mockResolvedValue({ 'Authorization': 'Basic test' });
    mockApiClient.getServerUrl.mockReturnValue('http://localhost:8000');
    (ApiClient as jest.Mocked<typeof ApiClient>).hasValidConfigStatic = jest.fn().mockReturnValue(true);

    const mockApiClientForProvider = {} as any;
    mockManagerTreeProvider = new ManagerTreeDataProvider({} as any, {} as any, mockApiClientForProvider) as jest.Mocked<ManagerTreeDataProvider>;
    mockManagerTreeProvider.refresh = jest.fn();

    service = new ScriptletService(mockApiClient, mockManagerTreeProvider);
  });

  describe('resetLocalFromRemote', () => {
    it('resets local from remote successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'test code' }),
      } as Response);

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);
      mockFs.mkdirSync = jest.fn();
      mockFs.writeFileSync = jest.fn();

      await service.resetLocalFromRemote(treeItem);

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/inventory/code?set=testSet&name=testName', {
        headers: { 'Authorization': 'Basic test' },
      });
      expect(mockFs.mkdirSync).toHaveBeenCalledWith('/workspace/pyops/testSet/task', { recursive: true });
      expect(mockFs.writeFileSync).toHaveBeenCalledWith('/workspace/pyops/testSet/task/testName.py', 'test code');
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('handles fetch failure', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
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

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
      } as Response);

      await service.writeLocalToRemoteDraft(treeItem);

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/inventory/draft', {
        method: 'POST',
        headers: { 'Authorization': 'Basic test', 'Content-Type': 'application/json' },
        body: JSON.stringify({ set: 'testSet', name: 'testName', code: 'test code' }),
      });
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('shows warning if no valid config', async () => {
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(false);

      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
    });
  });

  describe('saveRemoteDraft', () => {
    it('saves remote draft successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ has_unstaged: true }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ code: 'draft code' }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
        } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(global.fetch).toHaveBeenCalledTimes(3);
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('does nothing if no unstaged changes', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ has_unstaged: false }),
      } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('No remote draft to save for testName; no unstaged changes.');
    });
  });

  describe('diffLocalRemote', () => {
    it('diffs local and remote successfully', async () => {
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
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

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(''),
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/inventory/item?set=testSet&name=testName', {
        method: 'DELETE',
        headers: { 'Authorization': 'Basic test' },
      });
      expect(mockFs.unlinkSync).toHaveBeenCalled();
      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
    });

    it('does not delete if not confirmed', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue(undefined);

      await service.deleteScriptlet(treeItem);

      expect(global.fetch).not.toHaveBeenCalled();
    });
  });

  describe('resetLocalFromRemote', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error resetting local: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to fetch remote: Not Found');
    });
  });

  describe('writeLocalToRemoteDraft', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error writing draft: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockReturnValue('test code');

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: false,
        statusText: 'Server Error',
      } as Response);

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to write draft: Server Error');
    });
  });

  describe('saveRemoteDraft', () => {
    it('handles status check network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error saving draft: Network error');
    });

    it('handles status check non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: false,
      } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to check draft status');
    });

    it('handles code fetch network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ has_unstaged: true }),
        } as Response)
        .mockRejectedValueOnce(new Error('Network error'));

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error saving draft: Network error');
    });

    it('handles code fetch non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ has_unstaged: true }),
        } as Response)
        .mockResolvedValueOnce({
          ok: false,
        } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('No remote draft to save');
    });

    it('handles save network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ has_unstaged: true }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ code: 'draft code' }),
        } as Response)
        .mockRejectedValueOnce(new Error('Network error'));

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error saving draft: Network error');
    });

    it('handles save non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ has_unstaged: true }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ code: 'draft code' }),
        } as Response)
        .mockResolvedValueOnce({
          ok: false,
          statusText: 'Conflict',
        } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to save: Conflict');
    });
  });

  describe('diffLocalRemote', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error in diff: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
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

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error deleting scriptlet: Network error');
    });

    it('handles non-ok responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);

      (global.fetch as jest.MockedFunction<typeof fetch>)
        .mockResolvedValueOnce({
          ok: false,
          text: () => Promise.resolve('Delete failed'),
        } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to delete: Delete failed');
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

    it('handles resetLocalFromRemote when response code is missing', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
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

    it('handles resetLocalFromRemote network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockRejectedValueOnce(new Error('Network error'));

      await service.resetLocalFromRemote(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error resetting local: Network error');
    });

    it('handles writeLocalToRemoteDraft when local file read fails', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.readFileSync.mockImplementation(() => {
        throw new Error('File read error');
      });

      await service.writeLocalToRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error reading local file');
    });

    it('handles saveRemoteDraft when status check returns invalid JSON', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.reject(new Error('Invalid JSON')),
      } as Response);

      await service.saveRemoteDraft(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error saving draft: Invalid JSON');
    });

    it('handles diffLocalRemote when local file is missing', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(false);

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ code: 'remote code' }),
      } as Response);

      await service.diffLocalRemote(treeItem);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('Local file not found; create first');
    });

    it('handles deleteScriptlet when local file unlink fails', async () => {
      const mockVscode = require('vscode');
      const treeItem = { set: 'testSet', scriptletName: 'testName', scriptletType: 'task' } as ScriptletTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');

      const mockFs = require('fs');
      mockFs.existsSync.mockReturnValue(true);
      mockFs.unlinkSync.mockImplementation(() => {
        throw new Error('Unlink failed');
      });

      (global.fetch as jest.MockedFunction<typeof fetch>).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(''),
      } as Response);

      await service.deleteScriptlet(treeItem);

      expect(mockManagerTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted testSet::testName (task) locally and remotely');
    });
  });
});
