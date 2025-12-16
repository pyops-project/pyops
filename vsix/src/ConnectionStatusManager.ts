import * as vscode from 'vscode';

export class ConnectionStatusManager {
    private statusBarItem: vscode.StatusBarItem | undefined;

    constructor(private context: vscode.ExtensionContext, private outputChannel: vscode.OutputChannel) {}

    createStatusBarItem(): vscode.StatusBarItem {
        this.statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
        this.statusBarItem.command = 'pyops.refreshConnectionStatus';
        return this.statusBarItem;
    }

    async update(): Promise<void> {
        if (!this.statusBarItem) throw new Error('StatusBarItem not created');

        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || '';
        const username = pyopsConfig.get('username') as string;
        const password = this.context.workspaceState.get('pyops.password') as string;

        let isValid = false;

        if (!serverUrl) {
            this.statusBarItem.text = '$(circle-slash) PyOps: No serverUrl';
            this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
            this.statusBarItem.tooltip = 'No PyOps serverUrl configured. Open settings (Ctrl+,) to configure serverUrl.';
            this.outputChannel.appendLine('Status: No serverUrl available');
        } else {
            try {
                const url = new URL('/', serverUrl).toString();
                const headers = this.getAuthHeaders(username, password);
                const response = await fetch(url, { headers });
                if (response.ok) {
                    this.statusBarItem.text = `$(check) PyOps: Connected`;
                    this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
                    this.statusBarItem.tooltip = 'PyOps connected successfully.';
                    isValid = true;
                    this.outputChannel.appendLine(`Status: Connected to ${serverUrl}`);
                } else {
                    this.statusBarItem.text = `$(circle-slash) PyOps: Failed (${response.status})`;
                    this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                    this.statusBarItem.tooltip = `Connection failed (${response.status}). Check server, credentials, and network.`;
                    this.outputChannel.appendLine(`Status: Failed to connect to ${serverUrl} (${response.status})`);
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                this.statusBarItem.text = `$(circle-slash) PyOps: Error (${errorMessage})`;
                this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                this.statusBarItem.tooltip = `Error connecting to server: ${errorMessage}. Check configuration and network.`;
                this.outputChannel.appendLine(`Status: Error connecting to server: ${errorMessage}`);
            }
        }

        await vscode.commands.executeCommand('setContext', 'pyops.hasValidConfig', isValid);
        this.statusBarItem.show();
    }

    private getAuthHeaders(username: string | undefined, password: string | undefined): Record<string, string> {
        const headers: Record<string, string> = {};
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }
        return headers;
    }
}
