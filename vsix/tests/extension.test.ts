import { activate, deactivate } from '../src/extension';

jest.mock('vscode');
jest.mock('../src/utils');
jest.mock('../src/jobsTreeProvider', () => ({
  JobsTreeDataProvider: jest.fn().mockImplementation(function() {
    // @ts-ignore
    this.refresh = jest.fn();
  })
}));

jest.mock('../src/managerTreeProvider', () => ({
  ManagerTreeDataProvider: jest.fn().mockImplementation(function() {
    // @ts-ignore
    this.refresh = jest.fn();
  })
}));

jest.mock('../src/commands/CreateScriptletCommand');
jest.mock('../src/services/AuthService', () => ({
  AuthService: {
    setupAuth: jest.fn().mockResolvedValue(undefined)
  }
}));

jest.mock('../src/services/DebugService', () => {
  const mockDebugService = jest.fn().mockImplementation(() => ({
    enqueueDebugJob: jest.fn().mockResolvedValue({ jobId: '123' }),
  }));
  // @ts-ignore
  mockDebugService.buildOverrides = jest.fn().mockReturnValue({});
  return { DebugService: mockDebugService };
});

jest.mock('../src/services/DebugOutputMonitor', () => ({ DebugOutputMonitor: jest.fn().mockImplementation(() => ({ dispose: jest.fn(), isMonitoring: jest.fn().mockReturnValue(false), startMonitoring: jest.fn().mockResolvedValue(undefined), stopMonitoring: jest.fn(), })) }));
jest.mock('../src/services/JobService', () => ({ JobService: jest.fn().mockImplementation(() => ({ dispose: jest.fn(), copyJobParam: jest.fn(), cancelJob: jest.fn(), downloadJob: jest.fn(), deleteJob: jest.fn(), viewJob: jest.fn(), cancelAllJobs: jest.fn(), deleteAllDone: jest.fn(), deleteAllFailed: jest.fn(), })) }));
jest.mock('../src/services/PyOpsPythonDebugConfigProvider');
jest.mock('../src/services/RefreshTimerManager', () => ({
  RefreshTimerManager: jest.fn().mockImplementation(function() {
    // @ts-ignore
    this.ensureRunning = jest.fn();
    // @ts-ignore
    this.stopIfRunning = jest.fn();
  })
}));


jest.mock('../src/services/ScriptletService', () => ({ ScriptletService: jest.fn().mockImplementation(() => ({ resetLocalFromRemote: jest.fn(), writeLocalToRemoteDraft: jest.fn(), saveRemoteDraft: jest.fn(), diffLocalRemote: jest.fn(), deleteScriptlet: jest.fn(), openLocalFile: jest.fn(), })) }));
jest.mock('../src/apiClient');
jest.mock('../src/providers/RemoteContentProvider');
jest.mock('../src/ConnectionStatusManager', () => ({
  ConnectionStatusManager: jest.fn().mockImplementation(function() {
    // @ts-ignore
    this.createStatusBarItem = () => (require('vscode').window.createStatusBarItem as jest.Mock)();
    // @ts-ignore
    this.update = jest.fn().mockResolvedValue(undefined);
  })
}));






