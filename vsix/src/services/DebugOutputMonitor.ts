import * as vscode from 'vscode';
import { ApiClient } from '../apiClient';
import { errorText } from '../errorText';
import { appendIncrementalOutput } from '../utils';

export class DebugOutputMonitor {
    private activeJobs = new Map<string, { timeout: NodeJS.Timeout | null; jobId: string; prevStdoutLen: number; prevStderrLen: number }>();

    constructor(private apiClient: ApiClient, private outputChannel: vscode.OutputChannel) {}

    /**
     * Performs one polling cycle for the given key.
     * @returns true if monitoring should continue, false if it should stop
     */
    private async pollOnce(key: string): Promise<boolean> {
        const active = this.activeJobs.get(key);
        if (!active) {
            return false;
        }

        try {
            const jobResponse = await this.apiClient.fetch(`/scriptlet/job/item?id=${active.jobId}`);
            if (jobResponse.ok) {
                const jobData = await jobResponse.json() as unknown;
                const jobObj = jobData as { stdout?: string; stderr?: string; state: string };

                appendIncrementalOutput(this.outputChannel, jobObj, active);
                if (['done', 'failed', 'cancelled', 'remove'].includes(jobObj.state)) {
                    this.activeJobs.delete(key);
                    return false;
                }
                return true;
            } else if (jobResponse.status === 404) {
                this.activeJobs.delete(key);
                return false;
            } else {
                return true;
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            console.error(`Error polling debug job: ${errorMessage}`);
            return true;
        }
    }

    async startMonitoring(key: string, jobId: string): Promise<void> {
        if (this.activeJobs.has(key)) {
            vscode.window.showWarningMessage('Debug job already running');
            return;
        }

        this.activeJobs.set(key, { timeout: null, jobId, prevStdoutLen: 0, prevStderrLen: 0 });

        const pollLoop = async () => {
            const shouldContinue = await this.pollOnce(key);
            if (shouldContinue) {
                const active = this.activeJobs.get(key);
                if (active) {
                    active.timeout = setTimeout(pollLoop, 1000);
                }
            }
        };

        // Start the first poll immediately
        const shouldContinue = await this.pollOnce(key);
        if (shouldContinue) {
            const active = this.activeJobs.get(key);
            if (active) {
                active.timeout = setTimeout(pollLoop, 1000);
            }
        }
    }

    async stopMonitoring(key: string): Promise<void> {
        const active = this.activeJobs.get(key);
        if (!active) {
            vscode.window.showWarningMessage('No active debug job');
            return;
        }

        const jobId = active.jobId;
        try {
            const cancelResponse = await this.apiClient.fetch(`/scriptlet/job/cancel?id=${jobId}`);
            // 409: the job had already finished (or is being deleted) —
            // nothing left to stop, so the monitor entry goes either way.
            const alreadyFinished = cancelResponse.status === 409;
            if (cancelResponse.ok || alreadyFinished) {
                if (active.timeout) clearTimeout(active.timeout);
                this.activeJobs.delete(key);
                if (alreadyFinished) {
                    this.outputChannel.appendLine('\n--- Job already finished ---\n');
                    vscode.window.showInformationMessage(`Debug job ${jobId} already finished`);
                } else {
                    this.outputChannel.appendLine('\n--- Job stopped ---\n');
                    vscode.window.showInformationMessage(`Stopped debug job ${jobId}`);
                }
            } else {
                vscode.window.showErrorMessage(`Failed to stop debug: ${await errorText(cancelResponse)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error stopping debug: ${errorMessage}`);
        }
    }

    isMonitoring(key: string): boolean {
        return this.activeJobs.has(key);
    }

    dispose(): void {
        this.activeJobs.forEach(({ timeout }) => { if (timeout) clearTimeout(timeout); });
        this.activeJobs.clear();
    }
}
