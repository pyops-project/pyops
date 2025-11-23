import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { StatusBarAlignment, StatusBarItem, window as vscodeWindow, TreeDataProvider, TreeItem, commands, Uri, QuickPickItem, InputBoxOptions, TextDocumentContentProvider, languages, TextDocument, env, workspace } from 'vscode';
import { JobsTreeDataProvider, JobTreeItem, Job } from './jobsTreeProvider';

interface ScriptletTreeItem extends TreeItem {
    set?: string;
    scriptletName?: string;
    scriptletType?: string;
}

class RemoteContentProvider implements TextDocumentContentProvider {
    private _onDidChange = new vscode.EventEmitter<vscode.Uri>();

    get onDidChange(): vscode.Event<vscode.Uri> {
        return this._onDidChange.event;
    }

    constructor(private _context: vscode.ExtensionContext) {}

    public provideTextDocumentContent(uri: vscode.Uri): vscode.ProviderResult<string> {
        if (uri.fragment) {
            try {
                return decodeURIComponent(uri.fragment);
            } catch (e: unknown) {
                const errorMessage = e instanceof Error ? e.message : String(e);
                return `Error decoding content: ${errorMessage}`;
            }
        }
        return "No content provided";
    }
}


let outputChannel: vscode.OutputChannel;
let statusBarItem: StatusBarItem;
let jobOutputChannel: vscode.OutputChannel;
let jobErrOutputChannel: vscode.OutputChannel;
const activeJobs = new Map<string, { interval: NodeJS.Timeout; jobId: string; prevStdoutLen: number; prevStderrLen: number; isView?: boolean }>(); // key: jobId for view/debug, value: {interval, jobId, prev lengths, isView}

let jobViewOutputChannel: vscode.OutputChannel;
let jobsTreeProvider: JobsTreeDataProvider | undefined;

function inferExt(type: string): string {
    switch (type) {
        case 'task':
        case 'library':
        case 'controller':
            return 'py';
        case 'view':
            return 'html';
        case 'markdown':
            return 'md';
        default:
            return '';
    }
}


function hasValidConfig(context: vscode.ExtensionContext): boolean {
    const pyopsConfig = vscode.workspace.getConfiguration('pyops');
    const serverUrl = pyopsConfig.get('serverUrl') as string || '';
    const username = pyopsConfig.get('username') as string || '';
    const password = context.workspaceState.get('pyops.password') as string || '';
    return !!(serverUrl && username && password);
}


