import * as vscode from 'vscode';
import * as path from 'path';
import { DebugService } from './DebugService';

export class PyOpsPythonDebugConfigProvider implements vscode.DebugConfigurationProvider {
    constructor(private _debugService: DebugService) {}

    async resolveDebugConfiguration(folder: vscode.WorkspaceFolder | undefined, config: vscode.DebugConfiguration): Promise<vscode.DebugConfiguration | null | undefined> {
        if (config.jobId) {
            // Already resolved, return as-is to avoid duplicate job enqueuing
            return config;
        }

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
        if (relativePath.startsWith('..')) {
            return config;
        }
        const dirName = path.dirname(relativePath);
        if (!dirName.endsWith('task') || !relativePath.endsWith('.py')) {
            return config; // Not a pyops task file
        }

        const dirParts = dirName.split(path.sep);
        if (dirParts.length !== 2 || dirParts[1] !== 'task') {
            return config;
        }
        const set = dirParts[0];
        const scriptlet = path.basename(relativePath, '.py');

        // Build overrides from all local .py files in set
        const { overrides, pathMappings } = DebugService.buildOverrides(set, workspaceRoot);



        console.log(`[DEBUG PyOpsPythonDebugConfigProvider] Built overrides for ${set}: ${Object.keys(overrides).length} files`);
        console.log(`[DEBUG PyOpsPythonDebugConfigProvider] Path mappings:`, pathMappings);

        // Enqueue debug job
        const { jobId, port } = await this._debugService.enqueueDebugJob(set, scriptlet, overrides);
        if (!jobId) {
            vscode.window.showErrorMessage('Failed to enqueue PyOps debug job');
            return undefined;
        }

        // Return attach config
        const debugConfig = {
            name: `Attach PyOps ${set}::${scriptlet}`,
            type: 'python',
            request: 'attach',
            connect: { host: 'localhost', port },
            path: uri.fsPath,
            pathMappings,
            jobId
        };
        console.log(`[DEBUG PyOpsPythonDebugConfigProvider] Returning config:`, debugConfig);
        return debugConfig;
    }
}
