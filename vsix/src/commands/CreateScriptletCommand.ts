/// <reference types="node" />
import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { Uri } from 'vscode';
import { ManagerTreeDataProvider } from '../managerTreeProvider';
import { inferExt } from '../utils';

export class CreateScriptletCommand {
    static async execute(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel, managerTreeProvider: ManagerTreeDataProvider) {
        const types: vscode.QuickPickItem[] = [
            { label: 'Task', description: 'Python task script' },
            { label: 'Library', description: 'Python library module' },
            { label: 'Controller', description: 'Python controller' },
            { label: 'View', description: 'HTML view template' },
            { label: 'JS Library', description: 'JavaScript library module' },
            { label: 'Markdown', description: 'Markdown document (README only)' }
        ];
        const typePick = await vscode.window.showQuickPick(types, { placeHolder: 'Select scriptlet type' });
        if (!typePick) return;

        let typeLower = typePick.label.toLowerCase();
        if (typeLower === 'js library') typeLower = 'jslibrary';

        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const serverUrl = pyopsConfig.get('serverUrl') as string || 'http://localhost:8000';
        const username = pyopsConfig.get('username') as string;
        const password = context.workspaceState.get('pyops.password') as string;

        const headers: Record<string, string> = {};
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            headers['Authorization'] = `Basic ${auth}`;
        }

        // Fetch existing sets
        outputChannel.appendLine(`[Create Scriptlet] Fetching existing sets...`);
        let scriptlets: unknown[] = [];
        try {
            const response = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
            outputChannel.appendLine(`[Create Scriptlet] Sets list fetch status: ${response.status} ${response.statusText}`);
            if (response.ok) {
                scriptlets = await response.json() as unknown[];
                const existingSets = [...new Set(scriptlets.map((s: unknown) => (s as { set: string }).set))].sort();
                outputChannel.appendLine(`[Create Scriptlet] Found sets: ${JSON.stringify(existingSets)}`);
                if (existingSets.length === 0) {
                    vscode.window.showErrorMessage('No existing sets found. Cannot create scriptlet without a set.');
                    outputChannel.appendLine(`[Create Scriptlet] No sets available.`);
                    return;
                }

                const setItems: vscode.QuickPickItem[] = existingSets.map(g => ({ label: g }));
                const setPick = await vscode.window.showQuickPick(setItems, { placeHolder: 'Select set' });
                if (!setPick) return;

                const set = setPick.label;
                outputChannel.appendLine(`[Create Scriptlet] Selected set: ${set}`);

                // Fetch scriptlets for selected set to check names
                const setScriptlets = scriptlets.filter((s: unknown) => (s as { set: string }).set === set);
                const existingNames = new Set(setScriptlets.map((s: unknown) => (s as { name: string }).name));
                outputChannel.appendLine(`[Create Scriptlet] Existing names in set '${set}': ${JSON.stringify(Array.from(existingNames))}`);

                // Loop for unique name
                let name: string | undefined;
                while (!name) {
                    const nameInput: vscode.InputBoxOptions = {
                        prompt: 'Enter unique name'
                    };
                    name = await vscode.window.showInputBox(nameInput);
                    if (!name) return; // User cancelled
                    if (existingNames.has(name)) {
                        vscode.window.showWarningMessage(`Name '${name}' already exists. Please choose a different name.`);
                        name = undefined; // Force loop
                    } else {
                        const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                        const ext = inferExt(typeLower);
                        const localPath = path.join(workspaceRoot, localPyopsDir, set, typeLower, `${name}.${ext}`);
                        if (fs.existsSync(localPath)) {
                            vscode.window.showWarningMessage(`Local file '${name}.${ext}' already exists. Please choose a different name.`);
                            name = undefined;
                        }
                    }

                }
                outputChannel.appendLine(`[Create Scriptlet] Selected unique name: ${name}`);

                if (typeLower === 'markdown' && name !== 'README') {
                    vscode.window.showErrorMessage('Markdown only allowed for README');
                    return;
                }

                outputChannel.appendLine(`[Create Scriptlet] Creating local ${typeLower} '${set}::${name}'...`);
                const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                const ext = inferExt(typeLower);
                const localPath = path.join(workspaceRoot, localPyopsDir, set, typeLower, `${name}.${ext}`);
                const dirPath = path.dirname(localPath);
                if (!fs.existsSync(dirPath)) {
                    fs.mkdirSync(dirPath, { recursive: true });
                }
                let template = '';
                if (ext === 'py') {
                    template = 'pass';
                }
                fs.writeFileSync(localPath, template);
                outputChannel.appendLine(`[Create Scriptlet] Created local file: ${localPath}`);
                managerTreeProvider.refresh();
                vscode.window.showInformationMessage(`Created local ${typeLower} ${set}::${name}`);
                const localUri = Uri.file(localPath);
                await vscode.workspace.openTextDocument(localUri).then(doc => vscode.window.showTextDocument(doc));

            } else {
                const errorMsg = `Failed to fetch sets: ${response.statusText}`;
                vscode.window.showErrorMessage(errorMsg);
                outputChannel.appendLine(`[Create Scriptlet] Sets fetch failed: ${errorMsg}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            const errorMsg = `Error fetching sets: ${errorMessage}`;
            vscode.window.showErrorMessage(errorMsg);
            outputChannel.appendLine(`[Create Scriptlet] Sets fetch error: ${errorMsg}`);
        }
    }
}
