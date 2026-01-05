import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';

export class DebugService {
    constructor(private context: vscode.ExtensionContext, private outputChannel: vscode.OutputChannel) {}

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
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
        const username = pyopsConfig.get('username') as string;
        const password = this.context.workspaceState.get('pyops.password') as string;
        const port = pyopsConfig.get('debugPort', 5678) as number;

        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }

        const data = { set, name, drop_done: false, param: '#>AWAIT_DEBUG', kind: 'debug', overrides };
        this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Request to ${serverUrl}/scriptlet/job/item with data keys: ${Object.keys(data)}`);
        this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] Overrides: ${JSON.stringify(overrides)}`);
        try {
            const response = await fetch(`${serverUrl}/scriptlet/job/item`, {
                method: 'PUT',
                headers,
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
}
