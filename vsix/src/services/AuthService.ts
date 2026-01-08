import * as vscode from 'vscode';

export class AuthService {
    static async setupAuth(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): Promise<void> {
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
    }
}
