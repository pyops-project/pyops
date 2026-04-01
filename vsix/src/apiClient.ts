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

    async fetch(path: string, init?: RequestInit): Promise<Response> {
        const url = `${this.getServerUrl()}${path}`;
        const headers = await this.getHeaders();
        return fetch(url, { ...init, headers: { ...headers, ...init?.headers as Record<string, string> } });
    }

    async fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
        const response = await this.fetch(path, init);
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json() as Promise<T>;
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
