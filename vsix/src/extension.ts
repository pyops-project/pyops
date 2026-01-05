import * as vscode from 'vscode';
import { StatusBarItem, window as vscodeWindow } from 'vscode';
import { JobsTreeDataProvider, JobTreeItem } from './jobsTreeProvider';
import { ManagerTreeDataProvider, ScriptletTreeItem } from './managerTreeProvider';
import { CreateScriptletCommand } from './commands/CreateScriptletCommand';
import { DebugService } from './services/DebugService';
import { DebugOutputMonitor } from './services/DebugOutputMonitor';
import { PyOpsPythonDebugConfigProvider } from './services/PyOpsPythonDebugConfigProvider';
import { ConnectionStatusManager } from './ConnectionStatusManager';
import { ApiClient } from './apiClient';
import { JobService } from './services/JobService';
import { ScriptletService } from './services/ScriptletService';
import { RemoteContentProvider, registerRemoteLanguageModeSetter } from './providers/RemoteContentProvider';
import { AuthService } from './services/AuthService';
import { RefreshTimerManager } from './services/RefreshTimerManager';





let outputChannel: vscode.OutputChannel;

let statusBarItem: StatusBarItem;
let jobOutputChannel: vscode.OutputChannel;
let jobErrOutputChannel: vscode.OutputChannel;
let jobService!: JobService;
let debugOutputMonitor: DebugOutputMonitor;

let jobsTreeProvider: JobsTreeDataProvider | undefined;
let managerTreeProvider: ManagerTreeDataProvider | undefined;
let refreshManager: RefreshTimerManager | undefined;




