import * as vscode from 'vscode';

import { AuthService } from './services/AuthService';
import { ApiClient } from './apiClient';
import { resolveConnectivity, usesPromptPassword } from './connectivity';

export class ConnectionStatusManager {
    private statusBarItem: vscode.StatusBarItem | undefined;

    constructor(private apiClient: ApiClient, private context: vscode.ExtensionContext, private outputChannel: vscode.OutputChannel) {}

    createStatusBarItem(): vscode.StatusBarItem {
        this.statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
        this.statusBarItem.command = 'pyops.refreshConnectionStatus';
        return this.statusBarItem;
    }

    async update(): Promise<void> {
        if (!this.statusBarItem) throw new Error('StatusBarItem not created');

        const serverUrl = this.apiClient.getServerUrl('');

        let isValid = false;

        if (!serverUrl) {
            this.statusBarItem.text = '$(circle-slash) PyOps: No serverUrl';
            this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
            this.statusBarItem.tooltip = 'No PyOps serverUrl configured. Open settings (Ctrl+,) to configure serverUrl.';
            this.outputChannel.appendLine('Status: No serverUrl available');
        } else {
            try {
                const response = await this.apiClient.fetch('/');
                if (response.ok) {
                    this.statusBarItem.text = `$(check) PyOps: Connected`;
                    this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
                    this.statusBarItem.tooltip = 'PyOps connected successfully.';
                    isValid = true;
                    this.outputChannel.appendLine(`Status: Connected to ${serverUrl}`);
                } else {
                    // Only clear+reprompt when the password was actually prompted for.
                    // A bad profile api_key (or x-user) is not fixed by re-entry.
                    if (response.status === 401 && usesPromptPassword(resolveConnectivity())) {
                        this.outputChannel.appendLine('[AUTH] Invalid credentials (401), clearing stored password and prompting for new one.');
                        await this.context.secrets.delete('pyops.password');
                        await AuthService.setupAuth(this.context, this.outputChannel);
                        // retry
                        const response2 = await this.apiClient.fetch('/');
                        if (response2.ok) {
                            this.statusBarItem.text = `$(check) PyOps: Connected`;
                            this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
                            this.statusBarItem.tooltip = 'PyOps connected successfully.';
                            isValid = true;
                            this.outputChannel.appendLine(`Status: Connected to ${serverUrl}`);
                        } else {
                            this.statusBarItem.text = `$(circle-slash) PyOps: Failed (${response2.status})`;
                            this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                            this.statusBarItem.tooltip = `Connection failed (${response2.status}). Check server, credentials, and network.`;
                            this.outputChannel.appendLine(`Status: Failed to connect to ${serverUrl} (${response2.status})`);
                        }
                    } else {
                        this.statusBarItem.text = `$(circle-slash) PyOps: Failed (${response.status})`;
                        this.statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
                        this.statusBarItem.tooltip = `Connection failed (${response.status}). Check server, credentials, and network.`;
                        this.outputChannel.appendLine(`Status: Failed to connect to ${serverUrl} (${response.status})`);
                    }
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
}
