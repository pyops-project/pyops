import * as vscode from 'vscode';
import { TreeDataProvider, TreeItem, TreeItemCollapsibleState, EventEmitter, ThemeIcon } from 'vscode';

interface Job {
    id: string;
    start: string;
    end: string;
    kind: string;
    state: string;
    detail?: string;
    param?: string;
}

interface JobTreeItem extends TreeItem {
    job?: Job;
}

class JobsTreeDataProvider implements TreeDataProvider<JobTreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<JobTreeItem | undefined | null | void> = new EventEmitter<JobTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<JobTreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    private _context: vscode.ExtensionContext;
    private _jobs: Job[] = [];
    private _loading: boolean = false;

    constructor(context: vscode.ExtensionContext) {
        this._context = context;
        this.refresh();
    }

    getTreeItem(element: JobTreeItem): TreeItem {
        return element;
    }

    getChildren(element?: JobTreeItem): JobTreeItem[] {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
            return [new TreeItem('No Workspace Open', TreeItemCollapsibleState.None)];
        }

        if (element) {
            // No children for job items
            return [];
        } else {
            // Root level
            if (this._loading) {
                const loadingItem = new TreeItem('Loading Jobs...', TreeItemCollapsibleState.None);
                loadingItem.contextValue = 'loading';
                return [loadingItem];
            } else if (this._jobs.length === 0) {
                const emptyItem = new TreeItem('No Jobs Available', TreeItemCollapsibleState.None);
                emptyItem.contextValue = 'empty';
                return [emptyItem];
            }

            const jobItems: JobTreeItem[] = this._jobs.map((job: Job) => {
                const label = job.id;
                const description = `${job.state} - ${job.kind}`;
                const tooltip = `${job.state}: ${job.detail || ''}\nStart: ${job.start}\nEnd: ${job.end}\nParam: ${job.param || ''}`;
                const treeItem = new TreeItem(label, TreeItemCollapsibleState.None) as JobTreeItem;
                treeItem.description = description;
                treeItem.tooltip = tooltip;
                treeItem.contextValue = `pyopsJob-${job.state}`;
                treeItem.id = job.id;
                treeItem.job = job;

                // Icon based on state
                let icon: ThemeIcon | undefined;
                switch (job.state) {
                    case 'running':
                        icon = new ThemeIcon('loading~spin');
                        break;
                    case 'done':
                        icon = new ThemeIcon('check');
                        break;
                    case 'failed':
                    case 'canceled':
                        icon = new ThemeIcon('close');
                        break;
                    default:
                        icon = new ThemeIcon('symbol-method');
                }
                treeItem.iconPath = icon;

                return treeItem;
            });

            // Sort by start time descending (most recent first)
            jobItems.sort((a, b) => {
                const jobA = a.job!;
                const jobB = b.job!;
                return new Date(jobB.start).getTime() - new Date(jobA.start).getTime();
            });

            return jobItems;
        }
    }

    private async fetchJobs(): Promise<Job[]> {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
        const username = pyopsConfig.get('username') as string;
        const password = this._context.workspaceState.get('pyops.password') as string;

        const headers: Record<string, string> = {};
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }

        try {
            const response = await fetch(`${serverUrl}/scriptlet/job/list`, { headers });
            if (response.ok) {
                return await response.json() as Job[];
            } else {
                vscode.window.showErrorMessage(`Failed to fetch jobs: ${response.statusText}`);
                return [];
            }
        } catch (error: any) {
            vscode.window.showErrorMessage(`Error fetching jobs: ${error.message}`);
            return [];
        }
    }

    refresh(): void {
        this._loading = true;
        this._onDidChangeTreeData.fire();

        this.fetchJobs().then((jobs) => {
            this._jobs = jobs;
            this._loading = false;
            this._onDidChangeTreeData.fire();
        });
    }
}

export { JobsTreeDataProvider, Job, JobTreeItem };