class ManagerTreeDataProvider implements TreeDataProvider<TreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<TreeItem | undefined | null | void> = new vscode.EventEmitter<TreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<TreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    private _context: vscode.ExtensionContext;
    private _scriptlets: unknown[] = [];
    private _syncStatus: Map<string, boolean> = new Map();

    constructor(context: vscode.ExtensionContext) {
        this._context = context;
        this.refresh();
    }

    getTreeItem(element: TreeItem): TreeItem {
        return element;
    }

    async getChildren(element?: TreeItem): Promise<TreeItem[]> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
            return [new TreeItem('No Workspace Open', vscode.TreeItemCollapsibleState.None)];
        }

        const isValidConfig = hasValidConfig(this._context);
        if (!isValidConfig && !element) {
            const msgItem = new TreeItem('No PyOps configuration set. Please configure serverUrl, username, and password in settings.', vscode.TreeItemCollapsibleState.None);
            msgItem.contextValue = 'noConfig';
            msgItem.iconPath = new vscode.ThemeIcon('warning');
            msgItem.tooltip = 'Open settings (Ctrl+,) and set PyOps configuration to connect.';
            return [msgItem];
        }

        if (element) {
            if (element.contextValue === 'commands') {
                // Children of commands node
                const createItem = new TreeItem('Create Scriptlet', vscode.TreeItemCollapsibleState.None);
                createItem.command = { command: 'pyops.createScriptlet', title: 'Create Scriptlet' };
                const refreshItem = new TreeItem('Refresh', vscode.TreeItemCollapsibleState.None);
                refreshItem.command = { command: 'pyops.refreshManager', title: 'Refresh' };
                return [createItem, refreshItem];
            } else if (element.contextValue?.startsWith('pyopsSet')) {
                // Children of set: items (2-level: set > name (type))
                const set = element.label as string;
                const setItems = this._scriptlets.filter((s: unknown) => (s as { set: string }).set === set);
                const itemTrees: TreeItem[] = setItems.map((item: unknown) => {
                    const itemObj = item as { name: string; type: string };
                    const label = `${itemObj.name} (${itemObj.type})`;
                    const ext = inferExt(itemObj.type);
                    const key = `${set}-${itemObj.name}-${itemObj.type}`;
                    const isSynced = this._syncStatus.get(key) ?? true;
                    const treeItem = new TreeItem(label, vscode.TreeItemCollapsibleState.None) as ScriptletTreeItem;
                    treeItem.description = ext ? `.${ext}` : '';
                    treeItem.contextValue = `pyopsItem-${itemObj.type}`;
                    treeItem.id = key;
                    treeItem.iconPath = isSynced ? new vscode.ThemeIcon('check') : new vscode.ThemeIcon('warning');
                    treeItem.tooltip = `Set: ${set}, Name: ${itemObj.name}, Type: ${itemObj.type}, Sync: ${isSynced ? 'Synced' : 'Unsynced'}`;
                    treeItem.set = set;
                    treeItem.scriptletName = itemObj.name;
                    treeItem.scriptletType = itemObj.type;
                    treeItem.command = { command: 'pyops.openLocalFile', title: 'Open Local File', arguments: [treeItem] };
                    return treeItem;
                });
                return itemTrees.sort((a, b) => (a.label as string).localeCompare(b.label as string));
            }
            return [];
        } else {
            // Root: sets only (commands via toolbar)
            if (this._scriptlets.length === 0) {
                if (isValidConfig) {
                    await this.fetchScriptlets();
                    await this.downloadScriptlets(this._scriptlets);
                }
                if (this._scriptlets.length === 0) {
                    return [new TreeItem('No scriptlets available', vscode.TreeItemCollapsibleState.None)];
                }
            }

            const sets = [...new Set(this._scriptlets.map((s: unknown) => (s as { set: string }).set))].sort();
            const setItems: TreeItem[] = sets.map(g => {
                const gi = new TreeItem(g, vscode.TreeItemCollapsibleState.Collapsed);
                gi.contextValue = 'pyopsSet';
                gi.iconPath = new vscode.ThemeIcon('folder');
                gi.id = `set-${g}`;
                return gi;
            });

            return setItems;
        }
    }

    private async fetchScriptlets(): Promise<unknown[]> {
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
            const response = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
            if (response.ok) {
                return await response.json() as unknown[];
            } else {
                vscode.window.showErrorMessage(`Failed to fetch scriptlets: ${response.statusText}`);
                return [];
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error fetching scriptlets: ${errorMessage}`);
            return [];
        }
    }

    private async downloadScriptlets(scriptlets: unknown[]): Promise<void> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) return;

        this._syncStatus.clear();

        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
        const username = pyopsConfig.get('username') as string;
        const password = this._context.workspaceState.get('pyops.password') as string;

        const headers: Record<string, string> = {};
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }

        const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const baseLocalPath = path.join(workspaceRoot, localPyopsDir);

        const downloadPromises = scriptlets.map(async (s: unknown) => {
            const sObj = s as { set: string; name: string; type: string };
            const key = `${sObj.set}-${sObj.name}-${sObj.type}`;
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${sObj.set}&name=${sObj.name}`, { headers });
                if (response.ok) {
                    const responseData = await response.json();
                    const remoteCode = (responseData as { code: string }).code;
                    const ext = inferExt(sObj.type);
                    const dirPath = path.join(baseLocalPath, sObj.set, sObj.type);
                    if (!fs.existsSync(dirPath)) {
                        fs.mkdirSync(dirPath, { recursive: true });
                    }
                    const localPath = path.join(dirPath, `${sObj.name}.${ext}`);
                    let isSynced = true;
                    if (fs.existsSync(localPath)) {
                        const localCode = fs.readFileSync(localPath, 'utf8');
                        isSynced = localCode.trim() === remoteCode.trim();
                    }
                    if (!fs.existsSync(localPath)) {
                        fs.writeFileSync(localPath, remoteCode);
                    }
                    this._syncStatus.set(key, isSynced);
                } else {
                    this._syncStatus.set(key, false);
                    console.error(`Failed to fetch ${sObj.set}::${sObj.name}: ${response.statusText}`);
                }
            } catch (error: unknown) {
                this._syncStatus.set(key, false);
                const errorMessage = error instanceof Error ? error.message : String(error);
                console.error(`Error downloading ${sObj.set}::${sObj.name}:`, errorMessage);
            }
        });

        await Promise.all(downloadPromises);
    }

    private getExcludedSets(): string[] {
        return vscode.workspace.getConfiguration('pyops').get('excludedSets', []) as string[];
    }

    async refresh(): Promise<void> {
        const isValidConfig = hasValidConfig(this._context);
        if (!isValidConfig) {
            this._scriptlets = [];
            this._onDidChangeTreeData.fire();
            return;
        }

        const excludedSets = this.getExcludedSets();
        const scriptlets = await this.fetchScriptlets();
        this._scriptlets = scriptlets.filter((s: unknown) => !excludedSets.includes((s as { set: string }).set));
        if (this._scriptlets.length > 0) {
            await this.downloadScriptlets(this._scriptlets);
        }
        this._onDidChangeTreeData.fire();
    }
}

