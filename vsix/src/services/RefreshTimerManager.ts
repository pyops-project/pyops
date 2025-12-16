import * as vscode from 'vscode';
import { ManagerTreeDataProvider } from '../managerTreeProvider';
import { JobsTreeDataProvider } from '../jobsTreeProvider';
import { hasValidConfig } from '../utils';

export class RefreshTimerManager {
    private managerTimer?: NodeJS.Timeout;
    private monitorTimer?: NodeJS.Timeout;

    constructor(
        private managerTreeProvider: ManagerTreeDataProvider,
        private jobsTreeProvider: JobsTreeDataProvider,
    ) {}

    ensureRunning(): void {
        if (!hasValidConfig()) return;

        const autoRefreshInterval = vscode.workspace.getConfiguration('pyops').get('autoRefreshInterval', 10) * 1000;

        if (!this.managerTimer) {
            this.managerTimer = setInterval(() => {
                this.managerTreeProvider.refresh();
            }, autoRefreshInterval);
        }

        if (!this.monitorTimer) {
            this.monitorTimer = setInterval(() => {
                this.jobsTreeProvider.refresh();
            }, autoRefreshInterval);
        }
    }

    stopIfRunning(): void {
        if (this.managerTimer) {
            clearInterval(this.managerTimer);
            this.managerTimer = undefined;
        }
        if (this.monitorTimer) {
            clearInterval(this.monitorTimer);
            this.monitorTimer = undefined;
        }
    }

    dispose(): void {
        this.stopIfRunning();
    }
}
