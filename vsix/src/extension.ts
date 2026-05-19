import * as net from 'net';
import * as vscode from 'vscode';
import { StatusBarItem, window as vscodeWindow } from 'vscode';
import { Job, JobsTreeDataProvider, JobTreeItem } from './jobsTreeProvider';
import { ManagerTreeDataProvider, ScriptletTreeItem } from './managerTreeProvider';
import { CreateScriptletCommand } from './commands/CreateScriptletCommand';
import { DebugService } from './services/DebugService';
import { DebugSessionWatcher } from './services/DebugSessionWatcher';
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
let jobService!: JobService;
let debugOutputMonitor: DebugOutputMonitor;

let jobsTreeProvider: JobsTreeDataProvider | undefined;
let managerTreeProvider: ManagerTreeDataProvider | undefined;
let refreshManager: RefreshTimerManager | undefined;

let extensionState: {
    outputChannel: vscode.OutputChannel;
    connectionStatusManager: ConnectionStatusManager;
    jobService: JobService;
    scriptletService: ScriptletService;
    debugService: DebugService;
    DebugService: typeof DebugService;
    debugOutputMonitor: DebugOutputMonitor;
    managerTreeProvider: ManagerTreeDataProvider;
    jobsTreeProvider: JobsTreeDataProvider;
    debugConfigProvider: PyOpsPythonDebugConfigProvider;
} | undefined;

