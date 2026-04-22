import { RefreshTimerManager } from '../../src/services/RefreshTimerManager';
import { ManagerTreeDataProvider } from '../../src/managerTreeProvider';
import { JobsTreeDataProvider } from '../../src/jobsTreeProvider';
import { ApiClient } from '../../src/apiClient';
import { createMockApiClient, createMockTreeProvider } from '../utils/helpers';

jest.mock('vscode');
jest.mock('../../src/utils');
jest.mock('../../src/apiClient', () => ({ ApiClient: jest.fn() }));

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

    const mockApiClient = createMockApiClient(ApiClient);
    mockManagerProvider = createMockTreeProvider('manager') as jest.Mocked<ManagerTreeDataProvider>;
    mockJobsProvider = createMockTreeProvider('jobs') as jest.Mocked<JobsTreeDataProvider>;

    manager = new RefreshTimerManager(mockManagerProvider, mockJobsProvider);
  });

  afterEach(() => {
    manager.dispose();
    jest.restoreAllMocks();
  });

  describe('ensureRunning', () => {
    it('starts timers when config is valid and no timers running', () => {
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 15});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();

      expect(ApiClient.hasValidConfigStatic).toHaveBeenCalled();
      expect(require('vscode').workspace.getConfiguration).toHaveBeenCalledWith('pyops');
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
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 10});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();
      manager.ensureRunning();

      expect(setInterval).toHaveBeenCalledTimes(2); // Only once per type
    });
  });

  describe('stopIfRunning', () => {
    it('clears timers when running', () => {
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 10});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();
      manager.stopIfRunning();

      expect(clearInterval).toHaveBeenCalledTimes(2);
    });

    it('does nothing if no timers running', () => {
      manager.stopIfRunning();

      expect(clearInterval).not.toHaveBeenCalled();
    });
  });

  describe('timer callbacks', () => {
    it('calls managerTreeProvider.refresh on manager timer tick', () => {
      jest.useFakeTimers();
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 10});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();

      jest.advanceTimersByTime(10000);

      expect(mockManagerProvider.refresh).toHaveBeenCalled();
      jest.useRealTimers();
    });

    it('calls jobsTreeProvider.refresh on monitor timer tick', () => {
      jest.useFakeTimers();
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 10});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();

      jest.advanceTimersByTime(10000);

      expect(mockJobsProvider.refresh).toHaveBeenCalled();
      jest.useRealTimers();
    });
  });

  describe('dispose', () => {
    it('clears all timers', () => {
      require('../utils/helpers').setupConfigMock({autoRefreshInterval: 10});
      (ApiClient.hasValidConfigStatic as jest.MockedFunction<typeof ApiClient.hasValidConfigStatic>).mockReturnValue(true);

      manager.ensureRunning();
      manager.dispose();

      expect(clearInterval).toHaveBeenCalledTimes(2);
    });
  });
});
