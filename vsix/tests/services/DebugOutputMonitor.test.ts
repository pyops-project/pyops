import { DebugOutputMonitor } from '../../src/services/DebugOutputMonitor';
import { ApiClient } from '../../src/apiClient';

jest.mock('vscode');
jest.mock('../../src/apiClient');

global.fetch = jest.fn();

const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();

const flushPromises = () => new Promise(resolve => process.nextTick(resolve));

describe('DebugOutputMonitor', () => {
  let mockApiClient: jest.Mocked<ApiClient>;
  let mockOutputChannel: any;
  let monitor: DebugOutputMonitor;
  let mockFetch: jest.MockedFunction<typeof global.fetch>;

  beforeEach(() => {
    jest.clearAllMocks();
    const mockVscode = require('vscode');

    mockVscode.window.showWarningMessage = jest.fn();
    mockVscode.window.showInformationMessage = jest.fn();
    mockVscode.window.showErrorMessage = jest.fn();

    mockApiClient = new ApiClient({} as any) as jest.Mocked<ApiClient>;
    // @ts-ignore
    mockApiClient.getHeaders.mockResolvedValue({ 'Authorization': 'Basic test' });
    mockApiClient.getServerUrl.mockReturnValue('http://localhost:8000');

    mockFetch = global.fetch as jest.MockedFunction<typeof global.fetch>;
    mockFetch.mockResolvedValue({ ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any);

    mockOutputChannel = {
      append: jest.fn(),
      appendLine: jest.fn(),
      show: jest.fn(),
    };

    monitor = new DebugOutputMonitor(mockApiClient, mockOutputChannel);
  });

  afterEach(() => {
    monitor.dispose();
  });

  describe('startMonitoring', () => {
    it('starts monitoring', async () => {
      await monitor.startMonitoring('key1', 'job1');

      expect(monitor.isMonitoring('key1')).toBe(true);
    });

    it('shows warning if already monitoring', async () => {
      const mockVscode = require('vscode');

      await monitor.startMonitoring('key1', 'job1');

      await monitor.startMonitoring('key1', 'job2');

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('Debug job already running');
    });
  });

  describe('startMonitoring polling', () => {
    it('appends incremental stdout and shows channel', async () => {
      const firstJobData = { stdout: 'hello', stderr: '', state: 'running' };
      const firstResponse = { ok: true, json: jest.fn().mockResolvedValue(firstJobData) } as any;
      mockFetch.mockResolvedValueOnce(firstResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately in startMonitoring, so check initial state
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[STDOUT]');
      expect(mockOutputChannel.append).toHaveBeenCalledWith('hello');
      expect(mockOutputChannel.show).toHaveBeenCalledWith(true);

      const secondJobData = { stdout: 'hello world', stderr: '', state: 'running' };
      const secondResponse = { ok: true, json: jest.fn().mockResolvedValue(secondJobData) } as any;
      mockFetch.mockResolvedValueOnce(secondResponse);

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(mockOutputChannel.append).toHaveBeenLastCalledWith(' world');

      await monitor.stopMonitoring('key1');
    });

    it('appends incremental stderr', async () => {
      const firstJobData = { stdout: '', stderr: 'error line 1', state: 'running' };
      const firstResponse = { ok: true, json: jest.fn().mockResolvedValue(firstJobData) } as any;
      mockFetch.mockResolvedValueOnce(firstResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('[STDERR]');
      expect(mockOutputChannel.append).toHaveBeenCalledWith('error line 1');

      const secondJobData = { stdout: '', stderr: 'error line 1\\nerror line 2', state: 'running' };
      const secondResponse = { ok: true, json: jest.fn().mockResolvedValue(secondJobData) } as any;
      mockFetch.mockResolvedValueOnce(secondResponse);

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(mockOutputChannel.append).toHaveBeenLastCalledWith('\\nerror line 2');

      await monitor.stopMonitoring('key1');
    });

    it('does not append if no new output', async () => {
      const jobData = { stdout: 'hello', stderr: '', state: 'running' };
      const response = { ok: true, json: jest.fn().mockResolvedValue(jobData) } as any;
      mockFetch.mockResolvedValueOnce(response);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(mockOutputChannel.append).toHaveBeenCalledTimes(1);

      mockFetch.mockResolvedValueOnce(response); // same length

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(mockOutputChannel.append).toHaveBeenCalledTimes(1);

      await monitor.stopMonitoring('key1');
    });

    it('handles missing stdout/stderr', async () => {
      const response = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(response);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(mockOutputChannel.append).not.toHaveBeenCalled();

      await monitor.stopMonitoring('key1');
    });

    it('stops polling on job done state', async () => {
      const initialResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(initialResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately and job is still running
      expect(monitor.isMonitoring('key1')).toBe(true);

      const doneResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'done' }) } as any;
      mockFetch.mockResolvedValueOnce(doneResponse);

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(monitor.isMonitoring('key1')).toBe(false);
    });

    it('stops polling on 404', async () => {
      const initialResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(initialResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(monitor.isMonitoring('key1')).toBe(true);

      const notFoundResponse = { ok: false, status: 404 } as Response;
      mockFetch.mockResolvedValueOnce(notFoundResponse);

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(monitor.isMonitoring('key1')).toBe(false);
    });

    it('logs error on fetch failure during poll', async () => {
      const initialResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(initialResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(monitor.isMonitoring('key1')).toBe(true);

      mockFetch.mockRejectedValueOnce(new Error('fetch failed'));

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(consoleErrorSpy).toHaveBeenCalledWith(expect.stringContaining('Error polling debug job: fetch failed'));
      expect(monitor.isMonitoring('key1')).toBe(true);

      await monitor.stopMonitoring('key1');
    });

    it('continues polling on non-404 error response', async () => {
      const initialResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(initialResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(monitor.isMonitoring('key1')).toBe(true);

      const errorResponse = { ok: false, status: 500 } as Response;
      mockFetch.mockResolvedValueOnce(errorResponse);

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(monitor.isMonitoring('key1')).toBe(true);

      await monitor.stopMonitoring('key1');
    });

    it('logs error on fetch failure with non-Error', async () => {
      const initialResponse = { ok: true, json: jest.fn().mockResolvedValue({ state: 'running' }) } as any;
      mockFetch.mockResolvedValueOnce(initialResponse);

      await monitor.startMonitoring('key1', 'job1');

      // First poll happens immediately
      expect(monitor.isMonitoring('key1')).toBe(true);

      mockFetch.mockRejectedValueOnce('network error');

      await new Promise(resolve => setTimeout(resolve, 1100));

      expect(consoleErrorSpy).toHaveBeenCalledWith(expect.stringContaining('Error polling debug job: network error'));
      expect(monitor.isMonitoring('key1')).toBe(true);

      await monitor.stopMonitoring('key1');
    });
  });

  describe('stopMonitoring', () => {
    it('cancels job and stops monitoring', async () => {
      const mockVscode = require('vscode');

      await monitor.startMonitoring('key1', 'job1');

      mockFetch.mockResolvedValueOnce({
        ok: true,
      } as Response);

      await monitor.stopMonitoring('key1');

      expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/scriptlet/job/cancel?id=job1', {
        headers: { 'Authorization': 'Basic test' },
      });
      expect(mockOutputChannel.appendLine).toHaveBeenCalledWith('\n--- Job stopped ---\n');
      expect(mockVscode.window.showInformationMessage).toHaveBeenCalledWith('Stopped debug job job1');
      expect(monitor.isMonitoring('key1')).toBe(false);
    });

    it('handles cancel failure', async () => {
      const mockVscode = require('vscode');

      await monitor.startMonitoring('key1', 'job1');

      mockFetch.mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      } as Response);

      await monitor.stopMonitoring('key1');

      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Failed to stop debug: Not Found');
    });

    it('shows warning if not monitoring', async () => {
      const mockVscode = require('vscode');

      await monitor.stopMonitoring('key1');

      expect(mockVscode.window.showWarningMessage).toHaveBeenCalledWith('No active debug job');
    });

    it('handles fetch error during cancel', async () => {
      const mockVscode = require('vscode');
      await monitor.startMonitoring('key1', 'job1');
      mockFetch.mockRejectedValueOnce(new Error('network error'));
      await monitor.stopMonitoring('key1');
      expect(mockVscode.window.showErrorMessage).toHaveBeenCalledWith('Error stopping debug: network error');
      expect(monitor.isMonitoring('key1')).toBe(true);
    });
  });

  describe('isMonitoring', () => {
    it('returns true if monitoring', async () => {
      await monitor.startMonitoring('key1', 'job1');

      expect(monitor.isMonitoring('key1')).toBe(true);
      expect(monitor.isMonitoring('key2')).toBe(false);
    });
  });

  describe('dispose', () => {
    it('clears all intervals', async () => {
      await monitor.startMonitoring('key1', 'job1');
      await monitor.startMonitoring('key2', 'job2');

      monitor.dispose();

      expect(monitor.isMonitoring('key1')).toBe(false);
      expect(monitor.isMonitoring('key2')).toBe(false);
    });
  });
});