export function getExtensionState() { return extensionState; }




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

    const apiClient = new ApiClient(context);

        // Create status bar item for connection state
        const statusManager = new ConnectionStatusManager(apiClient, context, outputChannel);
        statusBarItem = statusManager.createStatusBarItem();
        context.subscriptions.push(statusBarItem);

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

    // Register tree view for manager
    managerTreeProvider = new ManagerTreeDataProvider(context, outputChannel, apiClient);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('pyops-manager', managerTreeProvider));

    context.subscriptions.push(vscode.commands.registerCommand('pyops.createScriptlet', async () => {
        await CreateScriptletCommand.execute(apiClient, outputChannel, managerTreeProvider!);
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
    const scriptletService = new ScriptletService(apiClient, managerTreeProvider!, remoteProvider);

    // Create debug service + session watcher. The watcher polls each
    // pyops debug session's underlying job; when the job ends, it
    // calls vscode.debug.stopDebugging so the DAP disconnect triggers
    // debugpy's adapter to shut down on its own protocol path (the
    // adapter detaches itself from the job's process group via its
    // own setsid, so a server-side killpg on the job doesn't reach it).
    const debugService = new DebugService(apiClient, outputChannel);
    const debugSessionWatcher = new DebugSessionWatcher(apiClient, outputChannel);
    context.subscriptions.push({ dispose: () => debugSessionWatcher.dispose() });
    context.subscriptions.push(vscode.debug.onDidStartDebugSession((session) => {
        debugSessionWatcher.watch(session);
    }));

    // Register debug configuration provider for Python
    const debugConfigProvider = new PyOpsPythonDebugConfigProvider(debugService);
    context.subscriptions.push(vscode.debug.registerDebugConfigurationProvider('python', debugConfigProvider));


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
        jobOutputChannel = vscodeWindow.createOutputChannel('PyOps Job Output');
        context.subscriptions.push(jobOutputChannel);

        debugOutputMonitor = new DebugOutputMonitor(apiClient, jobOutputChannel);

        extensionState = {
            outputChannel,
            connectionStatusManager: statusManager,
            jobService,
            scriptletService,
            debugService,
            DebugService,
            debugOutputMonitor,
            managerTreeProvider: managerTreeProvider!,
            jobsTreeProvider: jobsTreeProvider!,
            debugConfigProvider,
        };

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

            const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
            if (!workspaceFolder) {
                vscode.window.showWarningMessage('No workspace folder open');
                return;
            }
            const workspaceRoot = workspaceFolder.uri.fsPath;
            const { overrides, pathMappings } = DebugService.buildOverrides(set, workspaceRoot);
            outputChannel.appendLine(`[DEBUG] Built overrides for ${set}: ${Object.keys(overrides).length} files`);
            outputChannel.appendLine(`[DEBUG] Path mappings: ${JSON.stringify(pathMappings, null, 2)}`);
            const result = await debugService.enqueueDebugJob(set, name, overrides);
            if (result.jobId) {
                try {
                    jobOutputChannel.clear();
                    jobOutputChannel.show(true);
                    await debugOutputMonitor.startMonitoring(key, result.jobId);

                    // Wait until the debugpy listener is reachable on the
                    // leased port. This is the readiness signal under the
                    // new server contract — `state=running` only fires
                    // AFTER `vscode.debug.startDebugging` lets debugpy
                    // proceed past `wait_for_client()`, so polling the
                    // server-side job state here would deadlock. The
                    // first-time-per-set venv install can be slow; allow
                    // a generous deadline. While we wait, watch for
                    // terminal job states so a failed install / spawn
                    // doesn't hang the operator at the workbench.
                    const debugHost = debugService.getDebugHost();
                    const listenDeadlineMs = 180000;
                    const listenStart = Date.now();
                    let listenerUp = false;
                    while (Date.now() - listenStart < listenDeadlineMs) {
                        const jobResp = await apiClient.fetch(`/scriptlet/job/item?id=${result.jobId}`);
                        if (jobResp.ok) {
                            const job = await jobResp.json() as { state: string; detail?: string };
                            if (['failed', 'killed', 'cancelled', 'removed'].includes(job.state)) {
                                const detail = job.detail ? `\n${job.detail}` : '';
                                throw new Error(`Job ${result.jobId} ended before listener came up: state=${job.state}${detail}`);
                            }
                        }
                        const reachable = await new Promise<boolean>((resolve) => {
                            const sock = new net.Socket();
                            const close = () => { try { sock.destroy(); } catch { /* noop */ } };
                            sock.setTimeout(1000);
                            sock.once('connect', () => { close(); resolve(true); });
                            sock.once('error', () => { close(); resolve(false); });
                            sock.once('timeout', () => { close(); resolve(false); });
                            sock.connect(result.port as number, debugHost);
                        });
                        if (reachable) { listenerUp = true; break; }
                        await new Promise(resolve => setTimeout(resolve, 500));
                    }
                    if (!listenerUp) {
                        throw new Error(`Timeout waiting for debugpy listener on ${debugHost}:${result.port}`);
                    }

                    // Start debug attach
                    const host = debugService.getDebugHost();
                    const config = {
                        name: `Attach PyOps ${set}::${name}`,
                        type: 'python',
                        request: 'attach',
                        connect: { host, port: result.port },
                        pathMappings,
                        jobId: result.jobId
                    };
                    outputChannel.appendLine(`[DEBUG] Starting debug with config: ${JSON.stringify(config, null, 2)}`);
                    await vscode.debug.startDebugging(workspaceFolder, config);

                    vscode.window.showInformationMessage(`Started debug job ${result.jobId} for ${name} with local overrides`);
                } catch (error: unknown) {
                    // The port was leased in enqueueDebugJob; if we never
                    // get to vscode.debug.startDebugging (job died early,
                    // listener never opened, etc.), onDidTerminateDebugSession
                    // will never fire to release it. Release here so the
                    // pool isn't poisoned for the next attempt.
                    // (The DebugOutputMonitor poll loop self-terminates
                    // when it sees a terminal state, so the local monitor
                    // entry is cleaned up by the next poll without us
                    // having to call stopMonitoring — which would send a
                    // cancel API call and show a misleading "Stopped"
                    // toast for an already-dead job.)
                    if (result.port !== undefined) {
                        debugService.releasePort(result.port);
                    }
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

        context.subscriptions.push(vscode.commands.registerCommand('pyops.attachDebugJob', async () => {
            const folder = vscode.workspace.workspaceFolders?.[0];
            if (!folder) {
                vscode.window.showWarningMessage('No workspace folder');
                return;
            }
            const jobs = await jobService.listJobs();
            const runningDebugJobs = jobs.filter(j => j.kind === 'debug' && (j.state === 'running' || j.state === 'dequeued'));
            if (runningDebugJobs.length === 0) {
                vscode.window.showErrorMessage('No running debug job found');
                return;
            }
            if (runningDebugJobs.length > 1) {
                vscode.window.showErrorMessage('Multiple running debug jobs. Cannot attach.');
                return;
            }
            const debugJob: Job = runningDebugJobs[0];
            // Port comes from this VSCode session's lease table. If the
            // operator restarted VSCode between `start debug` and
            // `attach debug`, the in-memory lease is lost and we can't
            // recover the port — the operator must restart the job.
            const port = debugService.findPortForJob(debugJob.id);
            if (port === undefined) {
                vscode.window.showErrorMessage(
                    `No debug-port lease for job ${debugJob.id} in this VSCode session ` +
                    `(VSCode restarted between start and attach?). Stop the job and ` +
                    `start a fresh debug session.`,
                );
                return;
            }
            outputChannel.appendLine(`Attaching to job ${debugJob.id} (${debugJob.set || '?'}::${debugJob.name || '?'}) at port ${port}`);
            const host = debugService.getDebugHost();
            const config: vscode.DebugConfiguration = {
                name: `Attach to PyOps Debug (${debugJob.set || 'unknown'}::${debugJob.name || 'unknown'})`,
                type: 'python',
                request: 'attach',
                connect: {
                    host,
                    port
                },
                justMyCode: false
            };
            try {
                await vscode.debug.startDebugging(folder, config);
                vscode.window.showInformationMessage(`Attached to debug job ${debugJob.id}`);
            } catch (error: unknown) {
                vscode.window.showErrorMessage(`Failed to start debug session: ${error instanceof Error ? error.message : String(error)}`);
            }
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

        // Auto-release debug-port leases when the corresponding VSCode
        // debug session ends (clean stop, user-clicks-stop, or crash).
        // Without this the pool drains to zero as the operator iterates
        // on a debug session, because nothing else returns ports to it.
        context.subscriptions.push(vscode.debug.onDidTerminateDebugSession((session) => {
            // Stop watching the session first (no point polling a job
            // whose session just went away — VSCode does its own
            // disconnect handling).
            debugSessionWatcher.cancel(session.id);

            const config = session.configuration as Record<string, unknown>;
            const jobId = typeof config?.jobId === 'string' ? config.jobId : undefined;
            if (!jobId) return;
            const port = debugService.findPortForJob(jobId);
            if (port !== undefined) {
                debugService.releasePort(port);
                outputChannel.appendLine(
                    `[DEBUG] released port ${port} on debug-session-end for ${jobId}`,
                );
            }
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
    statusBarItem?.dispose();
    outputChannel?.dispose();
    jobOutputChannel?.dispose();
    jobService?.dispose();
    debugOutputMonitor?.dispose();
}
