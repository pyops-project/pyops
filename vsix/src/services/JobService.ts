import * as vscode from 'vscode';
import { ApiClient } from '../apiClient';
import { errorText } from '../errorText';
import { JobsTreeDataProvider, JobTreeItem, Job } from '../jobsTreeProvider';
import { appendIncrementalOutput } from '../utils';

export class JobService {
    private activeJobs = new Map<string, { interval: NodeJS.Timeout; jobId: string; prevStdoutLen: number; prevStderrLen: number }>();

    constructor(private apiClient: ApiClient, private jobsTreeProvider: JobsTreeDataProvider | undefined, private jobViewOutputChannel: vscode.OutputChannel) {}

    public async listJobs(): Promise<Job[]> {
        return await this.fetchJobs();
    }

    protected async fetchJobs(): Promise<Job[]> {
        try {
            const response = await this.apiClient.fetch('/scriptlet/job/list');
            if (response.ok) {
                return await response.json() as Job[];
            } else {
                vscode.window.showErrorMessage(`Failed to fetch jobs: ${await errorText(response)}`);
                return [];
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error fetching jobs: ${errorMessage}`);
            return [];
        }
    }

    async copyJobParam(treeItem: JobTreeItem | undefined): Promise<void> {
        if (!treeItem || !treeItem.job) {
            vscode.window.showWarningMessage('No job selected');
            return;
        }
        const param = treeItem.job.param || '';
        await vscode.env.clipboard.writeText(param);
        vscode.window.showInformationMessage(`Copied param for job ${treeItem.job.id}`);
    }

    async cancelJob(treeItem: JobTreeItem | undefined): Promise<void> {
        if (!treeItem || !treeItem.job) {
            vscode.window.showWarningMessage('No job selected');
            return;
        }
        const id = treeItem.job.id;
        try {
            const response = await this.apiClient.fetch(`/scriptlet/job/cancel?id=${id}`);
            if (response.ok) {
                if (this.jobsTreeProvider) {
                    this.jobsTreeProvider.refresh();
                }
                vscode.window.showInformationMessage(`Canceled job ${id}`);
            } else {
                vscode.window.showErrorMessage(`Failed to cancel job: ${await errorText(response)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error canceling job: ${errorMessage}`);
        }
    }

    async downloadJob(treeItem: JobTreeItem | undefined): Promise<void> {
        if (!treeItem || !treeItem.job) {
            vscode.window.showWarningMessage('No job selected');
            return;
        }
        const id = treeItem.job.id;
        try {
            const response = await this.apiClient.fetch(`/scriptlet/job/download?id=${id}`);
            if (response.ok) {
                const blob = await response.blob();
                const defaultUri = vscode.Uri.file(`${id}.zip`);
                const uri = await vscode.window.showSaveDialog({
                    defaultUri,
                    filters: { 'ZIP Files': ['zip'] }
                });
                if (uri) {
                    await vscode.workspace.fs.writeFile(uri, new Uint8Array(await blob.arrayBuffer()));
                    vscode.window.showInformationMessage(`Downloaded workspace for job ${id} to ${uri.fsPath}`);
                }
            } else {
                vscode.window.showErrorMessage(`Failed to download job: ${await errorText(response)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error downloading job: ${errorMessage}`);
        }
    }

    async deleteJob(treeItem: JobTreeItem | undefined): Promise<void> {
        if (!treeItem || !treeItem.job) {
            vscode.window.showWarningMessage('No job selected');
            return;
        }
        const id = treeItem.job.id;
        const confirm = await vscode.window.showWarningMessage(
            `Delete job ${id}? This cannot be undone.`,
            { modal: true },
            'Delete'
        );
        if (confirm !== 'Delete') return;
        try {
            const response = await this.apiClient.fetch(`/scriptlet/job/item?id=${id}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                if (this.jobsTreeProvider) {
                    this.jobsTreeProvider.refresh();
                }
                vscode.window.showInformationMessage(`Deleted job ${id}`);
            } else {
                vscode.window.showErrorMessage(`Failed to delete job: ${await errorText(response)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error deleting job: ${errorMessage}`);
        }
    }

    async viewJob(treeItem: JobTreeItem | undefined): Promise<void> {
        if (!treeItem || !treeItem.job) {
            vscode.window.showWarningMessage('No job selected');
            return;
        }
        const id = treeItem.job.id;
        const key = id; // Use jobId as key
        if (this.activeJobs.has(key)) {
            vscode.window.showWarningMessage('Job details already being viewed');
            return;
        }
        this.jobViewOutputChannel.clear();
        this.jobViewOutputChannel.show(true);
        const entry = { interval: null as unknown as NodeJS.Timeout, jobId: id, prevStdoutLen: 0, prevStderrLen: 0 };
        this.activeJobs.set(key, entry);
        this.jobViewOutputChannel.appendLine(`Viewing job ${id} details...\n`);
        entry.interval = setInterval(async () => {
            try {
                const jobResponse = await this.apiClient.fetch(`/scriptlet/job/item?id=${id}`);
                if (jobResponse.ok) {
                    const jobData = await jobResponse.json() as unknown;
                    const jobObj = jobData as { stdout?: string; stderr?: string; state: string };
                    const active = this.activeJobs.get(key);
                    if (active) {
                        appendIncrementalOutput(this.jobViewOutputChannel, jobObj, active);
                        this.jobViewOutputChannel.appendLine(`\n--- State: ${jobObj.state} ---`);
                        if (['done', 'failed', 'cancelled', 'remove'].includes(jobObj.state)) {
                            clearInterval(entry.interval);
                            this.activeJobs.delete(key);
                            this.jobViewOutputChannel.appendLine('\n--- Job ended ---');
                        }
                    }
                } else if (jobResponse.status === 404) {
                    clearInterval(entry.interval);
                    this.activeJobs.delete(key);
                    this.jobViewOutputChannel.appendLine('\n--- Job no longer exists ---');
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                this.jobViewOutputChannel.appendLine(`Error polling job: ${errorMessage}`);
            }
        }, 1000);
        vscode.window.showInformationMessage(`Viewing details for job ${id}`);
    }

    private async performBulkAction(action: 'cancel' | 'delete', filterStates: string[]): Promise<void> {
        const jobs = await this.fetchJobs();
        const jobsToAct = action === 'cancel'
            ? jobs.filter(j => !['done', 'failed', 'cancelled'].includes(j.state))
            : jobs.filter(j => filterStates.includes(j.state));
        if (jobsToAct.length === 0) {
            vscode.window.showInformationMessage(`No jobs to ${action} matching criteria.`);
            return;
        }
        const confirmMsg = `Perform ${action} on ${jobsToAct.length} job(s)?`;
        const confirm = await vscode.window.showWarningMessage(confirmMsg, { modal: true }, 'Yes');
        if (confirm !== 'Yes') return;
        let successCount = 0;
        let errorCount = 0;
        let finishedCount = 0;
        for (const job of jobsToAct) {
            try {
                let response;
                if (action === 'cancel') {
                    response = await this.apiClient.fetch(`/scriptlet/job/cancel?id=${job.id}`);
                } else {
                    response = await this.apiClient.fetch(`/scriptlet/job/item?id=${job.id}`, { method: 'DELETE' });
                }
                if (response.ok) {
                    successCount++;
                } else if (action === 'cancel' && response.status === 409) {
                    // Finished since the list was fetched: nothing to cancel.
                    finishedCount++;
                } else {
                    errorCount++;
                }
            } catch {
                errorCount++;
            }
        }
        if (this.jobsTreeProvider) {
            this.jobsTreeProvider.refresh();
        }
        const finished = finishedCount > 0 ? `, ${finishedCount} already finished` : '';
        vscode.window.showInformationMessage(`Bulk ${action}: ${successCount} successful, ${errorCount} failed${finished}.`);
    }

    async cancelAllJobs(): Promise<void> {
        await this.performBulkAction('cancel', []); // Filter excludes done/failed/cancelled
    }

    async deleteAllDone(): Promise<void> {
        await this.performBulkAction('delete', ['done', 'cancelled']);
    }

    async deleteAllFailed(): Promise<void> {
        await this.performBulkAction('delete', ['failed', 'killed']);
    }

    public dispose(): void {
        this.activeJobs.forEach(({ interval }) => clearInterval(interval));
        this.activeJobs.clear();
        this.jobViewOutputChannel.dispose();
    }
}
