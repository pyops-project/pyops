import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ApiClient } from '../apiClient';

export class DebugService {
    constructor(private apiClient: ApiClient, private outputChannel: vscode.OutputChannel) {}

    static buildOverrides(set: string, workspaceRoot: string): { overrides: Record<string, string>, pathMappings: Array<{remoteRoot: string, localRoot: string}> } {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const setPath = path.join(workspaceRoot, localPyopsDir, set);
        const overrides: Record<string, string> = {};
        if (!fs.existsSync(setPath)) {
            return { overrides, pathMappings: [] };
        }
        const walkDir = (dir: string) => {
            const files = fs.readdirSync(dir);
            for (const file of files) {
                const fullPath = path.join(dir, file);
                const stat = fs.statSync(fullPath);
                if (stat.isDirectory()) {
                    walkDir(fullPath);
                } else if (file.endsWith('.py')) {
                    const relative = path.relative(setPath, fullPath);
                    const name = path.basename(relative, '.py');
                    const remoteKey = `${set}::${name}`;
                    overrides[remoteKey] = fs.readFileSync(fullPath, 'utf8');
                }
            }
        };
        walkDir(setPath);
        const pathMappings = [{
            localRoot: `\${workspaceFolder}/${localPyopsDir}`,
            remoteRoot: "./.debug/src"
        }];
        return { overrides, pathMappings };
    }

    async enqueueDebugJob(set: string, name: string, overrides: Record<string, string> = {}): Promise<{ jobId?: string; port?: number }> {
        const port = await this.getDebugPort();

        const data = { set, name, drop_done: false, param: '#>AWAIT_DEBUG', kind: 'debug', overrides };
        this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Request to /scriptlet/job/item with data keys: ${Object.keys(data)}`);
        this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Overrides: ${JSON.stringify(overrides)}`);
        try {
            const response = await this.apiClient.fetch('/scriptlet/job/item', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Response status: ${response.status} ${response.statusText}`);
            if (response.ok) {
                const responseData = await response.json();
                const jobId = (responseData as { job: string }).job;
                this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Success, jobId: ${jobId}`);
                return { jobId, port };
            } else {
                const errorText = await response.text();
                this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Failed with error: ${errorText}`);
                return {};
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Exception: ${errorMessage}`);
            return {};
        }
    }

    public async getDebugPort(): Promise<number> {
        const response = await this.apiClient.fetch('/debug/port', { method: 'GET' });
        if (!response.ok) {
            const errorText = await response.text();
            this.outputChannel.appendLine(`[DEBUG getDebugPort] Failed: ${errorText}`);
            throw new Error(`Failed to fetch debug port: ${errorText}`);
        }
        const portData = await response.json() as { port: number };
        const port = portData.port;
        if (typeof port !== 'number' || port <= 0 || port > 65535) {
            throw new Error(`Invalid port from server: ${port}`);
        }
        this.outputChannel.appendLine(`[DEBUG getDebugPort] Success: ${port}`);
        return port;
    }

    public getDebugHost(): string {
        const serverUrl = this.apiClient.getServerUrl();
        try {
            return new URL(serverUrl).hostname;
        } catch {
            this.outputChannel.appendLine(`[DEBUG getDebugHost] Invalid serverUrl ${serverUrl}, defaulting to localhost`);
            return 'localhost';
        }
    }
}
