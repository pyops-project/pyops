import * as vscode from 'vscode';

import { resolveConnectivity } from './connectivity';
import { errorText } from './errorText';

// Fallback X-User value when authMode is 'x-user' and no username is set,
// mirroring pyopsctl's DEFAULT_X_USER ('pyopsctl').
export const DEFAULT_X_USER = 'vsix';

export class ApiClient {
    constructor(private context: vscode.ExtensionContext) {}

    async getHeaders(): Promise<Record<string, string>> {
        const c = resolveConnectivity();
        const headers: Record<string, string> = {};
        if (c.authMode === 'x-user') {
            // Direct connection to a server that trusts the header (no auth
            // proxy in front): declare the owner via X-User, no Authorization.
            headers['X-User'] = c.username || DEFAULT_X_USER;
            return headers;
        }
        // Basic: the profile api_key supplies the password for the profile's own
        // identity; a remapped user (or profile-less setup) uses the stored secret.
        const password = c.profileApiKey !== undefined
            ? c.profileApiKey
            : await this.context.secrets.get('pyops.password');
        if (c.username && password) {
            const auth = Buffer.from(`${c.username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }
        return headers;
    }

    getServerUrl(defaultUrl: string = 'http://localhost:8000'): string {
        return resolveConnectivity().serverUrl || defaultUrl;
    }

    async fetch(path: string, init?: RequestInit): Promise<Response> {
        const url = `${this.getServerUrl()}${path}`;
        const headers = await this.getHeaders();
        return fetch(url, { ...init, headers: { ...headers, ...init?.headers as Record<string, string> } });
    }

    async fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
        const response = await this.fetch(path, init);
        if (!response.ok) throw new Error(await errorText(response));
        return response.json() as Promise<T>;
    }

    hasValidConfig(): boolean {
        return ApiClient.hasValidConfigStatic();
    }

    static hasValidConfigStatic(): boolean {
        return !!resolveConnectivity().serverUrl;
    }
}
