import { RefreshTimerManager } from '../../src/services/RefreshTimerManager';
import { ManagerTreeDataProvider } from '../../src/managerTreeProvider';
import { JobsTreeDataProvider } from '../../src/jobsTreeProvider';
import { ApiClient } from '../../src/apiClient';

jest.mock('vscode');
jest.mock('../../src/managerTreeProvider');
jest.mock('../../src/jobsTreeProvider');
jest.mock('../../src/utils');
jest.mock('../../src/apiClient');

describe('RefreshTimerManager', () => {
  let mockManagerProvider: jest.Mocked<ManagerTreeDataProvider>;
  let mockJobsProvider: jest.Mocked<JobsTreeDataProvider>;
  let manager: RefreshTimerManager;

  beforeEach(() => {
    jest.clearAllMocks();
    const mockVscode = require('vscode');

    mockVscode.workspace.getConfiguration = jest.fn();

    jest.spyOn(global, 'setInterval');
    jest.spyOn(global, 'clearInterval');

    (ApiClient as jest.Mocked<typeof ApiClient>).hasValidConfigStatic = jest.fn().mockReturnValue(true);

    const mockApiClient = {} as any;
    mockManagerProvider = new ManagerTreeDataProvider({} as any, {} as any, mockApiClient) as jest.Mocked<ManagerTreeDataProvider>;
    mockJobsProvider = new JobsTreeDataProvider({} as any, mockApiClient) as jest.Mocked<JobsTreeDataProvider>;

    manager = new RefreshTimerManager(mockManagerProvider, mockJobsProvider);
  });

  afterEach(() => {
    manager.dispose();
    jest.restoreAllMocks();
  });

  describe('ensureRunning', () => {
    it('starts timers when config is valid and no timers running', () => {
      const mockVscode = require('vscode');
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(15),
      });

      manager.ensureRunning();

      expect(ApiClient.hasValidConfigStatic).toHaveBeenCalled();
      expect(mockVscode.workspace.getConfiguration).toHaveBeenCalledWith('pyops');
      expect(setInterval).toHaveBeenCalledTimes(2);
      expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 15000);
    });

    it('does not start timers when config is invalid', () => {
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(false);

      manager.ensureRunning();

      expect(ApiClient.hasValidConfigStatic).toHaveBeenCalled();
      expect(setInterval).not.toHaveBeenCalled();
    });

    it('does not start duplicate timers if already running', () => {
      const mockVscode = require('vscode');
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(10),
      });

      manager.ensureRunning();
      manager.ensureRunning();

      expect(setInterval).toHaveBeenCalledTimes(2); // Only once per type
    });
  });

  describe('stopIfRunning', () => {
    it('clears timers when running', () => {
      const mockVscode = require('vscode');
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(10),
      });

      manager.ensureRunning();
      manager.stopIfRunning();

      expect(clearInterval).toHaveBeenCalledTimes(2);
    });

    it('does nothing if no timers running', () => {
      manager.stopIfRunning();

      expect(clearInterval).not.toHaveBeenCalled();
    });
  });

  describe('dispose', () => {
    it('clears all timers', () => {
      const mockVscode = require('vscode');
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(10),
      });

      manager.ensureRunning();
      manager.dispose();

      expect(clearInterval).toHaveBeenCalledTimes(2);
    });
  });
});
