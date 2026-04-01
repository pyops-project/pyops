import { JobService } from '../../src/services/JobService';
import { ApiClient } from '../../src/apiClient';
import { JobsTreeDataProvider, JobTreeItem } from '../../src/jobsTreeProvider';
import { createMockOutputChannel, createMockVscodeWindow, createMockApiClient, createMockTreeProvider } from '../utils/helpers';

jest.mock('vscode');
jest.mock('../../src/apiClient', () => ({ ApiClient: jest.fn() }));

const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();

let fetchCallCount = 0;

describe('JobService', () => {
  let mockApiClient: jest.Mocked<ApiClient>;
  let mockJobsTreeProvider: jest.Mocked<JobsTreeDataProvider>;
  let mockJobViewOutputChannel: any;
  let service: JobService;

  beforeEach(() => {
    jest.clearAllMocks();
    fetchCallCount = 0;
    const mockVscode = require('vscode');
    createMockVscodeWindow();
    mockVscode.workspace = { fs: { writeFile: jest.fn() } };

    mockApiClient = createMockApiClient(ApiClient, { headers: { 'Authorization': 'Basic test' } }) as any;

    mockJobsTreeProvider = createMockTreeProvider('jobs');

    mockJobViewOutputChannel = createMockOutputChannel();

    service = new JobService(mockApiClient, mockJobsTreeProvider, mockJobViewOutputChannel);
  });

  describe('copyJobParam', () => {
    it('copies param to clipboard when job selected', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1', param: 'test param' } } as JobTreeItem;

      await service.copyJobParam(treeItem);

      expect(mockVscode.env.clipboard.writeText).toHaveBeenCalledWith('test param');
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Copied param for job job1');
    });

    it('shows warning when no job selected', async () => {
      const mockVscode = require('vscode');

      await service.copyJobParam(undefined);

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No job selected');
    });
  });

  describe('cancelJob', () => {
    it('cancels job successfully', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
      } as Response);

      await service.cancelJob(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/cancel?id=job1');
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Canceled job job1');
});

    it('handles cancel failure', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.cancelJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to cancel job: Not Found');
    });
  });

  describe('downloadJob', () => {
    it('downloads job successfully', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;
      const mockUri = { fsPath: '/path/to/job1.zip' };

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(new Blob(['test'])),
      } as Response);
      mockVscode.window.showSaveDialog.mockResolvedValue(mockUri);
      mockVscode.workspace.fs.writeFile = jest.fn();

      await service.downloadJob(treeItem);

      expect(mockVscode.window.showSaveDialog).toHaveBeenCalledWith({
        defaultUri: mockVscode.Uri.file('job1.zip'),
        filters: { 'ZIP Files': ['zip'] },
  });
      expect(mockVscode.workspace.fs.writeFile).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Downloaded workspace for job job1 to /path/to/job1.zip');
    });
});

  describe('deleteJob', () => {
    it('deletes job after confirmation', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');
      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
      } as Response);

      await service.deleteJob(treeItem);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/item?id=job1', { method: 'DELETE' });
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted job job1');
    });

    it('does not delete if not confirmed', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue(undefined);

      await service.deleteJob(treeItem);

      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });
  });

  describe('viewJob', () => {
    it('starts polling job details', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      await service.viewJob(treeItem);

      expect(mockJobViewOutputChannel.clear).toHaveBeenCalled();
      expect(mockJobViewOutputChannel.show).toHaveBeenCalledWith(true);
      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('Viewing job job1 details...\n');
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Viewing details for job job1');

      service.dispose(); // Clear intervals to prevent open handles
    });
  });

  describe('bulk actions', () => {
    it('cancelAllJobs performs bulk cancel', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'running' },
        { id: 'job2', state: 'done' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock).mockResolvedValue({
        ok: true,
      } as Response);

      await service.cancelAllJobs();

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/cancel?id=job1');
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
    });

    it('deleteAllDone performs bulk delete', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'done' },
        { id: 'job2', state: 'running' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock).mockResolvedValue({
        ok: true,
      } as Response);

      await service.deleteAllDone();

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/item?id=job1', { method: 'DELETE' });
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
    });
  });

  describe('fetchJobs', () => {
    it('handles network errors', async () => {
      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      const result = await (service as any).fetchJobs();

      expect(result).toEqual([]);
    });

    it('handles non-ok responses', async () => {
      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Server Error',
      } as Response);

      const result = await (service as any).fetchJobs();

      expect(result).toEqual([]);
    });
  });

  describe('cancelJob', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.cancelJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error canceling job: Network error');
    });


  });

  describe('downloadJob', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.downloadJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error downloading job: Network error');
    });

    it('handles save dialog cancellation', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(new Blob(['test'])),
      } as Response);
      mockVscode.window.showSaveDialog.mockResolvedValue(undefined);

      await service.downloadJob(treeItem);

      expect(mockVscode.workspace.fs.writeFile).not.toHaveBeenCalled();
    });
  });

  describe('deleteJob', () => {
    it('handles network errors', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');
      (mockApiClient.fetch as jest.Mock).mockRejectedValueOnce(new Error('Network error'));

      await service.deleteJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error deleting job: Network error');
    });
  });

  describe('viewJob', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
      service.dispose(); // Clean up any intervals
    });

    it('handles job already being viewed', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      // Start viewing first time
      await service.viewJob(treeItem);
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Viewing details for job job1');

      // Try to view same job again
      await service.viewJob(treeItem);
      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('Job details already being viewed');
    });

    it('handles job polling with stdout and stderr updates', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      // Mock successful fetch responses for polling
      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({
            param: 'test param',
            stdout: 'initial output',
            stderr: 'initial error',
            state: 'running'
          }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({
            stdout: 'initial output\nnew output',
            stderr: 'initial error\nnew error',
            state: 'running'
          }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({
            stdout: 'initial output\nnew output',
            stderr: 'initial error\nnew error',
            state: 'done'
          }),
        } as Response);

      await service.viewJob(treeItem);

      // Fast-forward timers to trigger polling
      jest.advanceTimersByTime(1000);
      await Promise.resolve(); // Allow async operations

      jest.advanceTimersByTime(1000);
      await Promise.resolve();

      jest.advanceTimersByTime(1000);
      await Promise.resolve();

      // Verify that stdout/stderr updates are processed
      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('[STDOUT]');
      expect(mockJobViewOutputChannel.append).toHaveBeenCalledWith('\nnew output');
      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('[STDERR]');
      expect(mockJobViewOutputChannel.append).toHaveBeenCalledWith('\nnew error');
    });

    it('handles job not found (404)', async () => {
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ state: 'running' }),
        } as Response)
        .mockResolvedValueOnce({
          ok: false,
          status: 404,
        } as Response);

      await service.viewJob(treeItem);

      jest.advanceTimersByTime(2000);
      await Promise.resolve();

      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('\n--- Job no longer exists ---');
    });

    it('handles polling errors gracefully', async () => {
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ state: 'running' }),
        } as Response)
        .mockRejectedValueOnce(new Error('Poll error'));

      await service.viewJob(treeItem);

      jest.advanceTimersByTime(2000);
      await Promise.resolve();

      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('Error polling job: Poll error');
    });
  });

  describe('bulk actions', () => {
    it('deleteAllFailed performs bulk delete for failed jobs', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'failed' },
        { id: 'job2', state: 'running' },
        { id: 'job3', state: 'done' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock).mockResolvedValue({
        ok: true,
      } as Response);

      await service.deleteAllFailed();

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/item?id=job1', { method: 'DELETE' });
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
    });

    it('deleteAllFailed deletes killed jobs', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'jobKilled', state: 'killed' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock).mockResolvedValue({
        ok: true,
      } as Response);

      await service.deleteAllFailed();

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/item?id=jobKilled', { method: 'DELETE' });
      expect(mockJobsTreeProvider.refresh).toHaveBeenCalled();
    });

    it('bulk actions handle no jobs matching criteria', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'done' },
      ]);

      await service.cancelAllJobs();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('No jobs to cancel matching criteria.');
    });

    it('deleteAllDone handles no jobs matching criteria', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'running' },
      ]);

      await service.deleteAllDone();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('No jobs to delete matching criteria.');
    });

    it('deleteAllFailed handles no jobs matching criteria', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'running' },
      ]);

      await service.deleteAllFailed();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('No jobs to delete matching criteria.');
    });

    it('bulk actions handle confirmation cancellation', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'running' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue(undefined);

      await service.cancelAllJobs();

      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });

    it('bulk actions handle partial failures', async () => {
      const mockVscode = require('vscode');

      jest.spyOn(service as any, 'fetchJobs').mockResolvedValue([
        { id: 'job1', state: 'running' },
        { id: 'job2', state: 'running' },
      ]);
      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true } as Response)
        .mockRejectedValueOnce(new Error('Network error'));

      await service.cancelAllJobs();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Bulk cancel: 1 successful, 1 failed.');
    });
  });

  describe('service with undefined jobsTreeProvider', () => {
    beforeEach(() => {
      service = new JobService(mockApiClient, undefined, mockJobViewOutputChannel);
    });

    it('handles cancelJob without provider refresh', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
      } as Response);

      await service.cancelJob(treeItem);

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Canceled job job1');
      // Should not throw when provider is undefined
    });

    it('handles deleteJob without provider refresh', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');
      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
      } as Response);

      await service.deleteJob(treeItem);

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Deleted job job1');
    });
  });

  describe('cancelJob error handling', () => {
    it('handles non-ok cancel responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Forbidden',
      } as Response);

      await service.cancelJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to cancel job: Forbidden');
    });
  });

  describe('downloadJob error handling', () => {
    it('handles non-ok download responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await service.downloadJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to download job: Not Found');
    });
  });

  describe('deleteJob confirmation handling', () => {
    it('handles delete confirmation rejection', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Cancel');

      await service.deleteJob(treeItem);

      expect(mockApiClient.fetch).not.toHaveBeenCalled();
    });
  });

  describe('deleteJob error handling', () => {
    it('handles non-ok delete responses', async () => {
      const mockVscode = require('vscode');
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      mockVscode.window.showWarningMessage.mockResolvedValue('Delete');
      (mockApiClient.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        text: () => Promise.resolve('Delete forbidden'),
      } as Response);

      await service.deleteJob(treeItem);

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to delete job: Delete forbidden');
    });
  });

  describe('viewJob polling edge cases', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
      service.dispose();
    });

    it('handles job polling with missing stdout/stderr', async () => {
      jest.useRealTimers();
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      (mockApiClient.fetch as jest.Mock).mockImplementation(() => {
        fetchCallCount++;
        if (fetchCallCount === 1) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              param: 'test param',
              state: 'running'
            }),
          } as Response);
        } else {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              state: 'done'
            }),
          } as Response);
        }
      });

      await service.viewJob(treeItem);

      // Wait for polling to complete
      await new Promise(resolve => setTimeout(resolve, 2100));

      // Check that job ended and activeJobs was cleared
      expect((service as any).activeJobs.size).toBe(0);
      expect(mockJobViewOutputChannel.appendLine).toHaveBeenCalledWith('\n--- State: done ---');

      jest.useFakeTimers();
    });
  });

  describe('bulk operations error scenarios', () => {
    it('handles bulk operation when fetch returns empty jobs', async () => {
      const mockVscode = require('vscode');

      // Mock the fetchJobs method to return empty array (simulating fetch error scenario)
      const originalFetchJobs = (service as any).fetchJobs;
      (service as any).fetchJobs = jest.fn().mockResolvedValue([]);

      await service.cancelAllJobs();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('No jobs to cancel matching criteria.');

      // Restore original method
      (service as any).fetchJobs = originalFetchJobs;
    });

    it('handles bulk operation with individual operation failures', async () => {
      const mockVscode = require('vscode');

      // Mock the fetchJobs method to return jobs
      const originalFetchJobs = (service as any).fetchJobs;
      (service as any).fetchJobs = jest.fn().mockResolvedValue([
        { id: 'job1', state: 'running' },
      ]);

      mockVscode.window.showWarningMessage.mockResolvedValue('Yes');
      (mockApiClient.fetch as jest.Mock).mockRejectedValue(new Error('Operation failed'));

      await service.cancelAllJobs();

      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Bulk cancel: 0 successful, 1 failed.');

      // Restore original method
      (service as any).fetchJobs = originalFetchJobs;
    });
  });

  describe('dispose', () => {
    it('clears intervals and disposes channel', () => {
      service.dispose();

      expect(mockJobViewOutputChannel.dispose).toHaveBeenCalled();
    });

    it('clears active job intervals', async () => {
      const treeItem = { job: { id: 'job1' } } as JobTreeItem;

      await service.viewJob(treeItem);

      const clearIntervalSpy = jest.spyOn(global, 'clearInterval');

      service.dispose();

      expect(clearIntervalSpy).toHaveBeenCalled();
      expect(mockJobViewOutputChannel.dispose).toHaveBeenCalled();
    });
  });
});
