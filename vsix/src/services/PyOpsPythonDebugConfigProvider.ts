import * as vscode from 'vscode';
import * as path from 'path';
import { DebugService } from './DebugService';

export class PyOpsPythonDebugConfigProvider implements vscode.DebugConfigurationProvider {
    constructor(private _debugService: DebugService) {}

    async resolveDebugConfiguration(folder: vscode.WorkspaceFolder | undefined, config: vscode.DebugConfiguration): Promise<vscode.DebugConfiguration | null | undefined> {
        if (!folder || !vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
            return config;
        }

        const activeEditor = vscode.window.activeTextEditor;
        if (!activeEditor || activeEditor.document.languageId !== 'python') {
            return config;
        }

        const uri = activeEditor.document.uri;
        const workspaceRoot = folder.uri.fsPath;
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const localPyopsPath = path.join(workspaceRoot, localPyopsDir);

        // Check if file is in pyops/task/ directory
        const relativePath = path.relative(localPyopsPath, uri.fsPath);
        if (relativePath.startsWith('..') || !relativePath.startsWith('task/')) {
            return config; // Not a pyops task file
        }

        const parts = relativePath.split(path.sep);
        if (parts.length < 3) {
            return config;
        }
        const set = parts[1]; // pyops/{set}/task/{scriptlet}.py
        const scriptlet = path.basename(parts[2], '.py');

        // Build overrides from all local .py files in set
        const overrides = DebugService.buildOverrides(set, workspaceRoot);

        // Enqueue debug job
        const { jobId, port } = await this._debugService.enqueueDebugJob(set, scriptlet, overrides);
        if (!jobId) {
            vscode.window.showErrorMessage('Failed to enqueue PyOps debug job');
            return undefined;
        }

        // Return attach config
        return {
            name: `Attach PyOps ${set}::${scriptlet}`,
            type: 'python',
            request: 'attach',
            connect: { host: 'localhost', port },
            path: uri.fsPath,
            jobId
        };
    }
}
