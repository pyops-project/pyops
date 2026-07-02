import * as vscode from 'vscode';

// Fallback X-User value when authMode is 'x-user' and no username is set,
// mirroring pyopsctl's DEFAULT_X_USER ('pyopsctl').
export const DEFAULT_X_USER = 'vsix';

export class ApiClient {
    constructor(private context: vscode.ExtensionContext) {}

    async getHeaders(): Promise<Record<string, string>> {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const authMode = pyopsConfig.get('authMode', 'basic') as string;
        const username = pyopsConfig.get('username') as string;
        const headers: Record<string, string> = {};
        if (authMode === 'x-user') {
            // Direct connection to a server that trusts the header (no auth
            // proxy in front): declare the owner via X-User, no Authorization.
            headers['X-User'] = username || DEFAULT_X_USER;
            return headers;
        }
        const password = await this.context.secrets.get('pyops.password');
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
