import * as vscode from 'vscode';

import { resolveConnectivity, usesPromptPassword } from '../connectivity';

export class AuthService {
    static async setupAuth(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): Promise<void> {
        const c = resolveConnectivity();
        if (c.authMode === 'x-user') {
            outputChannel.appendLine('Auth mode: X-User (direct); no password required');
            return;
        }
        if (!usesPromptPassword(c)) {
            // Basic Auth password comes from the profile's api_key — nothing to prompt.
            outputChannel.appendLine(`Auth: using api_key from profile '${c.profileName}'`);
            return;
        }
        const username = c.username;
        let password = await context.secrets.get('pyops.password');
        if (username && !password) {
            password = await vscode.window.showInputBox({
                prompt: 'Enter PyOps password for Basic Auth (will be stored securely for this workspace)',
                password: true
            });
            if (password) {
                await context.secrets.store('pyops.password', password);
                outputChannel.appendLine('Auth password stored in secret storage');
            } else {
                outputChannel.appendLine('No auth password provided; Basic Auth may fail');
            }
        } else if (username && password) {
            outputChannel.appendLine('Auth credentials found in secret storage');
        }
    }
}