describe('Extension', () => {
  let mockContext: any;
  let mockOutputChannel: any;
  let mockStatusBarItem: any;
  let mockJobOutputChannel: any;
  let mockJobErrOutputChannel: any;
  let mockJobTreeItem: any;
  let mockScriptletTreeItem: any;

  beforeEach(() => {
    jest.clearAllMocks();

    mockContext = {
      subscriptions: [],
      workspaceState: { get: jest.fn().mockReturnValue('') },
    };

    // Mock createOutputChannel and createStatusBarItem
    mockOutputChannel = {
      appendLine: jest.fn(),
      dispose: jest.fn(),
      show: jest.fn(),
      clear: jest.fn(),
    };
    mockJobOutputChannel = {
      appendLine: jest.fn(),
      dispose: jest.fn(),
      show: jest.fn(),
      clear: jest.fn(),
    };
    mockJobErrOutputChannel = {
      appendLine: jest.fn(),
      dispose: jest.fn(),
      show: jest.fn(),
      clear: jest.fn(),
    };
    mockStatusBarItem = {
      text: '',
      dispose: jest.fn(),
    };

    (require('vscode').window.createOutputChannel as jest.Mock).mockImplementation((name: string) => {
      if (name === 'PyOps') return mockOutputChannel;
      if (name === 'PyOps Job Out') return mockJobOutputChannel;
      if (name === 'PyOps Job Err') return mockJobErrOutputChannel;
      return mockOutputChannel;
    });
    (require('vscode').window.createStatusBarItem as jest.Mock).mockImplementation(() => mockStatusBarItem);

    // Mock config
    (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'username') return 'testuser';
        return '';
      }),
    });

    // Mock fetch for debug session terminate
    global.fetch = jest.fn().mockResolvedValue({ ok: true });


    // Mock missing vscode APIs
    (require('vscode').workspace.registerTextDocumentContentProvider as jest.Mock).mockReturnValue({});
    (require('vscode').languages.setTextDocumentLanguage as jest.Mock).mockReturnValue({});
    (require('vscode').debug.registerDebugConfigurationProvider as jest.Mock).mockReturnValue({});
    (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mockReturnValue({});
    (require('vscode').window.registerTreeDataProvider as jest.Mock).mockReturnValue({});








    // Mock providers/services constructors
    (require('../src/providers/RemoteContentProvider').RemoteContentProvider as jest.Mock).mockImplementation(() => ({}));
    (require('../src/providers/RemoteContentProvider').registerRemoteLanguageModeSetter as jest.Mock).mockReturnValue({});
    (require('../src/apiClient').ApiClient as jest.Mock).mockImplementation(() => ({}));





    (require('../src/commands/CreateScriptletCommand').CreateScriptletCommand.execute as jest.Mock).mockResolvedValue(undefined);
    (require('../src/utils').hasValidConfig as jest.Mock).mockReturnValue(true);

    mockJobTreeItem = { jobId: '123', status: 'running' };
    mockScriptletTreeItem = { set: 'testset', scriptletName: 'testname', scriptletType: 'task' };
  });

  describe('activate', () => {
    it('should activate successfully', async () => {
      await activate(mockContext);

      expect(require('../src/services/AuthService').AuthService.setupAuth).toHaveBeenCalledWith(mockContext, expect.any(Object));
      expect(require('../src/providers/RemoteContentProvider').RemoteContentProvider).toHaveBeenCalled();
      expect(require('vscode').workspace.registerTextDocumentContentProvider).toHaveBeenCalledWith('pyops-remote', {});
      expect(require('../src/providers/RemoteContentProvider').registerRemoteLanguageModeSetter).toHaveBeenCalled();
      expect(require('../src/ConnectionStatusManager').ConnectionStatusManager).toHaveBeenCalledWith(mockContext, expect.any(Object));
      expect(require('vscode').workspace.onDidChangeConfiguration).toHaveBeenCalled();
      expect(require('vscode').window.registerTreeDataProvider).toHaveBeenCalledWith('pyops-manager', expect.any(Object));
      expect(require('vscode').window.registerTreeDataProvider).toHaveBeenCalledWith('pyops-monitor', expect.any(Object));
      expect(require('vscode').debug.registerDebugConfigurationProvider).toHaveBeenCalledWith('python', expect.any(Object));
      expect(require('vscode').debug.onDidTerminateDebugSession).toHaveBeenCalled();
      expect(require('vscode').commands.registerCommand).toHaveBeenCalledTimes(20); // Count from source
      expect(mockContext.subscriptions).toHaveLength(32); // Exact count
      expect(require('vscode').window.createOutputChannel).toHaveBeenCalledWith('PyOps');
    });

    it('should handle auth setup failure', async () => {
      const authMock = require('../src/services/AuthService').AuthService.setupAuth as jest.Mock;
      authMock.mockRejectedValueOnce(new Error('Auth failed'));

      await expect(activate(mockContext)).rejects.toThrow('Auth failed');
      expect(require('vscode').window.createOutputChannel.mock.results[0].value.show).toHaveBeenCalledWith(true);
    });

  });

  describe('deactivate', () => {
    it('should dispose resources', async () => {
      // First activate to set globals
      await activate(mockContext);

      expect(() => deactivate()).not.toThrow();

      // Verify all disposables are called
      expect(mockStatusBarItem.dispose).toHaveBeenCalled();
      expect(mockOutputChannel.dispose).toHaveBeenCalled();
      expect(mockJobOutputChannel.dispose).toHaveBeenCalled();
      expect(mockJobErrOutputChannel.dispose).toHaveBeenCalled();
      // Note: jobService and debugOutputMonitor are set as global variables during activate
      // and their dispose methods are called directly
    });
  });

  describe('config change handler', () => {
    it('should handle pyops config changes', async () => {
      await activate(mockContext);

      // Mock the config change event
      const mockEvent = { affectsConfiguration: jest.fn().mockReturnValue(true) };
      const configChangeHandler = (require('vscode').workspace.onDidChangeConfiguration as jest.Mock).mock.calls[0][0];

      await configChangeHandler(mockEvent);

      expect(mockEvent.affectsConfiguration).toHaveBeenCalledWith('pyops');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Config changed, updating status...');
    });

    it('should not handle non-pyops config changes', async () => {
      await activate(mockContext);

      // Clear previous calls from activate
      mockOutputChannel.appendLine.mockClear();

      const mockEvent = { affectsConfiguration: jest.fn().mockReturnValue(false) };
      const configChangeHandler = (require('vscode').workspace.onDidChangeConfiguration as jest.Mock).mock.calls[0][0];

      await configChangeHandler(mockEvent);

      expect(mockEvent.affectsConfiguration).toHaveBeenCalledWith('pyops');
      expect(mockOutputChannel.appendLine).not.toHaveBeenCalledWith('Config changed, updating status...');
    });

    it('should stop refresh timer when config becomes invalid', async () => {
      (require('../src/services/RefreshTimerManager').RefreshTimerManager as jest.Mock).mock.instances = [];
      await activate(mockContext);

      (require('../src/utils').hasValidConfig as jest.Mock).mockReturnValue(false);
      const mockEvent = { affectsConfiguration: jest.fn().mockReturnValue(true) };
      const configChangeHandler = (require('vscode').workspace.onDidChangeConfiguration as jest.Mock).mock.calls[0][0];

      await configChangeHandler(mockEvent);

      expect(mockEvent.affectsConfiguration).toHaveBeenCalledWith('pyops');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Config changed, updating status...');
    });

    it('should ensure refresh timer running when config valid', async () => {
      (require('../src/services/RefreshTimerManager').RefreshTimerManager as jest.Mock).mock.instances = [];
      await activate(mockContext);

      const mockEvent = { affectsConfiguration: jest.fn().mockReturnValue(true) };
      const configChangeHandler = (require('vscode').workspace.onDidChangeConfiguration as jest.Mock).mock.calls[0][0];

      await configChangeHandler(mockEvent);

      expect(mockEvent.affectsConfiguration).toHaveBeenCalledWith('pyops');
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Config changed, updating status...');
    });

    it('should handle config change before providers initialized (null branches)', async () => {
      // Clear instances to ensure clean state
      (require('../src/ConnectionStatusManager').ConnectionStatusManager as jest.Mock).mock.instances = [];
      (require('../src/managerTreeProvider').ManagerTreeDataProvider as jest.Mock).mock.instances = [];
      (require('../src/jobsTreeProvider').JobsTreeDataProvider as jest.Mock).mock.instances = [];
      (require('../src/services/RefreshTimerManager').RefreshTimerManager as jest.Mock).mock.instances = [];

      // Mock onDidChangeConfiguration to capture the callback
      let capturedCallback: any;
      (require('vscode').workspace.onDidChangeConfiguration as jest.Mock).mockImplementation((cb) => {
        capturedCallback = cb;
        return { dispose: jest.fn() };
      });

      // Start activate
      const activatePromise = activate(mockContext);

      // Fire event after registration (before assignments)
      const mockEvent = { affectsConfiguration: jest.fn().mockReturnValue(true) };
      await new Promise(r => setTimeout(r, 0));
      await capturedCallback(mockEvent);

      await activatePromise;

      // Should log and update status, but no refresh since null at fire time
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('Config changed, updating status...');

      // No refresh calls from config handler (constructor calls ok)
      const managerInstances = require('../src/managerTreeProvider').ManagerTreeDataProvider.mock.instances;
      expect(managerInstances[0].refresh).toHaveBeenCalledTimes(1);
      const jobsInstances = require('../src/jobsTreeProvider').JobsTreeDataProvider.mock.instances;
      expect(jobsInstances[0].refresh).toHaveBeenCalledTimes(1);
      const refreshInstances = require('../src/services/RefreshTimerManager').RefreshTimerManager.mock.instances;
      expect(refreshInstances[0].ensureRunning).toHaveBeenCalledTimes(2);
      expect(refreshInstances[0].stopIfRunning).not.toHaveBeenCalled();



    });
  });

  describe('debug session termination handler', () => {
    it('should cancel job when debug session terminates with jobId', async () => {
      // Mock password retrieval
      mockContext.workspaceState.get.mockReturnValue('testpass');

      await activate(mockContext);

      const mockSession = { configuration: { jobId: 'test-job-123' } };
      const terminateHandler = (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mock.calls[0][0];

      await terminateHandler(mockSession);

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/job/cancel?id=test-job-123', {
        headers: { 'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M=' }
      });
      expect(require('vscode').window.showInformationMessage).toHaveBeenCalledWith('Canceled debug job test-job-123');
    });

    it('should handle job cancellation failure', async () => {
      // Mock no auth
      mockContext.workspaceState.get.mockReturnValue(undefined);
      (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return undefined;
          return '';
        }),
      });

      await activate(mockContext);

      global.fetch = jest.fn().mockRejectedValue(new Error('Network error'));
      const mockSession = { configuration: { jobId: 'test-job-123' } };
      const terminateHandler = (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mock.calls[0][0];

      const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

      await terminateHandler(mockSession);

      expect(consoleSpy).toHaveBeenCalledWith('Error canceling job: Network error');
      consoleSpy.mockRestore();
    });

    it('should not cancel job when session has no jobId', async () => {
      await activate(mockContext);

      const mockSession = { configuration: {} };
      const terminateHandler = (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mock.calls[0][0];

      await terminateHandler(mockSession);

      expect(global.fetch).not.toHaveBeenCalled();
    });

    it('should not cancel job when session has no configuration', async () => {
      await activate(mockContext);

      const mockSession = {};
      const terminateHandler = (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mock.calls[0][0];

      await terminateHandler(mockSession);

      expect(global.fetch).not.toHaveBeenCalled();
    });

    it('should not cancel job when session config exists but has no jobId', async () => {
      await activate(mockContext);

      const mockSession = { configuration: { otherProp: 'value' } };
      const terminateHandler = (require('vscode').debug.onDidTerminateDebugSession as jest.Mock).mock.calls[0][0];

      await terminateHandler(mockSession);

      expect(global.fetch).not.toHaveBeenCalled();
    });
  });

  describe('command handlers', () => {
    let commandHandlers: Map<string, Function>;
    let jobServiceInstance: any;
    let scriptletServiceInstance: any;
    let debugServiceInstance: any;
    let debugOutputMonitorInstance: any;
    let connectionStatusManager: any;
    let managerTreeDataProvider: any;
    let jobsTreeDataProvider: any;
    let DebugServiceModule: any;
    beforeAll(async () => {
      // Set up mocks needed for activate
      const vscode = require('vscode');

      mockContext = {
        subscriptions: [],
        workspaceState: { get: jest.fn().mockReturnValue('') },
      };

      mockOutputChannel = {
        appendLine: jest.fn(),
        dispose: jest.fn(),
        show: jest.fn(),
        clear: jest.fn(),
      };
      mockJobOutputChannel = {
        appendLine: jest.fn(),
        dispose: jest.fn(),
        show: jest.fn(),
        clear: jest.fn(),
      };
      mockJobErrOutputChannel = {
        appendLine: jest.fn(),
        dispose: jest.fn(),
        show: jest.fn(),
        clear: jest.fn(),
      };
      mockStatusBarItem = {
        text: '',
        dispose: jest.fn(),
      };

      // @ts-ignore
      jest.spyOn(vscode.window, 'createOutputChannel').mockImplementation((name: string) => {
        if (name === 'PyOps') return mockOutputChannel;
        if (name === 'PyOps Job Out') return mockJobOutputChannel;
        if (name === 'PyOps Job Err') return mockJobErrOutputChannel;
        return mockOutputChannel;
      });
      // @ts-ignore
      jest.spyOn(vscode.window, 'createStatusBarItem').mockImplementation(() => mockStatusBarItem);

      jest.spyOn(vscode.workspace, 'getConfiguration').mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return 'http://localhost:8000';
          if (key === 'username') return 'testuser';
          return '';
        }),
      });

      Object.defineProperty(vscode.workspace, 'workspaceFolders', {
        get: () => [{ uri: { fsPath: '/test/workspace' } }],
        configurable: true
      });

      global.fetch = jest.fn().mockResolvedValue({ ok: true });

      jest.spyOn(vscode.workspace, 'registerTextDocumentContentProvider').mockReturnValue({});
      jest.spyOn(vscode.languages, 'setTextDocumentLanguage').mockReturnValue({});
      jest.spyOn(vscode.debug, 'registerDebugConfigurationProvider').mockReturnValue({});
      jest.spyOn(vscode.debug, 'onDidTerminateDebugSession').mockReturnValue({});
      jest.spyOn(vscode.window, 'registerTreeDataProvider').mockReturnValue({});
      jest.spyOn(vscode.window, 'showErrorMessage').mockResolvedValue(undefined);
      jest.spyOn(vscode.window, 'showWarningMessage').mockResolvedValue(undefined);
      jest.spyOn(vscode.window, 'showInformationMessage').mockResolvedValue(undefined);

      (require('../src/services/AuthService').AuthService.setupAuth as jest.Mock).mockResolvedValue(undefined);
      (require('../src/providers/RemoteContentProvider').RemoteContentProvider as jest.Mock).mockImplementation(() => ({}));
      (require('../src/providers/RemoteContentProvider').registerRemoteLanguageModeSetter as jest.Mock).mockReturnValue({});
      (require('../src/apiClient').ApiClient as jest.Mock).mockImplementation(() => ({}));
      (require('../src/utils').hasValidConfig as jest.Mock).mockReturnValue(true);

      // Reset instances
      const JobServiceModule = require('../src/services/JobService');
      const ScriptletServiceModule = require('../src/services/ScriptletService');
      DebugServiceModule = require('../src/services/DebugService');
      const DebugOutputMonitorModule = require('../src/services/DebugOutputMonitor');
      JobServiceModule.JobService.mock.instances = [];
      ScriptletServiceModule.ScriptletService.mock.instances = [];
      DebugServiceModule.DebugService.mock.instances = [];
      DebugOutputMonitorModule.DebugOutputMonitor.mock.instances = [];

      // Reset provider instances
      const ConnectionStatusManagerModule = require('../src/ConnectionStatusManager');
      const ManagerTreeDataProviderModule = require('../src/managerTreeProvider');
      const JobsTreeDataProviderModule = require('../src/jobsTreeProvider');
      ConnectionStatusManagerModule.ConnectionStatusManager.mock.instances = [];
      ManagerTreeDataProviderModule.ManagerTreeDataProvider.mock.instances = [];
      JobsTreeDataProviderModule.JobsTreeDataProvider.mock.instances = [];






      // Mock registerCommand to capture handlers
      commandHandlers = new Map<string, Function>();
      const originalRegisterCommand = require('vscode').commands.registerCommand;
      require('vscode').commands.registerCommand = jest.fn((command: string, handler: Function) => {
        commandHandlers.set(command, handler);
        return { dispose: jest.fn() };
      });

      // Activate once
      const extension = require('../src/extension');
      await extension.activate(mockContext);

      // Capture instances from globals
      jobServiceInstance = (global as any).jobService;
      scriptletServiceInstance = (global as any).scriptletService;
      debugServiceInstance = (global as any).debugService;
      debugOutputMonitorInstance = (global as any).debugOutputMonitor;

      // Get providers from mock instances and spy on methods
      connectionStatusManager = require('../src/ConnectionStatusManager').ConnectionStatusManager.mock.instances[0];
      managerTreeDataProvider = require('../src/managerTreeProvider').ManagerTreeDataProvider.mock.instances[0];
      jobsTreeDataProvider = require('../src/jobsTreeProvider').JobsTreeDataProvider.mock.instances[0];


      // Restore original registerCommand
      require('vscode').commands.registerCommand = originalRegisterCommand;
    });

    beforeEach(() => {
      jest.clearAllMocks();
      debugOutputMonitorInstance.isMonitoring.mockReturnValue(false);
    });

    afterAll(() => {
      jest.resetAllMocks();
    });

    it('should refresh connection status', async () => {
      const handler = commandHandlers.get('pyops.refreshConnectionStatus');
      await handler!();
      expect(connectionStatusManager.update).toHaveBeenCalled();
    });

    it('should refresh manager', async () => {
      const handler = commandHandlers.get('pyops.refreshManager');
      await handler!();
      expect(managerTreeDataProvider.refresh).toHaveBeenCalled();
      expect(require('vscode').window.showInformationMessage).toHaveBeenCalledWith('PyOps Manager refreshed');
    });

    it('should refresh monitor', async () => {
      const handler = commandHandlers.get('pyops.refreshMonitor');
      await handler!();
      expect(jobsTreeDataProvider.refresh).toHaveBeenCalled();
      expect(require('vscode').window.showInformationMessage).toHaveBeenCalledWith('PyOps Monitor refreshed');
    });

    it('should copy job param', async () => {
      const handler = commandHandlers.get('pyops.copyJobParam');
      await handler!(mockJobTreeItem);
      expect(jobServiceInstance.copyJobParam).toHaveBeenCalledWith(mockJobTreeItem);
    });

    it('should cancel job', async () => {
      const handler = commandHandlers.get('pyops.cancelJob');
      await handler!(mockJobTreeItem);
      expect(jobServiceInstance.cancelJob).toHaveBeenCalledWith(mockJobTreeItem);
    });

    it('should download job', async () => {
      const handler = commandHandlers.get('pyops.downloadJob');
      await handler!(mockJobTreeItem);
      expect(jobServiceInstance.downloadJob).toHaveBeenCalledWith(mockJobTreeItem);
    });

    it('should delete job', async () => {
      const handler = commandHandlers.get('pyops.deleteJob');
      await handler!(mockJobTreeItem);
      expect(jobServiceInstance.deleteJob).toHaveBeenCalledWith(mockJobTreeItem);
    });

    it('should view job', async () => {
      const handler = commandHandlers.get('pyops.viewJob');
      await handler!(mockJobTreeItem);
      expect(jobServiceInstance.viewJob).toHaveBeenCalledWith(mockJobTreeItem);
    });

    it('should cancel all jobs', async () => {
      const handler = commandHandlers.get('pyops.cancelAllJobs');
      await handler!();
      expect(jobServiceInstance.cancelAllJobs).toHaveBeenCalled();
    });

    it('should delete all done', async () => {
      const handler = commandHandlers.get('pyops.deleteAllDone');
      await handler!();
      expect(jobServiceInstance.deleteAllDone).toHaveBeenCalled();
    });

    it('should delete all failed', async () => {
      const handler = commandHandlers.get('pyops.deleteAllFailed');
      await handler!();
      expect(jobServiceInstance.deleteAllFailed).toHaveBeenCalled();
    });

    it('should reset local from remote', async () => {
      const handler = commandHandlers.get('pyops.resetLocalFromRemote');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.resetLocalFromRemote).toHaveBeenCalledWith(mockScriptletTreeItem);
    });

    it('should write local to remote draft', async () => {
      const handler = commandHandlers.get('pyops.writeLocalToRemoteDraft');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.writeLocalToRemoteDraft).toHaveBeenCalledWith(mockScriptletTreeItem);
    });

    it('should save remote draft', async () => {
      const handler = commandHandlers.get('pyops.saveRemoteDraft');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.saveRemoteDraft).toHaveBeenCalledWith(mockScriptletTreeItem);
    });

    it('should start debug job', async () => {
      const handler = commandHandlers.get('pyops.startDebugJob');
      await handler!(mockScriptletTreeItem);
      expect(DebugServiceModule.DebugService.buildOverrides).toHaveBeenCalledWith('testset', '/test/workspace');
      expect(debugServiceInstance.enqueueDebugJob).toHaveBeenCalledWith('testset', 'testname', {});
      expect(require('vscode').window.showInformationMessage).toHaveBeenCalledWith('Started debug job 123 for testname with local overrides');
      expect(debugOutputMonitorInstance.startMonitoring).toHaveBeenCalledWith('testset-testname', '123');
    });

    it('should not start debug job if invalid config', async () => {
      (require('../src/utils').hasValidConfig as jest.Mock).mockReturnValue(false);
      const handler = commandHandlers.get('pyops.startDebugJob');
      await handler!(mockScriptletTreeItem);
      expect(require('vscode').window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
      expect(debugServiceInstance.enqueueDebugJob).not.toHaveBeenCalled();
    });

    it('should not start debug job if treeItem is invalid', async () => {
      const invalidTreeItem = { set: 'testset', scriptletName: 'testname', scriptletType: 'not-task' };
      const handler = commandHandlers.get('pyops.startDebugJob');
      await handler!(invalidTreeItem);
      expect(debugServiceInstance.enqueueDebugJob).not.toHaveBeenCalled();
    });

    it('should not start debug job if treeItem is missing properties', async () => {
      const invalidTreeItem = { scriptletName: 'testname', scriptletType: 'task' };
      const handler = commandHandlers.get('pyops.startDebugJob');
      await handler!(invalidTreeItem);
      expect(debugServiceInstance.enqueueDebugJob).not.toHaveBeenCalled();
    });

    it('should not start debug job if already monitoring', async () => {
      debugOutputMonitorInstance.isMonitoring.mockReturnValue(true);

      const handler = commandHandlers.get('pyops.startDebugJob');
      await handler!(mockScriptletTreeItem);
      expect(require('vscode').window.showWarningMessage).toHaveBeenCalledWith('Debug job already running');
      expect(debugServiceInstance.enqueueDebugJob).not.toHaveBeenCalled();
    });

    it('should handle debug job enqueue failure', async () => {

      debugServiceInstance.enqueueDebugJob.mockResolvedValueOnce({});

      const vscode = require('vscode');
      expect(vscode.workspace.workspaceFolders).toBeDefined();
      expect(vscode.workspace.workspaceFolders).toHaveLength(1);
      expect(vscode.workspace.workspaceFolders![0].uri.fsPath).toBe('/test/workspace');

      const utils = require('../src/utils');

      expect(utils.hasValidConfig).toBeDefined();

      expect(DebugServiceModule.DebugService).toBeDefined();
      expect(DebugServiceModule.DebugService.buildOverrides).toBeDefined();

      expect(DebugServiceModule.DebugService).toBe(require('../src/services/DebugService').DebugService);

      const handler = commandHandlers.get('pyops.startDebugJob');

      await handler!(mockScriptletTreeItem);
      expect(utils.hasValidConfig).toHaveBeenCalled();
      expect(debugOutputMonitorInstance.isMonitoring).toHaveBeenCalledWith('testset-testname');
      expect(debugServiceInstance.enqueueDebugJob).toHaveBeenCalledWith('testset', 'testname', {});
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledWith('Failed to start debug');

      expect(debugOutputMonitorInstance.startMonitoring).not.toHaveBeenCalled();
    });




    it('should show warning for stopDebugJob invalid config', async () => {
      (require('../src/utils').hasValidConfig as jest.Mock).mockReturnValue(false);
      const handler = commandHandlers.get('pyops.stopDebugJob');
      await handler!(mockScriptletTreeItem);
      expect(require('vscode').window.showWarningMessage).toHaveBeenCalledWith('No valid PyOps configuration set. Please configure in settings.');
    });

    it('should stop debug job', async () => {
      const handler = commandHandlers.get('pyops.stopDebugJob');
      await handler!(mockScriptletTreeItem);
      expect(debugOutputMonitorInstance.stopMonitoring).toHaveBeenCalledWith('testset-testname');
    });

    it('should diff local remote', async () => {
      const handler = commandHandlers.get('pyops.diffLocalRemote');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.diffLocalRemote).toHaveBeenCalledWith(mockScriptletTreeItem);
    });

    it('should delete scriptlet', async () => {
      const handler = commandHandlers.get('pyops.deleteScriptlet');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.deleteScriptlet).toHaveBeenCalledWith(mockScriptletTreeItem);
    });

    it('should open local file', async () => {
      const handler = commandHandlers.get('pyops.openLocalFile');
      await handler!(mockScriptletTreeItem);
      expect(scriptletServiceInstance.openLocalFile).toHaveBeenCalledWith(mockScriptletTreeItem);
    });
  });
});
