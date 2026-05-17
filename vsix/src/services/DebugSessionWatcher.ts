import * as vscode from 'vscode';
import { ApiClient } from '../apiClient';

/**
 * Bridges a VSCode debug session to its underlying pyops job. Polls
 * the job state; when it reaches a terminal state, calls
 * `vscode.debug.stopDebugging(session)` so the DAP disconnect triggers
 * debugpy's adapter to shut down on its own protocol path.
 *
 * Why we need this: `debugpy.listen()` actively detaches its adapter
 * via its own `setsid`+fork (debugpy.server.api.listen → daemonised
 * adapter subprocess). That means the runner's `killpg` on the job's
 * process group hits the job process cleanly but doesn't reach the
 * adapter — the adapter survives, VSCode keeps thinking the debugger
 * is connected, `onDidTerminateDebugSession` never fires, and the
 * lease never releases. This watcher closes that gap: it observes the
 * server-side job state and explicitly ends the editor-side session
 * once the job is gone.
 *
 * Sessions without a `jobId` in their configuration are ignored
 * (non-pyops debug sessions are someone else's problem).
 */
export class DebugSessionWatcher {
    private timers = new Map<string, NodeJS.Timeout>();
    private readonly POLL_INTERVAL_MS = 3000;
    private readonly TERMINAL_STATES = new Set([
        'done', 'failed', 'cancelled', 'killed', 'remove', 'removed',
    ]);

    constructor(
        private apiClient: ApiClient,
        private outputChannel: vscode.OutputChannel,
    ) {}

    /**
     * Start watching a debug session. No-op if the session has no
     * `jobId` (= not a pyops-managed session) or if already watching.
     */
    watch(session: vscode.DebugSession): void {
        const config = session.configuration as Record<string, unknown>;
        const jobId = typeof config?.jobId === 'string' ? config.jobId : undefined;
        if (!jobId) return;
        if (this.timers.has(session.id)) return;
        this.outputChannel.appendLine(
            `[DEBUG] watching session ${session.id} for job ${jobId} terminal state`,
        );
        this.scheduleNext(session, jobId);
    }

    /** Stop watching a session. Idempotent. */
    cancel(sessionId: string): void {
        const timer = this.timers.get(sessionId);
        if (timer) {
            clearTimeout(timer);
            this.timers.delete(sessionId);
        }
    }

    dispose(): void {
        for (const t of this.timers.values()) clearTimeout(t);
        this.timers.clear();
    }

    private scheduleNext(session: vscode.DebugSession, jobId: string): void {
        const timer = setTimeout(() => this.poll(session, jobId), this.POLL_INTERVAL_MS);
        this.timers.set(session.id, timer);
    }

    private async poll(session: vscode.DebugSession, jobId: string): Promise<void> {
        // Defensive: if the session terminated between schedule and fire,
        // bail. (onDidTerminateDebugSession may have already canceled us.)
        if (!this.timers.has(session.id)) return;

        let terminal: string | null = null;
        try {
            const resp = await this.apiClient.fetch(`/scriptlet/job/item?id=${jobId}`);
            if (!resp.ok) {
                if (resp.status === 404) {
                    terminal = 'gone (404)';
                } else {
                    // Transient — retry next tick.
                    this.scheduleNext(session, jobId);
                    return;
                }
            } else {
                const job = await resp.json() as { state: string };
                if (this.TERMINAL_STATES.has(job.state)) {
                    terminal = job.state;
                }
            }
        } catch (err: unknown) {
            // Network blip — retry. If this is a real outage the watcher
            // keeps trying; the operator can also cancel the debug session
            // manually from VSCode's UI.
            const msg = err instanceof Error ? err.message : String(err);
            this.outputChannel.appendLine(`[DEBUG] watcher poll error for ${jobId}: ${msg}`);
            this.scheduleNext(session, jobId);
            return;
        }

        if (terminal === null) {
            this.scheduleNext(session, jobId);
            return;
        }

        // Job reached terminal state → tear down the editor session.
        this.timers.delete(session.id);
        this.outputChannel.appendLine(
            `[DEBUG] job ${jobId} reached terminal state (${terminal}); ` +
            `stopping debug session ${session.id}`,
        );
        try {
            await vscode.debug.stopDebugging(session);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this.outputChannel.appendLine(
                `[DEBUG] stopDebugging failed for ${session.id}: ${msg}`,
            );
        }
    }
}
