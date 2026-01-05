import { JobsTreeDataProvider, Job, JobTreeItem } from '../src/jobsTreeProvider';

jest.mock('vscode');

describe('JobsTreeDataProvider', () => {
  let mockContext: any;
  let provider: JobsTreeDataProvider;

  beforeEach(() => {
    jest.clearAllMocks();

    mockContext = {
      workspaceState: {
        get: jest.fn((key: string) => {
          if (key === 'pyops.password') return 'testpass';
          return '';
        }),
      },
    };

    // Mock workspace folders
    (require('vscode').workspace.workspaceFolders as any) = [{ uri: { fsPath: '/test/workspace' } }];

    // Mock configuration
    (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'username') return 'testuser';
        return '';
      }),
    });

// Mock fetch
global.fetch = jest.fn();

// Mock ApiClient
jest.mock('../src/apiClient', () => {
  const mockApiClient = jest.fn().mockImplementation(() => ({
    getHeaders: jest.fn().mockResolvedValue({ 'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M=' }),
    getServerUrl: jest.fn().mockReturnValue('http://localhost:8000'),
    hasValidConfig: jest.fn().mockReturnValue(true),
  }));
  (mockApiClient as any).hasValidConfigStatic = jest.fn().mockReturnValue(true);
  return {
    ApiClient: mockApiClient,
  };
});

