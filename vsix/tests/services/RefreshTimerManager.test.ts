import { RefreshTimerManager } from '../../src/services/RefreshTimerManager';
import { ManagerTreeDataProvider } from '../../src/managerTreeProvider';
import { JobsTreeDataProvider } from '../../src/jobsTreeProvider';
import { hasValidConfig } from '../../src/utils';

jest.mock('vscode');
jest.mock('../../src/managerTreeProvider');
jest.mock('../../src/jobsTreeProvider');
jest.mock('../../src/utils');

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

    mockManagerProvider = new ManagerTreeDataProvider({} as any, {} as any) as jest.Mocked<ManagerTreeDataProvider>;
    mockJobsProvider = new JobsTreeDataProvider({} as any) as jest.Mocked<JobsTreeDataProvider>;

    manager = new RefreshTimerManager(mockManagerProvider, mockJobsProvider);
  });

  afterEach(() => {
    manager.dispose();
    jest.restoreAllMocks();
  });

  describe('ensureRunning', () => {
    it('starts timers when config is valid and no timers running', () => {
      const mockVscode = require('vscode');
      (hasValidConfig as jest.MockedFunction<typeof hasValidConfig>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(15),
      });

      manager.ensureRunning();

      expect(hasValidConfig).toHaveBeenCalled();
      expect(mockVscode.workspace.getConfiguration).toHaveBeenCalledWith('pyops');
      expect(setInterval).toHaveBeenCalledTimes(2);
      expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 15000);
    });

    it('does not start timers when config is invalid', () => {
      (hasValidConfig as jest.MockedFunction<typeof hasValidConfig>).mockReturnValue(false);

      manager.ensureRunning();

      expect(hasValidConfig).toHaveBeenCalled();
      expect(setInterval).not.toHaveBeenCalled();
    });

    it('does not start duplicate timers if already running', () => {
      const mockVscode = require('vscode');
      (hasValidConfig as jest.MockedFunction<typeof hasValidConfig>).mockReturnValue(true);
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
      (hasValidConfig as jest.MockedFunction<typeof hasValidConfig>).mockReturnValue(true);
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
      (hasValidConfig as jest.MockedFunction<typeof hasValidConfig>).mockReturnValue(true);
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn().mockReturnValue(10),
      });

      manager.ensureRunning();
      manager.dispose();

      expect(clearInterval).toHaveBeenCalledTimes(2);
    });
  });
});
