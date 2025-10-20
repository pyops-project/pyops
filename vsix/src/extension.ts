import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import * as os from 'os';
import { StatusBarAlignment, StatusBarItem, window as vscodeWindow, TreeDataProvider, TreeItem, TreeItemCollapsibleState, DiagnosticCollection, commands, Uri, ProgressLocation, QuickPickItem, InputBoxOptions, DiagnosticSeverity, TextDocumentContentProvider, EventEmitter, languages, TextDocument, env, workspace } from 'vscode';
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
            } catch (e: any) {
                return `Error decoding content: ${e.message}`;
            }
        }
        return "No content provided";
    }
}

class PyOpsWebviewViewProvider implements vscode.WebviewViewProvider {
    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _viewId: string,
        private readonly _title: string,
        private readonly _context: vscode.ExtensionContext
    ) {}

    public async resolveWebviewView(webviewView: vscode.WebviewView) {
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = await this._getHtmlForWebview(webviewView.webview, this._title);
    }

    private async _getHtmlForWebview(webview: vscode.Webview, title: string): Promise<string> {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
        const username = pyopsConfig.get('username') as string;
        const password = this._context.workspaceState.get('pyops.password') as string;

        let htmlPath: string;
        switch (title.toLowerCase()) {
            case 'manager':
                htmlPath = '/sites/scriptlet/manager.html';
                break;
            case 'scheduler':
                htmlPath = '/sites/scriptlet/scheduler.html';
                break;
            case 'monitor':
                htmlPath = '/sites/scriptlet/monitor.html';
                break;
            default:
                return '<h1>Unknown View</h1><p>Invalid title.</p>';
        }

        try {
            const url = new URL(htmlPath, serverUrl).toString();
            const headers: any = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            const response = await fetch(url, { headers });
            if (response.ok) {
                let html = await response.text();

                // Rewrite relative paths to absolute server URLs for CSS/JS
                html = html.replace(/href="\/files/g, `href="${serverUrl}/files`);
                html = html.replace(/src="\/files/g, `src="${serverUrl}/files`);

                // Enable scripts and CSP for webview
                webview.options = {
                    enableScripts: true,
                    localResourceRoots: [],
                    enableCommandUris: true
                };

                return html;
            } else {
                return `<h1>${title} View</h1><p>Failed to load from server: ${response.status} ${response.statusText}</p><p>Server: ${serverUrl}</p>`;
            }
        } catch (error: any) {
            console.error(`Error fetching ${title} view:`, error);
            return `<h1>${title} View</h1><p>Error loading from server: ${error.message}</p><p>Server: ${serverUrl}</p>`;
        }
    }
}

let outputChannel: vscode.OutputChannel;
let statusBarItem: StatusBarItem;
let configWatcher: vscode.FileSystemWatcher | undefined;
let jobOutputChannel: vscode.OutputChannel;
let jobErrOutputChannel: vscode.OutputChannel;
let activeJobs = new Map<string, { interval: NodeJS.Timeout; jobId: string; prevStdoutLen: number; prevStderrLen: number; isView?: boolean }>(); // key: jobId for view/debug, value: {interval, jobId, prev lengths, isView}

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

function getIconForType(type: string): vscode.ThemeIcon | undefined {
    switch (type) {
        case 'task':
            return new vscode.ThemeIcon('symbol-function');
        case 'library':
            return new vscode.ThemeIcon('symbol-module');
        case 'controller':
            return new vscode.ThemeIcon('symbol-class');
        case 'view':
            return new vscode.ThemeIcon('symbol-enum');
        case 'markdown':
            return new vscode.ThemeIcon('markdown');
        default:
            return undefined;
    }
}

