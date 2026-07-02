import * as vscode from 'vscode';

export class AuthService {
    static async setupAuth(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): Promise<void> {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const authMode = pyopsConfig.get('authMode', 'basic') as string;
        if (authMode === 'x-user') {
            outputChannel.appendLine('Auth mode: X-User (direct); no password required');
            return;
        }
        const username = pyopsConfig.get('username') as string;
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
