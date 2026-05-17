import { DebugSessionWatcher } from '../../src/services/DebugSessionWatcher';

jest.mock('vscode');

describe('DebugSessionWatcher', () => {
  let mockApiClient: any;
  let mockOutputChannel: any;
  let watcher: DebugSessionWatcher;

  beforeEach(() => {
    jest.useFakeTimers();
    mockApiClient = {
      fetch: jest.fn(),
    };
    mockOutputChannel = { appendLine: jest.fn() };
    watcher = new DebugSessionWatcher(mockApiClient, mockOutputChannel);

    // Reset vscode.debug.stopDebugging mock between tests.
    const mockVscode = require('vscode');
    mockVscode.debug.stopDebugging = jest.fn().mockResolvedValue(true);
  });

  afterEach(() => {
    watcher.dispose();
    jest.useRealTimers();
  });

  function makeSession(id: string, jobId: string | undefined): any {
    return {
      id,
      configuration: jobId !== undefined ? { jobId } : {},
    };
  }

  function jobResp(state: string, ok: boolean = true, status: number = 200) {
    return {
      ok,
      status,
      json: () => Promise.resolve({ state }),
    };
  }

  it('ignores sessions without a jobId', () => {
    watcher.watch(makeSession('s1', undefined));
    // No timer scheduled → fast-forward 10s, nothing should happen.
    jest.advanceTimersByTime(10000);
    expect(mockApiClient.fetch).not.toHaveBeenCalled();
  });

  it('polls the job-item endpoint for sessions with a jobId', async () => {
    mockApiClient.fetch.mockResolvedValue(jobResp('loading'));
    watcher.watch(makeSession('s1', 'job-123'));
    // First tick.
    jest.advanceTimersByTime(3000);
    await Promise.resolve();
    expect(mockApiClient.fetch).toHaveBeenCalledWith('/scriptlet/job/item?id=job-123');
  });

  it('keeps polling while the job is not terminal', async () => {
    mockApiClient.fetch.mockResolvedValue(jobResp('running'));
    watcher.watch(makeSession('s1', 'job-123'));
    for (let i = 0; i < 4; i++) {
      jest.advanceTimersByTime(3000);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    }
    expect(mockApiClient.fetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    const mockVscode = require('vscode');
    expect(mockVscode.debug.stopDebugging).not.toHaveBeenCalled();
  });

  it('stops the session when the job reaches a terminal state', async () => {
    // First poll: still running. Second poll: cancelled.
    mockApiClient.fetch
      .mockResolvedValueOnce(jobResp('running'))
      .mockResolvedValueOnce(jobResp('cancelled'));

    const session = makeSession('s1', 'job-123');
    watcher.watch(session);

    // First tick — running, schedule another.
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve();
    // Second tick — cancelled, stopDebugging fires.
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();

    const mockVscode = require('vscode');
    expect(mockVscode.debug.stopDebugging).toHaveBeenCalledWith(session);
  });

  it.each(['done', 'failed', 'cancelled', 'killed', 'remove', 'removed'])(
    'treats %s as terminal',
    async (state) => {
      mockApiClient.fetch.mockResolvedValue(jobResp(state));
      const session = makeSession('s1', 'job-123');
      watcher.watch(session);
      jest.advanceTimersByTime(3000);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      const mockVscode = require('vscode');
      expect(mockVscode.debug.stopDebugging).toHaveBeenCalledWith(session);
    },
  );

  it('treats 404 as terminal (job purged before session ended)', async () => {
    mockApiClient.fetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    });
    const session = makeSession('s1', 'job-123');
    watcher.watch(session);
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    const mockVscode = require('vscode');
    expect(mockVscode.debug.stopDebugging).toHaveBeenCalledWith(session);
  });

  it('does not stop on transient non-404 HTTP errors — retries', async () => {
    mockApiClient.fetch
      .mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) })
      .mockResolvedValueOnce(jobResp('running'));
    const session = makeSession('s1', 'job-123');
    watcher.watch(session);
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve();
    jest.advanceTimersByTime(3000);
    await Promise.resolve();
    const mockVscode = require('vscode');
    expect(mockVscode.debug.stopDebugging).not.toHaveBeenCalled();
    expect(mockApiClient.fetch.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('does not stop on network exceptions — retries', async () => {
    mockApiClient.fetch
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(jobResp('running'));
    const session = makeSession('s1', 'job-123');
    watcher.watch(session);
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve();
    jest.advanceTimersByTime(3000);
    await Promise.resolve();
    const mockVscode = require('vscode');
    expect(mockVscode.debug.stopDebugging).not.toHaveBeenCalled();
  });

  it('cancel() stops polling for that session', async () => {
    mockApiClient.fetch.mockResolvedValue(jobResp('running'));
    watcher.watch(makeSession('s1', 'job-123'));
    watcher.cancel('s1');
    jest.advanceTimersByTime(10000);
    await Promise.resolve();
    // Nothing fetched because the only scheduled poll was canceled.
    expect(mockApiClient.fetch).not.toHaveBeenCalled();
  });

  it('watch() is idempotent', async () => {
    mockApiClient.fetch.mockResolvedValue(jobResp('running'));
    const s = makeSession('s1', 'job-123');
    watcher.watch(s);
    watcher.watch(s); // second call should be a no-op
    jest.advanceTimersByTime(3000);
    await Promise.resolve(); await Promise.resolve();
    expect(mockApiClient.fetch).toHaveBeenCalledTimes(1);
  });

  it('dispose() cancels all timers', async () => {
    mockApiClient.fetch.mockResolvedValue(jobResp('running'));
    watcher.watch(makeSession('s1', 'a'));
    watcher.watch(makeSession('s2', 'b'));
    watcher.dispose();
    jest.advanceTimersByTime(10000);
    await Promise.resolve();
    expect(mockApiClient.fetch).not.toHaveBeenCalled();
  });
});