class ManagerTreeDataProvider implements TreeDataProvider<TreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<TreeItem | undefined | null | void> = new vscode.EventEmitter<TreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<TreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    private _context: vscode.ExtensionContext;
    private _scriptlets: any[] = [];

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
                const setItems = this._scriptlets.filter((s: any) => s.set === set);
                const itemTrees: TreeItem[] = setItems.map((item: any) => {
                    const label = `${item.name} (${item.type})`;
                    const ext = inferExt(item.type);
                    const treeItem = new TreeItem(label, vscode.TreeItemCollapsibleState.None) as ScriptletTreeItem;
                    treeItem.description = ext ? `.${ext}` : '';
                    treeItem.contextValue = `pyopsItem-${item.type}`;
                    treeItem.id = `${set}-${item.name}-${item.type}`;
                    treeItem.iconPath = getIconForType(item.type);
                    treeItem.tooltip = `Set: ${set}, Name: ${item.name}, Type: ${item.type}`;
                    treeItem.set = set;
                    treeItem.scriptletName = item.name;
                    treeItem.scriptletType = item.type;
                    treeItem.command = { command: 'pyops.openLocalFile', title: 'Open Local File', arguments: [treeItem] };
                    return treeItem;
                });
                return itemTrees.sort((a, b) => (a.label as string).localeCompare(b.label as string));
            }
            return [];
        } else {
            // Root: sets only (commands via toolbar)
            if (this._scriptlets.length === 0) {
                return [new TreeItem('Loading...', vscode.TreeItemCollapsibleState.None)];
            }

            const sets = [...new Set(this._scriptlets.map((s: any) => s.set))].sort();
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

    private async fetchScriptlets(): Promise<any[]> {
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
                return await response.json() as any[];
            } else {
                vscode.window.showErrorMessage(`Failed to fetch scriptlets: ${response.statusText}`);
                return [];
            }
        } catch (error: any) {
            vscode.window.showErrorMessage(`Error fetching scriptlets: ${error.message}`);
            return [];
        }
    }

    private async downloadScriptlets(scriptlets: any[]): Promise<void> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) return;

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
        const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
        const baseLocalPath = path.join(workspaceRoot, localPyopsDir);

        const downloadPromises = scriptlets.map(async (s: any) => {
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${s.set}&name=${s.name}`, { headers });
                if (response.ok) {
                const responseData = await response.json();
                const code = (responseData as any).code;
                const ext = inferExt(s.type);
                const dirPath = path.join(baseLocalPath, s.set, s.type);
                if (!fs.existsSync(dirPath)) {
                    fs.mkdirSync(dirPath, { recursive: true });
                }
                const localPath = path.join(dirPath, `${s.name}.${ext}`);
                if (!fs.existsSync(localPath)) {
                    fs.writeFileSync(localPath, code);
                }
                }
            } catch (error: any) {
                console.error(`Failed to download ${s.set}::${s.name}:`, error);
            }
        });

        await Promise.all(downloadPromises);
    }

    refresh(): void {
        this.fetchScriptlets().then(async (scriptlets) => {
            this._scriptlets = scriptlets;
            this._onDidChangeTreeData.fire();
            if (scriptlets.length > 0) {
                await this.downloadScriptlets(scriptlets);
            }
        });
    }
}

export async function activate(context: vscode.ExtensionContext) {
    outputChannel = vscodeWindow.createOutputChannel('PyOps');
    outputChannel.appendLine('PyOps extension activating...');

    try {
        const extensionUri = context.extensionUri;

        // Load PyOps configuration from YAML
        let yamlConfig: Record<string, any> = {};
        if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
            const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
            outputChannel.appendLine(`Workspace root: ${workspaceRoot}`);
            const yamlPath = path.join(workspaceRoot, '.pyops.yaml');
            const systemUser = os.userInfo().username;
            const userYamlPath = path.join(workspaceRoot, `.pyops.${systemUser}.yaml`);

            // Load global YAML
            let globalConfig: Record<string, any> = {};
            if (fs.existsSync(yamlPath)) {
                try {
                    const yamlContent = fs.readFileSync(yamlPath, 'utf8');
                    outputChannel.appendLine(`Default YAML content length: ${yamlContent.length}`);
                    const parsed = yaml.load(yamlContent);
                    outputChannel.appendLine(`Default YAML parsed type: ${typeof parsed}`);
                    if (parsed && typeof parsed === 'object' && parsed !== null) {
                        globalConfig = parsed as Record<string, any>;
                        outputChannel.appendLine(`Loaded global config from ${yamlPath}`);
                    } else {
                        outputChannel.appendLine(`Invalid or empty global YAML at ${yamlPath}: parsed = ${JSON.stringify(parsed)}`);
                    }
                } catch (error: any) {
                    outputChannel.appendLine(`Error parsing global YAML: ${error.message}`);
                }
            }

            // Load and merge user-specific YAML if exists (user overrides global)
            let userConfig: Record<string, any> = {};
            if (fs.existsSync(userYamlPath)) {
                try {
                    const userYamlContent = fs.readFileSync(userYamlPath, 'utf8');
                    outputChannel.appendLine(`User YAML content length: ${userYamlContent.length}`);
                    const parsed = yaml.load(userYamlContent);
                    outputChannel.appendLine(`User YAML parsed type: ${typeof parsed}`);
                    if (parsed && typeof parsed === 'object' && parsed !== null) {
                        userConfig = parsed as Record<string, any>;
                        outputChannel.appendLine(`Loaded user-specific config from ${userYamlPath}`);
                    } else {
                        outputChannel.appendLine(`Invalid or empty user-specific YAML at ${userYamlPath}: parsed = ${JSON.stringify(parsed)}`);
                    }
                } catch (error: any) {
                    outputChannel.appendLine(`Error parsing user-specific YAML: ${error.message}`);
                }
            }

            yamlConfig = { ...globalConfig, ...userConfig };
            outputChannel.appendLine('Merged global and user configs');

            // Update VSCode configuration with YAML values
            if (Object.keys(yamlConfig).length > 0) {
                const pyopsConfig = vscode.workspace.getConfiguration('pyops');
                if (yamlConfig.serverUrl) {
                    await pyopsConfig.update('serverUrl', yamlConfig.serverUrl, vscode.ConfigurationTarget.Workspace);
                    outputChannel.appendLine(`Updated serverUrl: ${yamlConfig.serverUrl}`);
                }
                if (yamlConfig.user) {
                    await pyopsConfig.update('username', yamlConfig.user, vscode.ConfigurationTarget.Workspace);
                    outputChannel.appendLine(`Updated username from 'user': ${yamlConfig.user}`);
                }
                if (yamlConfig.timeout) {
                    await pyopsConfig.update('timeout', yamlConfig.timeout, vscode.ConfigurationTarget.Workspace);
                    outputChannel.appendLine(`Updated timeout: ${yamlConfig.timeout}`);
                }
                // Handle auth: use global/user auth setting, prompt/store password if http-basic
                const authType = yamlConfig.auth || 'none';
                if (authType === 'http-basic') {
                    let password = context.workspaceState.get('pyops.password') as string | undefined;
                    // Use 'key' from user config if available and no stored password
                    if (!password && userConfig.key) {
                        password = userConfig.key;
                        await context.workspaceState.update('pyops.password', password);
                        outputChannel.appendLine('Used key from user YAML as password');
                    }
                    if (!password) {
                        password = await vscode.window.showInputBox({
                            prompt: 'Enter PyOps auth key or password for Basic Auth (will be stored securely for this workspace)',
                            password: true
                        });
                        if (password) {
                            await context.workspaceState.update('pyops.password', password);
                            outputChannel.appendLine('Auth key/password stored in workspace state');
                        } else {
                            outputChannel.appendLine('No auth key/password provided; Basic Auth may fail');
                        }
                    } else {
                        outputChannel.appendLine('Auth key/password found in workspace state');
                    }
                }
                outputChannel.appendLine('PyOps configuration loaded from YAML');
            } else {
                outputChannel.appendLine('No valid YAML config loaded');
            }

            // Watch for YAML file changes
            const yamlPattern = path.join(workspaceRoot, '*.yaml');
            configWatcher = vscode.workspace.createFileSystemWatcher(yamlPattern);
            configWatcher.onDidChange(async (uri) => {
                outputChannel.appendLine(`YAML file changed: ${uri.fsPath}`);
                // Reload config on change
                const changedUserPath = path.join(workspaceRoot, `.pyops.${systemUser}.yaml`);
                const changedDefaultPath = path.join(workspaceRoot, '.pyops.yaml');
                if (uri.fsPath === changedUserPath || uri.fsPath === changedDefaultPath) {
                outputChannel.appendLine('Relevant YAML file changed, reloading config...');
                // Reload global YAML
                let globalReloadConfig: Record<string, any> = {};
                if (fs.existsSync(yamlPath)) {
                    try {
                        const content = fs.readFileSync(yamlPath, 'utf8');
                        const parsed = yaml.load(content);
                        if (parsed && typeof parsed === 'object' && parsed !== null) {
                            globalReloadConfig = parsed as Record<string, any>;
                            outputChannel.appendLine(`Reloaded global config from ${yamlPath}`);
                        } else {
                            outputChannel.appendLine(`Invalid or empty global YAML at ${yamlPath}: parsed = ${JSON.stringify(parsed)}`);
                        }
                    } catch (error: any) {
                        outputChannel.appendLine(`Error reloading global YAML: ${error.message}`);
                    }
                }

                // Reload user-specific YAML if exists (user overrides global)
                let userReloadConfig: Record<string, any> = {};
                if (fs.existsSync(changedUserPath)) {
                    try {
                        const content = fs.readFileSync(changedUserPath, 'utf8');
                        const parsed = yaml.load(content);
                        if (parsed && typeof parsed === 'object' && parsed !== null) {
                            userReloadConfig = parsed as Record<string, any>;
                            outputChannel.appendLine(`Reloaded user-specific config from ${changedUserPath}`);
                        } else {
                            outputChannel.appendLine(`Invalid or empty user-specific YAML at ${changedUserPath}: parsed = ${JSON.stringify(parsed)}`);
                        }
                    } catch (error: any) {
                        outputChannel.appendLine(`Error reloading user-specific YAML: ${error.message}`);
                    }
                }

                const reloadConfig = { ...globalReloadConfig, ...userReloadConfig };
                outputChannel.appendLine('Merged global and user configs on reload');

                // Update VSCode config if changed
                if (Object.keys(reloadConfig).length > 0) {
                    const pyopsConfig = vscode.workspace.getConfiguration('pyops');
                    if (reloadConfig.serverUrl) {
                        await pyopsConfig.update('serverUrl', reloadConfig.serverUrl, vscode.ConfigurationTarget.Workspace);
                        outputChannel.appendLine(`Reloaded serverUrl: ${reloadConfig.serverUrl}`);
                    }
                    if (reloadConfig.user) {
                        await pyopsConfig.update('username', reloadConfig.user, vscode.ConfigurationTarget.Workspace);
                        outputChannel.appendLine(`Reloaded username from 'user': ${reloadConfig.user}`);
                    }
                    if (reloadConfig.timeout) {
                        await pyopsConfig.update('timeout', reloadConfig.timeout, vscode.ConfigurationTarget.Workspace);
                        outputChannel.appendLine(`Reloaded timeout: ${reloadConfig.timeout}`);
                    }
                    // Handle auth: use merged auth setting, update password if key present and auth http-basic
                    const authType = reloadConfig.auth || 'none';
                    if (authType === 'http-basic') {
                        let password = context.workspaceState.get('pyops.password') as string | undefined;
                        // Use 'key' from user config if available and no stored password
                        if (!password && userReloadConfig.key) {
                            password = userReloadConfig.key;
                            await context.workspaceState.update('pyops.password', password);
                            outputChannel.appendLine('Used key from user YAML as password on reload');
                        }
                        if (!password) {
                            password = await vscode.window.showInputBox({
                                prompt: 'Enter PyOps auth key or password for Basic Auth',
                                password: true
                            });
                            if (password) {
                                await context.workspaceState.update('pyops.password', password);
                                outputChannel.appendLine('Auth key/password stored in workspace state on reload');
                            } else {
                                outputChannel.appendLine('No auth key/password provided; Basic Auth may fail');
                            }
                        } else {
                            outputChannel.appendLine('Auth key/password found in workspace state');
                        }
                    }
                }
                // Refresh status
                await updateConnectionStatus();
                }
            });
            context.subscriptions.push(configWatcher);
            outputChannel.appendLine('YAML file watcher set up');
        } else {
            outputChannel.appendLine('No workspace opened');
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

            if (!serverUrl || !username) {
                statusBarItem.text = '$(circle-slash) PyOps: No Config';
                statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                statusBarItem.show();
                outputChannel.appendLine('Status: No config available');
                return;
            }

            try {
                const url = new URL('/', serverUrl).toString();
                const headers: any = {};
                if (username && password) {
                    const auth = Buffer.from(`${username}:${password}`).toString('base64');
                    headers['Authorization'] = `Basic ${auth}`;
                }

                const response = await fetch(url, { headers });
                if (response.ok) {
                    statusBarItem.text = `$(check) PyOps: Connected`;
                    statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
                    outputChannel.appendLine(`Status: Connected to ${serverUrl}`);
                } else {
                    statusBarItem.text = `$(circle-slash) PyOps: Failed (${response.status})`;
                    statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                    outputChannel.appendLine(`Status: Failed to connect to ${serverUrl} (${response.status})`);
                }
            } catch (error: any) {
                statusBarItem.text = `$(circle-slash) PyOps: Error (${error.message})`;
                statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                outputChannel.appendLine(`Status: Error connecting to server: ${error.message}`);
            }
            statusBarItem.show();
        }

        // Initial status update
        await updateConnectionStatus();

        // Update on config changes
        context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(async (e) => {
            if (e.affectsConfiguration('pyops')) {
                outputChannel.appendLine('Config changed, updating status...');
                await updateConnectionStatus();
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

    // Auto-refresh timer for manager
    const autoRefreshInterval = vscode.workspace.getConfiguration('pyops').get('autoRefreshInterval', 10) * 1000;
    const managerRefreshTimer = setInterval(() => {
        managerTreeProvider.refresh();
    }, autoRefreshInterval);
    context.subscriptions.push({ dispose: () => clearInterval(managerRefreshTimer) });

    // Register native tree view for monitor (repurposing existing ID)
    jobsTreeProvider = new JobsTreeDataProvider(context);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-monitor', jobsTreeProvider));

    // Auto-refresh timer for monitor
    const monitorRefreshInterval = autoRefreshInterval;
    const monitorRefreshTimer = setInterval(() => {
        if (jobsTreeProvider) {
            jobsTreeProvider.refresh();
        }
    }, monitorRefreshInterval);
    context.subscriptions.push({ dispose: () => clearInterval(monitorRefreshTimer) });

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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error fetching jobs: ${error.message}`);
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error canceling job: ${error.message}`);
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error downloading job: ${error.message}`);
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error deleting job: ${error.message}`);
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
                        const jobData = await jobResponse.json() as any;
                        const active = activeJobs.get(key);
                        if (active) {
                            // Append param if available
                            if (jobData.param && jobViewOutputChannel) {
                                jobViewOutputChannel.appendLine(`\n--- Parameters ---`);
                                jobViewOutputChannel.appendLine(jobData.param);
                            }
                            // Append stdout
                            if (jobData.stdout) {
                                const newOut = jobData.stdout.slice(active.prevStdoutLen);
                                if (newOut.length > 0 && jobViewOutputChannel) {
                                    jobViewOutputChannel.appendLine(`[STDOUT]`);
                                    jobViewOutputChannel.append(newOut);
                                }
                                active.prevStdoutLen = jobData.stdout.length;
                            }
                            // Append stderr
                            if (jobData.stderr) {
                                const newErr = jobData.stderr.slice(active.prevStderrLen);
                                if (newErr.length > 0 && jobViewOutputChannel) {
                                    jobViewOutputChannel.appendLine(`[STDERR]`);
                                    jobViewOutputChannel.append(newErr);
                                }
                                active.prevStderrLen = jobData.stderr.length;
                            }
                            // Update state
                            jobViewOutputChannel.appendLine(`\n--- State: ${jobData.state} ---`);
                            if (['done', 'failed', 'cancelled', 'remove'].includes(jobData.state)) {
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
                } catch (error: any) {
                    jobViewOutputChannel.appendLine(`Error polling job: ${error.message}`);
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
                } catch (error: any) {
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

            const type = typePick.label.toLowerCase();

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
            let scriptlets: any[] = [];
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
                outputChannel.appendLine(`[Create Scriptlet] Sets list fetch status: ${response.status} ${response.statusText}`);
                if (response.ok) {
                    scriptlets = await response.json() as any[];
                    const existingSets = [...new Set(scriptlets.map((s: any) => s.set))].sort();
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
                    const setScriptlets = scriptlets.filter((s: any) => s.set === set);
                    const existingNames = new Set(setScriptlets.map((s: any) => s.name));
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

                    if (type === 'markdown' && name !== 'README') {
                        vscode.window.showErrorMessage('Markdown only allowed for README');
                        return;
                    }

                    const createHeaders: any = { 'Content-Type': 'application/json', ...headers };
                    const data = { set, name, type };
                    outputChannel.appendLine(`[Create Scriptlet] Attempting to create ${type} '${set}::${name}' with data: ${JSON.stringify(data)}`);
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
                                const updatedScriptlets = await verifyResponse.json() as any[];
                                const created = updatedScriptlets.some((s: any) => s.set === set && s.name === name && s.type === type);
                                if (created) {
                                    managerTreeProvider.refresh();
                                    vscode.window.showInformationMessage(`Created ${type} ${set}::${name}`);
                                    outputChannel.appendLine(`[Create Scriptlet] Verified creation successful.`);
                                    // Open local editor
                                    const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                                    const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
                                    const ext = inferExt(type);
                                    const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
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
                                vscode.window.showInformationMessage(`Created ${type} ${set}::${name} (verification failed)`);
                            }
                        } else {
                            const err = await response.text();
                            const errorMsg = `Failed to create: ${err}`;
                            vscode.window.showErrorMessage(errorMsg);
                            outputChannel.appendLine(`[Create Scriptlet] Creation failed: ${errorMsg}`);
                        }
                    } catch (error: any) {
                        const errorMsg = `Error creating scriptlet: ${error.message}`;
                        vscode.window.showErrorMessage(errorMsg);
                        outputChannel.appendLine(`[Create Scriptlet] Creation error: ${errorMsg}`);
                    }
                } else {
                    const errorMsg = `Failed to fetch sets: ${response.statusText}`;
                    vscode.window.showErrorMessage(errorMsg);
                    outputChannel.appendLine(`[Create Scriptlet] Sets fetch failed: ${errorMsg}`);
                }
            } catch (error: any) {
                const errorMsg = `Error fetching sets: ${error.message}`;
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

            const headers: any = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers });
                if (response.ok) {
                    const responseData = await response.json();
                    const code = (responseData as any).code;
                    const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                    const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
                    const ext = inferExt(type);
                    const localPath = path.join(workspaceRoot, localPyopsDir, set, type, `${name}.${ext}`);
                    const dirPath = path.dirname(localPath);
                    if (!fs.existsSync(dirPath)) {
                        fs.mkdirSync(dirPath, { recursive: true });
                    }
                    fs.writeFileSync(localPath, code);
                    vscode.window.showInformationMessage(`Reset local ${name}.${ext} from remote`);
                } else {
                    vscode.window.showErrorMessage(`Failed to fetch remote: ${response.statusText}`);
                }
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error resetting local: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.writeLocalToRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
            const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
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

            const headers: any = { 'Content-Type': 'application/json' };
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
                    vscode.window.showInformationMessage(`Wrote local to remote draft for ${name}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to write draft: ${response.statusText}`);
                }
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error writing draft: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.saveRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: any = { 'Content-Type': 'application/json' };
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            // Fetch current draft
            const fetchHeaders = { ...headers };
            delete fetchHeaders['Content-Type'];
            try {
                const codeResponse = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers: fetchHeaders });
                if (!codeResponse.ok) {
                    vscode.window.showErrorMessage('No remote draft to save');
                    return;
                }
                const codeResponseData = await codeResponse.json();
                const code = (codeResponseData as any).code;
                const data = { set, name, code };
                const saveResponse = await fetch(`${serverUrl}/scriptlet/inventory/item`, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(data)
                });
                if (saveResponse.ok) {
                    managerTreeProvider.refresh();
                    vscode.window.showInformationMessage(`Saved remote draft for ${name}`);
                } else {
                    vscode.window.showErrorMessage(`Failed to save: ${saveResponse.statusText}`);
                }
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error saving draft: ${error.message}`);
            }
        }));

        // Job output channels
        jobOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Out');
        jobErrOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Err');
        context.subscriptions.push(jobOutputChannel, jobErrOutputChannel);

        context.subscriptions.push(vscode.commands.registerCommand('pyops.startDebugJob', async (treeItem?: ScriptletTreeItem) => {
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

            const headers: any = { 'Content-Type': 'application/json' };
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
                    const jobId = (responseData as any).job;
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
                            const jobData = await jobResponse.json() as any;
                            const active = activeJobs.get(key);
                            if (active) {
                                if (jobData.stdout) {
                                    const newOut = jobData.stdout.slice(active.prevStdoutLen);
                                    if (newOut.length > 0) {
                                        jobOutputChannel.append(newOut);
                                    }
                                    active.prevStdoutLen = jobData.stdout.length;
                                }
                                if (jobData.stderr) {
                                    const newErr = jobData.stderr.slice(active.prevStderrLen);
                                    if (newErr.length > 0) {
                                        jobErrOutputChannel.append(newErr);
                                    }
                                    active.prevStderrLen = jobData.stderr.length;
                                }
                                jobOutputChannel.show(true);
                                jobErrOutputChannel.show(true);
                            }
                            if (['done', 'failed', 'cancelled', 'remove'].includes(jobData.state)) {
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error starting debug: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.stopDebugJob', async (treeItem?: ScriptletTreeItem) => {
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error stopping debug: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.diffLocalRemote', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
            const username = pyopsConfig.get('username') as string;
            const password = context.workspaceState.get('pyops.password') as string;

            const headers: any = {};
            if (username && password) {
                const auth = Buffer.from(`${username}:${password}`).toString('base64');
                headers['Authorization'] = `Basic ${auth}`;
            }

            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${set}&name=${name}`, { headers });
                if (response.ok) {
                const responseData = await response.json();
                const code = (responseData as any).code;
                const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error in diff: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteScriptlet', async (treeItem?: ScriptletTreeItem) => {
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

            const headers: any = {};
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
                    const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
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
            } catch (error: any) {
                vscode.window.showErrorMessage(`Error deleting scriptlet: ${error.message}`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.openLocalFile', async (treeItem?: ScriptletTreeItem) => {
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const type = treeItem.scriptletType!;
            const pyopsConfig = vscode.workspace.getConfiguration('pyops');
            const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
            const localPyopsDir = pyopsConfig.get('localPyopsDir', '.pyops') as string;
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
    } catch (error: any) {
        outputChannel.appendLine(`Activation failed: ${error.message}`);
        outputChannel.appendLine(`Stack: ${error.stack}`);
        outputChannel.show(true);
        console.error('PyOps activation error:', error);
        throw error; // Re-throw to show in VSCode
    }
}

export function deactivate() {
    if (configWatcher) {
        configWatcher.dispose();
    }
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