export async function activate(context: vscode.ExtensionContext) {
    outputChannel = vscodeWindow.createOutputChannel('PyOps');
    outputChannel.appendLine('PyOps extension activating...');

    try {

        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const username = pyopsConfig.get('username') as string;
        let password = context.workspaceState.get('pyops.password') as string | undefined;
        if (username && !password) {
            password = await vscode.window.showInputBox({
                prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
                password: true
            });
            if (password) {
                await context.workspaceState.update('pyops.password', password);
                outputChannel.appendLine('Auth password stored in workspace state');
            } else {
                outputChannel.appendLine('No auth password provided; Basic Auth may fail');
            }
        } else if (username && password) {
            outputChannel.appendLine('Auth credentials found in workspace state');
        }

        // Register remote content provider for readonly remote diffs
        const remoteProvider = new RemoteContentProvider(context);
        context.subscriptions.push(vscode.workspace.registerTextDocumentContentProvider('pyops-remote', remoteProvider));

        // Set language mode for remote diff documents
        context.subscriptions.push(vscode.workspace.onDidOpenTextDocument((document: TextDocument) => {
            if (document.uri.scheme === 'pyops-remote') {
                const params = new URLSearchParams(document.uri.query);
                const type = params.get('type');
                let languageId: string | undefined;
                switch (type) {
                    case 'task':
                    case 'library':
                    case 'controller':
                        languageId = 'python';
                        break;
                    case 'view':
                        languageId = 'html';
                        break;
                    case 'markdown':
                        languageId = 'markdown';
                        break;
                }
                if (languageId) {
                    languages.setTextDocumentLanguage(document, languageId);
                }
            }
        }));

        // Create status bar item for connection state
        statusBarItem = vscodeWindow.createStatusBarItem(StatusBarAlignment.Right, 100);
        statusBarItem.command = 'pyops.refreshConnectionStatus';
        context.subscriptions.push(statusBarItem);

        // Function to check connection and update status
        async function updateConnectionStatus() {
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            let hasValidConfig = false;
            if (!serverUrl || !username || !password) {
                statusBarItem.text = '$(circle-slash) PyOps: No Config';
                statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                statusBarItem.tooltip = 'No valid PyOps configuration. Open settings (Ctrl+,) to configure serverUrl, username, and password.';
                outputChannel.appendLine('Status: No config available');
            } else {
                try {
                    const url = new URL('/', serverUrl).toString();
                    const headers: Record<string, string> = {};
                    const auth = Buffer.from(`${username}:${password}`).toString('base64');
                    headers['Authorization'] = `Basic ${auth}`;

                    const response = await fetch(url, { headers });
                    if (response.ok) {
                        statusBarItem.text = `$(check) PyOps: Connected`;
                        statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
                        statusBarItem.tooltip = 'PyOps connected successfully.';
                        hasValidConfig = true;
                        outputChannel.appendLine(`Status: Connected to ${serverUrl}`);
                    } else {
                        statusBarItem.text = `$(circle-slash) PyOps: Failed (${response.status})`;
                        statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                        statusBarItem.tooltip = `Connection failed (${response.status}). Check server, credentials, and network.`;
                        outputChannel.appendLine(`Status: Failed to connect to ${serverUrl} (${response.status})`);
                    }
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : String(error);
                    statusBarItem.text = `$(circle-slash) PyOps: Error (${errorMessage})`;
                    statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                    statusBarItem.tooltip = `Error connecting to server: ${errorMessage}. Check configuration and network.`;
                    outputChannel.appendLine(`Status: Error connecting to server: ${errorMessage}`);
                }
            }
            await vscode.commands.executeCommand('setContext', 'pyops.hasValidConfig', hasValidConfig);
            statusBarItem.show();
        }

        // Initial status update
        await updateConnectionStatus();

        // Update on config changes
        context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(async (e) => {
            if (e.affectsConfiguration('pyops')) {
                outputChannel.appendLine('Config changed, updating status...');
                await updateConnectionStatus();
                if (managerTreeProvider) managerTreeProvider.refresh();
                if (jobsTreeProvider) jobsTreeProvider.refresh();
                // Recreate timers if config validity changed
                const isValidConfig = hasValidConfig(context);
                if (isValidConfig) {
                    if (!managerRefreshTimer) {
                        const autoRefreshInterval = vscode.workspace.getConfiguration('pyops').get('autoRefreshInterval', 10) * 1000;
                        managerRefreshTimer = setInterval(() => {
                            if (managerTreeProvider) managerTreeProvider.refresh();
                        }, autoRefreshInterval);
                    }
                    if (!monitorRefreshTimer) {
                        const autoRefreshInterval = vscode.workspace.getConfiguration('pyops').get('autoRefreshInterval', 10) * 1000;
                        monitorRefreshTimer = setInterval(() => {
                            if (jobsTreeProvider) jobsTreeProvider.refresh();
                        }, autoRefreshInterval);
                    }
                } else {
                    if (managerRefreshTimer) {
                        clearInterval(managerRefreshTimer);
                        managerRefreshTimer = undefined;
                    }
                    if (monitorRefreshTimer) {
                        clearInterval(monitorRefreshTimer);
                        monitorRefreshTimer = undefined;
                    }
                }
            }
        }));

        // Register command to refresh status
        const refreshStatusDisposable = vscode.commands.registerCommand('pyops.refreshConnectionStatus', async () => {
            outputChannel.appendLine('Manual status refresh triggered');
            await updateConnectionStatus();
        });
        context.subscriptions.push(refreshStatusDisposable);

    // Register tree view for manager
    const managerTreeProvider = new ManagerTreeDataProvider(context);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-manager', managerTreeProvider));

    let managerRefreshTimer: NodeJS.Timeout | undefined;
    let monitorRefreshTimer: NodeJS.Timeout | undefined;

    // Initial timer setup
    const isValidConfigInitial = hasValidConfig(context);
    const autoRefreshInterval = vscode.workspace.getConfiguration('pyops').get('autoRefreshInterval', 10) * 1000;
    if (isValidConfigInitial) {
        managerRefreshTimer = setInterval(() => {
            if (managerTreeProvider) managerTreeProvider.refresh();
        }, autoRefreshInterval);
        monitorRefreshTimer = setInterval(() => {
            if (jobsTreeProvider) jobsTreeProvider.refresh();
        }, autoRefreshInterval);
    }
    context.subscriptions.push({
        dispose: () => {
            if (managerRefreshTimer) clearInterval(managerRefreshTimer);
            if (monitorRefreshTimer) clearInterval(monitorRefreshTimer);
        }
    });

    // Register native tree view for monitor (repurposing existing ID)
    jobsTreeProvider = new JobsTreeDataProvider(context);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-monitor', jobsTreeProvider));

        // Register commands
        context.subscriptions.push(vscode.commands.registerCommand('pyops.refreshManager', async () => {
            managerTreeProvider.refresh();
            vscode.window.showInformationMessage('PyOps Manager refreshed');
        }));

        // Monitor commands
        context.subscriptions.push(vscode.commands.registerCommand('pyops.refreshMonitor', async () => {
            if (jobsTreeProvider) {
                jobsTreeProvider.refresh();
                vscode.window.showInformationMessage('PyOps Monitor refreshed');
            } else {
                vscode.window.showWarningMessage('Monitor provider not initialized');
            }
        }));

        // Helper function to get headers
        async function getHeaders(): Promise<Record<string, string>> {
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;
            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }
            return headers;
        }

        // Helper function to fetch jobs (public for provider, but for commands)
        async function fetchJobs(): Promise<Job[]> {
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const headers = await getHeaders();
            try {
                const response = await fetch(`${serverUrl}/scriptlet/job/list`, { headers });
                if (response.ok) {
                    return await response.json() as Job[];
                } else {
                    vscode.window.showErrorMessage(`Failed to fetch jobs: ${response.statusText}`);
                    return [];
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error fetching jobs: ${errorMessage}`);
                return [];
            }
        }

        // Copy Job Param
        context.subscriptions.push(vscode.commands.registerCommand('pyops.copyJobParam', async (treeItem?: JobTreeItem) => {
            if (!treeItem || !treeItem.job) {
                vscode.window.showWarningMessage('No job selected');
                return;
            }
            const param = treeItem.job.param || '';
            await env.clipboard.writeText(param);
            vscode.window.showInformationMessage(`Copied param for job ${treeItem.job.id}`);
        }));

        // Cancel Job
        context.subscriptions.push(vscode.commands.registerCommand('pyops.cancelJob', async (treeItem?: JobTreeItem) => {
            if (!treeItem || !treeItem.job) {
                vscode.window.showWarningMessage('No job selected');
                return;
            }
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const id = treeItem.job.id;
            const headers = await getHeaders();
            try {
                const response = await fetch(`${serverUrl}/scriptlet/job/cancel?id=${id}`, { headers });
                if (response.ok) {
                    if (jobsTreeProvider) {
                        jobsTreeProvider.refresh();
                    }
                    vscode.window.showInformationMessage(`Canceled job ${id}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to cancel job: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error canceling job: ${errorMessage}`);
            }
        }));

        // Download Job
        context.subscriptions.push(vscode.commands.registerCommand('pyops.downloadJob', async (treeItem?: JobTreeItem) => {
            if (!treeItem || !treeItem.job) {
                vscode.window.showWarningMessage('No job selected');
                return;
            }
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const id = treeItem.job.id;
            const headers = await getHeaders();
            try {
                const response = await fetch(`${serverUrl}/scriptlet/job/download?id=${id}`, { headers });
                if (response.ok) {
                    const blob = await response.blob();
                    const defaultUri = vscode.Uri.file(`${id}.zip`);
                    const uri = await vscode.window.showSaveDialog({
                        defaultUri,
                        filters: { 'ZIP Files': ['zip'] }
                    });
                    if (uri) {
                        await workspace.fs.writeFile(uri, new Uint8Array(await blob.arrayBuffer()));
                        vscode.window.showInformationMessage(`Downloaded workspace for job ${id} to ${uri.fsPath}`);
                    }
                } else {
                    vscode.window.showErrorMessage(`Failed to download job: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error downloading job: ${errorMessage}`);
            }
        }));

        // Delete Job
        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteJob', async (treeItem?: JobTreeItem) => {
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

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const headers = await getHeaders();
            try {
                const response = await fetch(`${serverUrl}/scriptlet/job/item?id=${id}`, {
                    method: 'DELETE',
                    headers
                });
                if (response.ok) {
                    if (jobsTreeProvider) {
                        jobsTreeProvider.refresh();
                    }
                    vscode.window.showInformationMessage(`Deleted job ${id}`);
                } else {
                    const err = await response.text();
                    vscode.window.showErrorMessage(`Failed to delete job: ${err}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error deleting job: ${errorMessage}`);
            }
        }));

        // View Job Details (poll and show in output)
        jobViewOutputChannel = vscodeWindow.createOutputChannel('PyOps Job View');
        context.subscriptions.push(jobViewOutputChannel);

        context.subscriptions.push(vscode.commands.registerCommand('pyops.viewJob', async (treeItem?: JobTreeItem) => {
            if (!treeItem || !treeItem.job) {
                vscode.window.showWarningMessage('No job selected');
                return;
            }
            const id = treeItem.job.id;
            const key = id; // Use jobId as key
            if (activeJobs.has(key)) {
                vscode.window.showWarningMessage('Job details already being viewed');
                return;
            }

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const headers = await getHeaders();

            jobViewOutputChannel.clear();
            jobViewOutputChannel.show(true);

            const pollInterval = setInterval(async () => {
                try {
                    const jobResponse = await fetch(`${serverUrl}/scriptlet/job/item?id=${id}`, { headers });
                    if (jobResponse.ok) {
                        const jobData = await jobResponse.json() as unknown;
                        const jobObj = jobData as { param?: string; stdout?: string; stderr?: string; state: string };
                        const active = activeJobs.get(key);
                        if (active) {
                            // Append param if available
                            if (jobObj.param && jobViewOutputChannel) {
                                jobViewOutputChannel.appendLine(`\n--- Parameters ---`);
                                jobViewOutputChannel.appendLine(jobObj.param);
                            }
                            // Append stdout
                            if (jobObj.stdout) {
                                const newOut = jobObj.stdout.slice(active.prevStdoutLen);
                                if (newOut.length > 0 && jobViewOutputChannel) {
                                    jobViewOutputChannel.appendLine(`[STDOUT]`);
                                    jobViewOutputChannel.append(newOut);
                                }
                                active.prevStdoutLen = jobObj.stdout.length;
                            }
                            // Append stderr
                            if (jobObj.stderr) {
                                const newErr = jobObj.stderr.slice(active.prevStderrLen);
                                if (newErr.length > 0 && jobViewOutputChannel) {
                                    jobViewOutputChannel.appendLine(`[STDERR]`);
                                    jobViewOutputChannel.append(newErr);
                                }
                                active.prevStderrLen = jobObj.stderr.length;
                            }
                            // Update state
                            jobViewOutputChannel.appendLine(`\n--- State: ${jobObj.state} ---`);
                            if (['done', 'failed', 'cancelled', 'remove'].includes(jobObj.state)) {
                                const active = activeJobs.get(key);
                                if (active && active.interval === pollInterval) {
                                    clearInterval(pollInterval);
                                    activeJobs.delete(key);
                                    jobViewOutputChannel.appendLine('\n--- Job ended ---');
                                }
                            }
                        }
                    } else if (jobResponse.status === 404) {
                        // Job deleted
                        const active = activeJobs.get(key);
                        if (active && active.interval === pollInterval) {
                            clearInterval(pollInterval);
                            activeJobs.delete(key);
                            jobViewOutputChannel.appendLine('\n--- Job no longer exists ---');
                        }
                    }
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : String(error);
                    jobViewOutputChannel.appendLine(`Error polling job: ${errorMessage}`);
                }
            }, 1000);

            activeJobs.set(key, { interval: pollInterval, jobId: id, prevStdoutLen: 0, prevStderrLen: 0, isView: true });
            jobViewOutputChannel.appendLine(`Viewing job ${id} details...\n`);
            vscode.window.showInformationMessage(`Viewing details for job ${id}`);
        }));

        // Bulk actions
        async function performBulkAction(action: 'cancel' | 'delete', filterStates: string[]) {
            const jobs = await fetchJobs();
            const jobsToAct = jobs.filter(j => filterStates.includes(j.state) || (action === 'cancel' && !['done', 'failed', 'cancelled'].includes(j.state)));
            if (jobsToAct.length === 0) {
                vscode.window.showInformationMessage(`No jobs to ${action} matching criteria.`);
                return;
            }
            const confirmMsg = `Perform ${action} on ${jobsToAct.length} job(s)?`;
            const confirm = await vscode.window.showWarningMessage(confirmMsg, { modal: true }, 'Yes');
            if (confirm !== 'Yes') return;

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const headers = await getHeaders();
            let successCount = 0;
            let errorCount = 0;

            for (const job of jobsToAct) {
                try {
                    let response;
                    if (action === 'cancel') {
                        response = await fetch(`${serverUrl}/scriptlet/job/cancel?id=${job.id}`, { headers });
                    } else {
                        response = await fetch(`${serverUrl}/scriptlet/job/item?id=${job.id}`, { method: 'DELETE', headers });
                    }
                    if (response.ok) {
                        successCount++;
                    } else {
                        errorCount++;
                    }
                } catch {
                    errorCount++;
                }
            }

            if (jobsTreeProvider) {
                jobsTreeProvider.refresh();
            }
            vscode.window.showInformationMessage(`Bulk ${action}: ${successCount} successful, ${errorCount} failed.`);
        }

        context.subscriptions.push(vscode.commands.registerCommand('pyops.cancelAllJobs', async () => {
            await performBulkAction('cancel', []); // Filter excludes done/failed/cancelled
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteAllDone', async () => {
            await performBulkAction('delete', ['done', 'cancelled']);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteAllFailed', async () => {
            await performBulkAction('delete', ['failed']);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.createScriptlet', async () => {
            const types: QuickPickItem[] = [
                { label: 'Task', description: 'Python task script' },
                { label: 'Library', description: 'Python library module' },
                { label: 'Controller', description: 'Python controller' },
                { label: 'View', description: 'HTML view template' },
                { label: 'Markdown', description: 'Markdown document (README only)' }
            ];
            const typePick = await vscode.window.showQuickPick(types, { placeHolder: 'Select scriptlet type' });
            if (!typePick) return;

            const typeLower = typePick.label.toLowerCase();

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            // Fetch existing sets
            outputChannel.appendLine(`[Create Scriptlet] Fetching existing sets...`);
            let scriptlets: unknown[] = [];
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
                outputChannel.appendLine(`[Create Scriptlet] Sets list fetch status: ${response.status} ${response.statusText}`);
                if (response.ok) {
                    scriptlets = await response.json() as unknown[];
                    const existingSets = [...new Set(scriptlets.map((s: unknown) => (s as { set: string }).set))].sort();
                    outputChannel.appendLine(`[Create Scriptlet] Found sets: ${JSON.stringify(existingSets)}`);
                    if (existingSets.length === 0) {
                        vscode.window.showErrorMessage('No existing sets found. Cannot create scriptlet without a set.');
                        outputChannel.appendLine(`[Create Scriptlet] No sets available.`);
                        return;
                    }

                    const setItems: QuickPickItem[] = existingSets.map(g => ({ label: g }));
                    const setPick = await vscode.window.showQuickPick(setItems, { placeHolder: 'Select set' });
                    if (!setPick) return;

                    const set = setPick.label;
                    outputChannel.appendLine(`[Create Scriptlet] Selected set: ${set}`);

                    // Fetch scriptlets for selected set to check names
                    const setScriptlets = scriptlets.filter((s: unknown) => (s as { set: string }).set === set);
                    const existingNames = new Set(setScriptlets.map((s: unknown) => (s as { name: string }).name));
                    outputChannel.appendLine(`[Create Scriptlet] Existing names in set '${set}': ${JSON.stringify(Array.from(existingNames))}`);

                    // Loop for unique name
                    let name: string | undefined;
                    while (!name) {
                        const nameInput: InputBoxOptions = { 
                            prompt: 'Enter unique name', 
                            validateInput: (value: string) => {
                                if (!value) return 'Name required';
                                if (existingNames.has(value)) return `Name '${value}' already exists in set '${set}'. Choose a different name.`;
                                return null;
                            }
                        };
                        name = await vscode.window.showInputBox(nameInput);
                        if (!name) return; // User cancelled
                        if (existingNames.has(name)) {
                            name = undefined; // Force loop
                            vscode.window.showWarningMessage(`Name '${name}' already exists. Please choose a different name.`);
                        }
                    }
                    outputChannel.appendLine(`[Create Scriptlet] Selected unique name: ${name}`);

                    if (typeLower === 'markdown' && name !== 'README') {
                        vscode.window.showErrorMessage('Markdown only allowed for README');
                        return;
                    }

                    const createHeaders: Record<string, string> = { 'Content-Type': 'application/json', ...headers };
                    const data = { set, name, type: typeLower };
                    outputChannel.appendLine(`[Create Scriptlet] Attempting to create ${typeLower} '${set}::${name}' with data: ${JSON.stringify(data)}`);
                    try {
                        const response = await fetch(`${serverUrl}/scriptlet/inventory/item`, {
                            method: 'PUT',
                            headers: createHeaders,
                            body: JSON.stringify(data)
                        });
                        outputChannel.appendLine(`[Create Scriptlet] Creation fetch status: ${response.status} ${response.statusText}`);
                        if (response.ok) {
                            const respText = await response.text();
                            outputChannel.appendLine(`[Create Scriptlet] Creation response body: ${respText}`);
                            // Verify creation
                            const verifyResponse = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
                            if (verifyResponse.ok) {
                                const updatedScriptlets = await verifyResponse.json() as unknown[];
                                const created = updatedScriptlets.some((s: unknown) => (s as { set: string; name: string; type: string }).set === set && (s as { set: string; name: string; type: string }).name === name && (s as { set: string; name: string; type: string }).type === typeLower);
                                if (created) {
                                    managerTreeProvider.refresh();
                                    vscode.window.showInformationMessage(`Created ${typeLower} ${set}::${name}`);
                                    outputChannel.appendLine(`[Create Scriptlet] Verified creation successful.`);
                                    // Open local editor
                                    const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                                    const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                                    const ext = inferExt(typeLower);
                                    const localPath = path.join(workspaceRoot, localPyopsDir, set, typeLower, `${name}.${ext}`);
                                    const localUri = Uri.file(localPath);
                                    await vscode.workspace.openTextDocument(localUri).then(doc => vscode.window.showTextDocument(doc));
                                } else {
                                    const errorMsg = `Creation response OK but scriptlet '${set}::${name}' not found after attempt. Server may not have created it.`;
                                    vscode.window.showErrorMessage(errorMsg);
                                    outputChannel.appendLine(`[Create Scriptlet] Verification failed: ${errorMsg}`);
                                }
                            } else {
                                outputChannel.appendLine(`[Create Scriptlet] Could not verify creation: ${verifyResponse.statusText}`);
                                managerTreeProvider.refresh();
                                vscode.window.showInformationMessage(`Created ${typeLower} ${set}::${name} (verification failed)`);
                            }
                        } else {
                            const err = await response.text();
                            const errorMsg = `Failed to create: ${err}`;
                            vscode.window.showErrorMessage(errorMsg);
                            outputChannel.appendLine(`[Create Scriptlet] Creation failed: ${errorMsg}`);
                        }
                    } catch (error: unknown) {
                        const errorMessage = error instanceof Error ? error.message : String(error);
                        const errorMsg = `Error creating scriptlet: ${errorMessage}`;
                        vscode.window.showErrorMessage(errorMsg);
                        outputChannel.appendLine(`[Create Scriptlet] Creation error: ${errorMsg}`);
                    }
                } else {
                    const errorMsg = `Failed to fetch sets: ${response.statusText}`;
                    vscode.window.showErrorMessage(errorMsg);
                    outputChannel.appendLine(`[Create Scriptlet] Sets fetch failed: ${errorMsg}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                const errorMsg = `Error fetching sets: ${errorMessage}`;
                vscode.window.showErrorMessage(errorMsg);
                outputChannel.appendLine(`[Create Scriptlet] Sets fetch error: ${errorMsg}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.resetLocalFromRemote', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers });
                if (response.ok) {
                    const responseData = await response.json();
                    const code = (responseData as { code: string }).code;
                    const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                    const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                    const ext = inferExt(type);
                    const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
                    const dirPath = path.dirname(localPath);
                    if (!fs.existsSync(dirPath)) {
                        fs.mkdirSync(dirPath, { recursive: true });
                    }
                    fs.writeFileSync(localPath, code);
                    vscode.window.showInformationMessage(`Reset local ${name}.${ext} from remote`);
                    managerTreeProvider.refresh();
                } else {
                    vscode.window.showErrorMessage(`Failed to fetch remote: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error resetting local: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.writeLocalToRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
            const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
            const ext = inferExt(type);
            const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
            if (!fs.existsSync(localPath)) {
                vscode.window.showErrorMessage('Local file not found');
                return;
            }
            const code = fs.readFileSync(localPath, 'utf8');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = { 'Content-Type': 'application/json' };
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            const data = { set, name, code };
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/draft`, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(data)
                });
                if (response.ok) {
                    vscode.window.showInformationMessage(`Wrote local changes to remote draft for ${name}`);
                    managerTreeProvider.refresh();
                } else {
                    vscode.window.showErrorMessage(`Failed to write draft: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error writing draft: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.saveRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const authHeaders: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                authHeaders['Authorization'] = `Basic ${auth}`;
            }

            // Check for unstaged changes (draft existence)
            const statusHeaders = { ...authHeaders };
            try {
                const statusResponse = await fetch(`${serverUrl}/scriptlet/inventory/status?set=${set}&name=${name}`, { headers: statusHeaders });
                if (!statusResponse.ok) {
                    vscode.window.showErrorMessage('Failed to check draft status');
                    return;
                }
                const statusData = await statusResponse.json();
                const hasUnstaged = (statusData as { has_unstaged: boolean }).has_unstaged;

                if (!hasUnstaged) {
                    vscode.window.showInformationMessage(`No remote draft to save for ${name}; no unstaged changes.`);
                    return;
                }

                // Fetch draft code and save (commit)
                const codeResponse = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers: statusHeaders });
                if (!codeResponse.ok) {
                    vscode.window.showErrorMessage('No remote draft to save');
                    return;
                }
                const codeResponseData = await codeResponse.json();
                const draftCode = (codeResponseData as { code: string }).code;

                const saveHeaders: Record<string, string> = { 'Content-Type': 'application/json', ...authHeaders };
                const data = { set, name, code: draftCode };
                const saveResponse = await fetch(`${serverUrl}/scriptlet/inventory/item`, {
                    method: 'POST',
                    headers: saveHeaders,
                    body: JSON.stringify(data)
                });
                if (saveResponse.ok) {
                    managerTreeProvider.refresh();
                    vscode.window.showInformationMessage(`Saved remote draft for ${name}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to save: ${saveResponse.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error saving draft: ${errorMessage}`);
            }
        }));

        // Job output channels
        jobOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Out');
        jobErrOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Err');
        context.subscriptions.push(jobOutputChannel, jobErrOutputChannel);

        context.subscriptions.push(vscode.commands.registerCommand('pyops.startDebugJob', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || treeItem.scriptletType !== 'task') return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const key = `${set}-${name}`;
            if (activeJobs.has(key)) {
                vscode.window.showWarningMessage('Debug job already running');
                return;
            }

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = { 'Content-Type': 'application/json' };
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            const data = { set, name, drop_done: false, param: '', debug: true };
            try {
                const response = await fetch(`${serverUrl}/scriptlet/job/item`, {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify(data)
                });
                if (response.ok) {
                    const responseData = await response.json();
                    const jobId = (responseData as { job: string }).job;
                    jobOutputChannel.clear();
                    jobErrOutputChannel.clear();
                    jobOutputChannel.show(true);
                    const pollInterval = setInterval(async () => {
                        const getHeaders: Record<string, string> = {};
                        if (username && password) {
                            const auth = Buffer.from(`${username}:${password}`).toString('base64');
                            getHeaders['Authorization'] = `Basic ${auth}`;
                        }
                        const jobResponse = await fetch(`${serverUrl}/scriptlet/job/item?id=${jobId}`, { headers: getHeaders });
                        if (jobResponse.ok) {
                            const jobData = await jobResponse.json() as unknown;
                            const jobObj = jobData as { stdout?: string; stderr?: string; state: string };
                            const active = activeJobs.get(key);
                            if (active) {
                                if (jobObj.stdout) {
                                    const newOut = jobObj.stdout.slice(active.prevStdoutLen);
                                    if (newOut.length > 0) {
                                        jobOutputChannel.append(newOut);
                                    }
                                    active.prevStdoutLen = jobObj.stdout.length;
                                }
                                if (jobObj.stderr) {
                                    const newErr = jobObj.stderr.slice(active.prevStderrLen);
                                    if (newErr.length > 0) {
                                        jobErrOutputChannel.append(newErr);
                                    }
                                    active.prevStderrLen = jobObj.stderr.length;
                                }
                                jobOutputChannel.show(true);
                                jobErrOutputChannel.show(true);
                            }
                            if (['done', 'failed', 'cancelled', 'remove'].includes(jobObj.state)) {
                                const active = activeJobs.get(key);
                                if (active && active.interval === pollInterval) {
                                    clearInterval(pollInterval);
                                    activeJobs.delete(key);
                                }
                            }
                        } else if (jobResponse.status === 404) {
                            // Job ended
                            const active = activeJobs.get(key);
                            if (active && active.interval === pollInterval) {
                                clearInterval(pollInterval);
                                activeJobs.delete(key);
                            }
                        }
                    }, 1000);
                    activeJobs.set(key, { interval: pollInterval, jobId, prevStdoutLen: 0, prevStderrLen: 0 });
                    vscode.window.showInformationMessage(`Started debug job ${jobId} for ${name}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to start debug: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error starting debug: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.stopDebugJob', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const key = `${set}-${name}`;
            const active = activeJobs.get(key);
            if (!active) {
                vscode.window.showWarningMessage('No active debug job');
                return;
            }

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            const jobId = active.jobId;
            try {
                const cancelResponse = await fetch(`${serverUrl}/scriptlet/job/cancel?id=${jobId}`, { headers });
                if (cancelResponse.ok) {
                    clearInterval(active.interval);
                    activeJobs.delete(key);
                    jobOutputChannel.appendLine('\n--- Job stopped ---\n');
                    vscode.window.showInformationMessage(`Stopped debug job ${jobId}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to stop debug: ${cancelResponse.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error stopping debug: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.diffLocalRemote', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers });
                if (response.ok) {
                const responseData = await response.json();
                const code = (responseData as { code: string }).code;
                const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                const ext = inferExt(type);
                const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
                if (!fs.existsSync(localPath)) {
                    vscode.window.showWarningMessage('Local file not found; create first');
                    return;
                }
                const encodedCode = encodeURIComponent(code);
                const remoteUri = vscode.Uri.parse(`pyops-remote:/${set}/${name}?type=${type}#${encodedCode}`);
                const localUri = vscode.Uri.file(localPath);
                await commands.executeCommand('vscode.diff', remoteUri, localUri, 'Remote vs Local');
                } else {
                    vscode.window.showErrorMessage(`Failed to fetch remote for diff: ${response.statusText}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error in diff: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteScriptlet', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = hasValidConfig(context);
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const confirm = await vscode.window.showWarningMessage(
                `Delete remote scriptlet ${set}::${name} (${type})? This cannot be undone.`,
                { modal: true },
                'Delete'
            );
            if (confirm !== 'Delete') return;

            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: Record<string, string> = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/item?set=${set}&name=${name}`, {
                    method: 'DELETE',
                    headers
                });
                if (response.ok) {
                    // Delete local file
                    const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                    const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                    const ext = inferExt(type);
                    const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
                    if (fs.existsSync(localPath)) {
                        fs.unlinkSync(localPath);
                    }

                    managerTreeProvider.refresh();
                    vscode.window.showInformationMessage(`Deleted ${set}::${name} (${type}) locally and remotely`);
                } else {
                    const err = await response.text();
                    vscode.window.showErrorMessage(`Failed to delete: ${err}`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Error deleting scriptlet: ${errorMessage}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.openLocalFile', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
            const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
            const ext = inferExt(type);
            const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
            if (!fs.existsSync(localPath)) {
                vscode.window.showWarningMessage(`Local file not found: ${localPath}. Run refresh to download.`);
                return;
            }
            const localUri = Uri.file(localPath);
            await vscode.workspace.openTextDocument(localUri).then(doc => vscode.window.showTextDocument(doc));
        }));

        outputChannel.appendLine('PyOps extension activated successfully');
        console.log('PyOps extension is now active');
    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorStack = error instanceof Error ? error.stack : undefined;
        outputChannel.appendLine(`Activation failed: ${errorMessage}`);
        if (errorStack) {
            outputChannel.appendLine(`Stack: ${errorStack}`);
        }
        outputChannel.show(true);
        console.error('PyOps activation error:', errorMessage);
        throw error; // Re-throw to show in VSCode
    }
}

export function deactivate() {
    if (statusBarItem) {
        statusBarItem.dispose();
    }
    if (outputChannel) {
        outputChannel.dispose();
    }
    if (jobOutputChannel) {
        jobOutputChannel.dispose();
    }
    if (jobErrOutputChannel) {
        jobErrOutputChannel.dispose();
    }
    if (jobViewOutputChannel) {
        jobViewOutputChannel.dispose();
    }
    activeJobs.forEach(({ interval }) => clearInterval(interval));
    activeJobs.clear();
}
