import * as vscode from 'vscode';

export class ApiClient {
    constructor(private context: vscode.ExtensionContext) {}

    async getHeaders(): Promise<Record<string, string>> {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const username = pyopsConfig.get('username') as string;
        const password = this.context.workspaceState.get('pyops.password') as string;
        const headers: Record<string, string> = {};
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }
        return headers;
    }

    getServerUrl(defaultUrl: string = 'http://localhost:8000'): string {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        return pyopsConfig.get('serverUrl') as string || defaultUrl;
    }

    hasValidConfig(): boolean {
        return ApiClient.hasValidConfigStatic();
    }

    static hasValidConfigStatic(): boolean {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || '';
        return !!serverUrl;
    }
}