export async function activate(context: vscode.ExtensionContext) {
    outputChannel = vscodeWindow.createOutputChannel('PyOps');
    outputChannel.appendLine('PyOps extension activating...');

    try {

        await AuthService.setupAuth(context, outputChannel);

        // Register remote content provider for readonly remote diffs
        const remoteProvider = new RemoteContentProvider();
        context.subscriptions.push(vscode.workspace.registerTextDocumentContentProvider('pyops-remote', remoteProvider));

        // Set language mode for remote diff documents
        context.subscriptions.push(registerRemoteLanguageModeSetter());

        // Create status bar item for connection state
        const statusManager = new ConnectionStatusManager(context, outputChannel);
        statusBarItem = statusManager.createStatusBarItem();
        context.subscriptions.push(statusBarItem);

        // Assign to global for testing
        (global as any).connectionStatusManager = statusManager;

        // Initial status update
        await statusManager.update();

        // Update on config changes
        context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(async (e) => {
            if (e.affectsConfiguration('pyops')) {
                outputChannel.appendLine('Config changed, updating status...');
                await statusManager.update();
                if (managerTreeProvider) managerTreeProvider.refresh();
                if (jobsTreeProvider) jobsTreeProvider.refresh();
                if (refreshManager) {
                    const isValidConfig = ApiClient.hasValidConfigStatic();
                    if (isValidConfig) {
                        refreshManager.ensureRunning();
                    } else {
                        refreshManager.stopIfRunning();
                    }
                }
            }
        }));

        // Register command to refresh status
        const refreshStatusDisposable = vscode.commands.registerCommand('pyops.refreshConnectionStatus', async () => {
            outputChannel.appendLine('Manual status refresh triggered');
            await statusManager.update();
        });
        context.subscriptions.push(refreshStatusDisposable);

    const apiClient = new ApiClient(context);

    // Register tree view for manager
    managerTreeProvider = new ManagerTreeDataProvider(context, outputChannel, apiClient);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-manager', managerTreeProvider));

    context.subscriptions.push(vscode.commands.registerCommand('pyops.createScriptlet', async () => {
        await CreateScriptletCommand.execute(context, outputChannel, managerTreeProvider!);
    }));

    // Register native tree view for monitor (repurposing existing ID)
    jobsTreeProvider = new JobsTreeDataProvider(context, apiClient);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-monitor', jobsTreeProvider));

    refreshManager = new RefreshTimerManager(managerTreeProvider!, jobsTreeProvider!);
    refreshManager.ensureRunning();
    context.subscriptions.push(refreshManager);

    const jobViewOutputChannel = vscodeWindow.createOutputChannel('PyOps Job View');
    context.subscriptions.push(jobViewOutputChannel);
    jobService = new JobService(apiClient, jobsTreeProvider, jobViewOutputChannel);
    const scriptletService = new ScriptletService(apiClient, managerTreeProvider!);

    // Create debug service
    const debugService = new DebugService(context, outputChannel);

    // Assign to global for testing
    (global as any).jobService = jobService;
    (global as any).scriptletService = scriptletService;
    (global as any).debugService = debugService;
    (global as any).DebugService = DebugService;
    (global as any).managerTreeProvider = managerTreeProvider;
    (global as any).jobsTreeProvider = jobsTreeProvider;

    // Register debug configuration provider for Python
    const debugConfigProvider = new PyOpsPythonDebugConfigProvider(debugService);
    context.subscriptions.push(vscode.debug.registerDebugConfigurationProvider('python', debugConfigProvider));

    // Assign to global for testing
    (global as any).debugConfigProvider = debugConfigProvider;

    // Register debug session termination handler
    context.subscriptions.push(vscode.debug.onDidTerminateDebugSession(async (session) => {
        if (session.configuration?.jobId) {
            try {
                const apiClient = new ApiClient(context);
                const headers = await apiClient.getHeaders();
                const serverUrl = apiClient.getServerUrl();
                await fetch(`${serverUrl}/scriptlet/job/cancel?id=${session.configuration.jobId}`, {
                    method: 'PUT',
                    headers
                });
                vscode.window.showInformationMessage(`Canceled debug job ${session.configuration.jobId}`);
            } catch (error: unknown) {
                console.error('Error canceling job:', error instanceof Error ? error.message : String(error));
            }
        }
    }));

    // Register commands
        context.subscriptions.push(vscode.commands.registerCommand('pyops.refreshManager', async () => {
            if (managerTreeProvider) {
                await managerTreeProvider.refresh();
                vscode.window.showInformationMessage('PyOps Manager refreshed');
            }
        }));

        // Monitor commands
        context.subscriptions.push(vscode.commands.registerCommand('pyops.refreshMonitor', async () => {
            if (jobsTreeProvider) {
                await jobsTreeProvider.refresh();
                vscode.window.showInformationMessage('PyOps Monitor refreshed');
            } else {
                vscode.window.showWarningMessage('Monitor provider not initialized');
            }
        }));



        // Job output channels
        jobOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Out');
        jobErrOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Err');
        context.subscriptions.push(jobOutputChannel, jobErrOutputChannel);

        debugOutputMonitor = new DebugOutputMonitor(apiClient, jobOutputChannel, jobErrOutputChannel);

        // Assign to global for testing
        (global as any).debugOutputMonitor = debugOutputMonitor;

        context.subscriptions.push(vscode.commands.registerCommand('pyops.copyJobParam', async (treeItem?: JobTreeItem) => {
            await jobService.copyJobParam(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.cancelJob', async (treeItem?: JobTreeItem) => {
            await jobService.cancelJob(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.downloadJob', async (treeItem?: JobTreeItem) => {
            await jobService.downloadJob(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteJob', async (treeItem?: JobTreeItem) => {
            await jobService.deleteJob(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.viewJob', async (treeItem?: JobTreeItem) => {
            await jobService.viewJob(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.cancelAllJobs', async () => {
            await jobService.cancelAllJobs();
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteAllDone', async () => {
            await jobService.deleteAllDone();
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteAllFailed', async () => {
            await jobService.deleteAllFailed();
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.resetLocalFromRemote', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.resetLocalFromRemote(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.writeLocalToRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.writeLocalToRemoteDraft(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.saveRemoteDraft', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.saveRemoteDraft(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.startDebugJob', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = ApiClient.hasValidConfigStatic();
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName || treeItem.scriptletType !== 'task') return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const key = `${set}-${name}`;
            if (debugOutputMonitor.isMonitoring(key)) {
                vscode.window.showWarningMessage('Debug job already running');
                return;
            }

            const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
            const { overrides, pathMappings } = DebugService.buildOverrides(set, workspaceRoot);
            outputChannel.appendLine(`[DEBUG] Built overrides for ${set}: ${Object.keys(overrides).length} files`);
            outputChannel.appendLine(`[DEBUG] Path mappings: ${JSON.stringify(pathMappings, null, 2)}`);
            const result = await debugService.enqueueDebugJob(set, name, overrides);
            if (result.jobId) {
                try {
                    jobOutputChannel.clear();
                    jobErrOutputChannel.clear();
                    jobOutputChannel.show(true);
                    await debugOutputMonitor.startMonitoring(key, result.jobId);

                    // Wait for job to be running
                    const serverUrl = apiClient.getServerUrl();
                    const headers = await apiClient.getHeaders();
                    const jobTimeoutMs = 15000;
                    const pollIntervalMs = 1000;
                    const jobStartTime = Date.now();
                    let jobRunning = false;
                    while (Date.now() - jobStartTime < jobTimeoutMs) {
                        const response = await fetch(`${serverUrl}/scriptlet/job/item?id=${result.jobId}`, { headers });
                        if (!response.ok) {
                            if (response.status === 404) {
                                throw new Error(`Job ${result.jobId} not found`);
                            }
                            throw new Error(`Failed to fetch job status: ${response.status}`);
                        }
                        const job = await response.json() as { state: string };
                        if (job.state === 'running') {
                            jobRunning = true;
                            break;
                        }
                        if (['failed', 'killed', 'cancelled', 'removed'].includes(job.state)) {
                            throw new Error(`Job ${result.jobId} failed with state: ${job.state}`);
                        }
                        await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
                    }
                    if (!jobRunning) {
                        throw new Error(`Timeout waiting for job ${result.jobId} to start running`);
                    }



                    // Start debug attach
                    const config = {
                        name: `Attach PyOps ${set}::${name}`,
                        type: 'debugpy',
                        request: 'attach',
                        connect: { host: 'localhost', port: result.port },
                        pathMappings,
                        jobId: result.jobId
                    };
                    outputChannel.appendLine(`[DEBUG] Starting debug with config: ${JSON.stringify(config, null, 2)}`);
                    await vscode.debug.startDebugging(vscode.workspace.workspaceFolders![0], config);

                    vscode.window.showInformationMessage(`Started debug job ${result.jobId} for ${name} with local overrides`);
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : String(error);
                    vscode.window.showErrorMessage(`Failed to start debug: ${errorMessage}`);
                }
            } else {
                vscode.window.showErrorMessage(`Failed to start debug`);
            }
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.stopDebugJob', async (treeItem?: ScriptletTreeItem) => {
            const isValidConfig = ApiClient.hasValidConfigStatic();
            if (!isValidConfig) {
                vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
                return;
            }
            if (!treeItem || !treeItem.set || !treeItem.scriptletName) return;
            const set = treeItem.set!;
            const name = treeItem.scriptletName!;
            const key = `${set}-${name}`;
            await debugOutputMonitor.stopMonitoring(key);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.diffLocalRemote', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.diffLocalRemote(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.deleteScriptlet', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.deleteScriptlet(treeItem);
        }));

        context.subscriptions.push(vscode.commands.registerCommand('pyops.openLocalFile', async (treeItem?: ScriptletTreeItem) => {
            await scriptletService.openLocalFile(treeItem);
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
    jobService.dispose();
    debugOutputMonitor.dispose();
}