// Mock vscode window
(require('vscode').window.showErrorMessage as jest.Mock).mockResolvedValue(undefined);


  });



  describe('constructor', () => {
    it('should create provider and call refresh', () => {
      // Spy on refresh to track calls
      const refreshSpy = jest.spyOn(JobsTreeDataProvider.prototype, 'refresh');

      const mockApiClient = new (require('../src/apiClient').ApiClient)();
      provider = new JobsTreeDataProvider(mockContext, mockApiClient);

      expect(refreshSpy).toHaveBeenCalled();
      expect(provider).toBeDefined();
    });
  });

  describe('getTreeItem', () => {
    beforeEach(() => {
      // Mock refresh to prevent constructor from calling it
      jest.spyOn(JobsTreeDataProvider.prototype, 'refresh').mockResolvedValue(undefined);
      const mockApiClient = new (require('../src/apiClient').ApiClient)();
      provider = new JobsTreeDataProvider(mockContext, mockApiClient);
    });

    it('should return the tree item as-is', () => {
      const mockElement = { label: 'test', id: '123' } as JobTreeItem;

      const result = provider.getTreeItem(mockElement);

      expect(result).toBe(mockElement);
    });
  });

  describe('getChildren', () => {
    beforeEach(() => {
      // Mock refresh to prevent constructor from calling it
      jest.spyOn(JobsTreeDataProvider.prototype, 'refresh').mockResolvedValue(undefined);
      const mockApiClient = new (require('../src/apiClient').ApiClient)();
      provider = new JobsTreeDataProvider(mockContext, mockApiClient);
    });

    it('should return "No Workspace Open" when no workspace folders', async () => {
      (require('vscode').workspace.workspaceFolders as any) = undefined;

      const result = await provider.getChildren();

      expect(result).toHaveLength(1);
      expect(result[0].label).toBe('No Workspace Open');
      expect(result[0].collapsibleState).toBe(0); // TreeItemCollapsibleState.None
    });

    it('should return config warning when config is invalid', async () => {
      // Mock invalid config by making hasValidConfig return false
      (provider as any)._apiClient.hasValidConfig.mockReturnValue(false);

      const result = await provider.getChildren();

      expect(result).toHaveLength(1);
      expect(result[0].label).toBe('No PyOps serverUrl set. Please configure serverUrl in settings.');
      expect(result[0].contextValue).toBe('noConfig');
      expect(result[0].iconPath).toBeDefined();
      expect(result[0].tooltip).toBe('Open settings (Ctrl+,) to configure serverUrl.');
    });

    it('should return loading message when loading', async () => {
      // Restore refresh for this test to get real behavior
      const refreshSpy = jest.spyOn(JobsTreeDataProvider.prototype, 'refresh');
      refreshSpy.mockRestore();

      // Set loading state by calling refresh
      const refreshPromise = provider.refresh();
      const result = await provider.getChildren();

      expect(result).toHaveLength(1);
      expect(result[0].label).toBe('Loading Jobs...');
      expect(result[0].contextValue).toBe('loading');

      await refreshPromise; // Wait for refresh to complete
    });

    it('should return empty message when no jobs', async () => {
      // Mock fetch to return empty array
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue([]),
      });

      await provider.refresh();
      const result = await provider.getChildren();

      expect(result).toHaveLength(1);
      expect(result[0].label).toBe('No Jobs Available');
      expect(result[0].contextValue).toBe('empty');
    });

    it('should return job items when jobs exist', async () => {
      // Restore refresh for this test to get real behavior
      const refreshSpy = jest.spyOn(JobsTreeDataProvider.prototype, 'refresh');
      refreshSpy.mockRestore();

      const mockJobs: Job[] = [
        {
          id: 'job1',
          start: '2023-01-01T10:00:00Z',
          end: '2023-01-01T10:05:00Z',
          kind: 'task',
          state: 'done',
          detail: 'Task completed successfully',
          param: 'param1=value1',
        },
        {
          id: 'job2',
          start: '2023-01-01T09:00:00Z',
          end: '2023-01-01T09:10:00Z',
          kind: 'script',
          state: 'running',
        },
      ];

      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue(mockJobs),
      });

      await provider.refresh();
      const result = await provider.getChildren();

      expect(result).toHaveLength(2);

      // Check first job (should be job1 due to sorting by start time desc - newest first)
      expect(result[0].label).toBe('job1');
      expect(result[0].description).toBe('done - task');
      expect(result[0].contextValue).toBe('pyopsJob-done');
      expect(result[0].id).toBe('job1');
      expect(result[0].iconPath).toBeDefined();
      expect(result[0].tooltip).toContain('done: Task completed successfully');
      expect((result[0] as JobTreeItem).job).toBe(mockJobs[0]);

      // Check second job
      expect(result[1].label).toBe('job2');
      expect(result[1].description).toBe('running - script');
      expect(result[1].contextValue).toBe('pyopsJob-running');
      expect(result[1].id).toBe('job2');
      expect(result[1].iconPath).toBeDefined();
      expect(result[1].tooltip).toContain('running:');
      expect((result[1] as JobTreeItem).job).toBe(mockJobs[1]);
    });

    it('should handle unknown job state with default icon', async () => {
      // Restore refresh for this test to get real behavior
      const refreshSpy = jest.spyOn(JobsTreeDataProvider.prototype, 'refresh');
      refreshSpy.mockRestore();

      const mockJobs: Job[] = [
        {
          id: 'job1',
          start: '2023-01-01T10:00:00Z',
          end: '2023-01-01T10:05:00Z',
          kind: 'task',
          state: 'unknown',
          detail: 'Unknown state',
        },
      ];

      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue(mockJobs),
      });

      await provider.refresh();
      const result = await provider.getChildren();

      expect(result).toHaveLength(1);
      expect(result[0].label).toBe('job1');
      expect(result[0].description).toBe('unknown - task');
      expect(result[0].contextValue).toBe('pyopsJob-unknown');
      expect(result[0].iconPath).toEqual(new (require('vscode').ThemeIcon)('symbol-method'));
    });

    it('should return empty array for job children', async () => {
      const mockElement = { job: { id: '123' } } as JobTreeItem;

      const result = await provider.getChildren(mockElement);

      expect(result).toHaveLength(0);
    });
  });

  describe('fetchJobs', () => {
    beforeEach(() => {
      // Mock refresh to prevent constructor from calling it
      jest.spyOn(JobsTreeDataProvider.prototype, 'refresh').mockResolvedValue(undefined);
      const mockApiClient = new (require('../src/apiClient').ApiClient)();
      provider = new JobsTreeDataProvider(mockContext, mockApiClient);
    });

    it('should fetch jobs successfully', async () => {
      const mockJobs: Job[] = [{ id: 'job1', start: '', end: '', kind: '', state: '' }];
      const mockResponse = {
        ok: true,
        json: jest.fn().mockResolvedValue(mockJobs),
      };
      (global.fetch as jest.Mock).mockResolvedValue(mockResponse);

      const result = await (provider as any).fetchJobs();

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/job/list', {
        headers: {
          'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M=', // base64 of testuser:testpass
        },
      });
      expect(result).toEqual(mockJobs);
      expect((provider as any)._lastJobsErrorShown).toBe(0);
    });

    it('should handle fetch error', async () => {
      const mockResponse = {
        ok: false,
        statusText: 'Internal Server Error',
      };
      (global.fetch as jest.Mock).mockResolvedValue(mockResponse);

      const result = await (provider as any).fetchJobs();

      expect(result).toEqual([]);
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledWith('Failed to fetch jobs: Internal Server Error');
    });

    it('should handle network error', async () => {
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

      const result = await (provider as any).fetchJobs();

      expect(result).toEqual([]);
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledWith('Error fetching jobs: Network error');
    });

    it('should throttle error messages', async () => {
      const mockResponse = {
        ok: false,
        statusText: 'Error',
      };
      (global.fetch as jest.Mock).mockResolvedValue(mockResponse);

      // First call
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(1);

      // Second call immediately after should not show error
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(1);

      // Mock time passing
      jest.spyOn(Date, 'now').mockReturnValue(Date.now() + 35000);
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(2);
    });

    it('should throttle network error messages', async () => {
      (global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'));

      // First call
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(1);

      // Second call immediately after should not show error
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(1);

      // Mock time passing
      jest.spyOn(Date, 'now').mockReturnValue(Date.now() + 35000);
      await (provider as any).fetchJobs();
      expect(require('vscode').window.showErrorMessage).toHaveBeenCalledTimes(2);
    });
  });

  describe('refresh', () => {
    beforeEach(() => {
      const mockApiClient = new (require('../src/apiClient').ApiClient)();
      provider = new JobsTreeDataProvider(mockContext, mockApiClient);
      // Restore the original refresh method for these tests
      jest.spyOn(JobsTreeDataProvider.prototype, 'refresh').mockRestore();
    });

    it('should clear jobs and fire event when config is invalid', async () => {
      // Mock invalid config
      mockContext.workspaceState.get.mockReturnValue('');

      const fireSpy = jest.spyOn((provider as any)._onDidChangeTreeData, 'fire');

      await provider.refresh();

      expect((provider as any)._jobs).toEqual([]);
      expect((provider as any)._loading).toBe(false);
      expect(fireSpy).toHaveBeenCalled();
    });

    it('should fetch jobs and update state when config is valid', async () => {
      const mockJobs: Job[] = [{ id: 'job1', start: '', end: '', kind: '', state: '' }];
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: jest.fn().mockResolvedValue(mockJobs),
      });

      const fireSpy = jest.spyOn((provider as any)._onDidChangeTreeData, 'fire');
      fireSpy.mockClear(); // Clear any calls from constructor

      const refreshPromise = provider.refresh();

      expect((provider as any)._loading).toBe(true);
      expect(fireSpy).toHaveBeenCalledTimes(1); // Loading state

      await refreshPromise;

      expect((provider as any)._jobs).toEqual(mockJobs);
      expect((provider as any)._loading).toBe(false);
      expect(fireSpy).toHaveBeenCalledTimes(2); // Final state
    });
  });
});
