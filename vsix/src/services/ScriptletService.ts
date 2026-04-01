import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ApiClient } from '../apiClient';
import { ManagerTreeDataProvider, ScriptletTreeItem } from '../managerTreeProvider';
import { inferExt } from '../utils';


export class ScriptletService {
    constructor(private apiClient: ApiClient, private managerTreeProvider: ManagerTreeDataProvider) {}

    private getLocalPath(treeItem: ScriptletTreeItem, workspaceRoot: string, localPyopsDir: string): string {
        const ext = inferExt(treeItem.scriptletType!);
        return path.join(workspaceRoot, localPyopsDir, treeItem.set!, treeItem.scriptletType!, `${treeItem.scriptletName!}.${ext}`);
    }

    async resetLocalFromRemote(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const set = treeItem.set!;
        const name = treeItem.scriptletName!;
        try {
            const response = await this.apiClient.fetch(`/scriptlet/inventory/code?set=${set}&name=${name}`);
            if (response.ok) {
                const responseData = await response.json();
                const code = (responseData as { code?: string }).code || '';
                const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                const pyopsConfig = vscode.workspace.getConfiguration('pyops');
                const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
                const dirPath = path.dirname(localPath);
                if (!fs.existsSync(dirPath)) {
                    fs.mkdirSync(dirPath, { recursive: true });
                }
                fs.writeFileSync(localPath, code);
                vscode.window.showInformationMessage(`Reset local ${name}.${inferExt(treeItem.scriptletType!)} from remote`);
                this.managerTreeProvider.refresh();
            } else {
                vscode.window.showErrorMessage(`Failed to fetch remote: ${response.statusText}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error resetting local: ${errorMessage}`);
        }
    }

    async writeLocalToRemoteDraft(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!this.apiClient.hasValidConfig()) {
            vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
            return;
        }
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const set = treeItem.set!;
        const name = treeItem.scriptletName!;
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
        if (!fs.existsSync(localPath)) {
            vscode.window.showErrorMessage('Local file not found');
            return;
        }
        let code: string;
        try {
            code = fs.readFileSync(localPath, 'utf8');
        } catch {
            vscode.window.showErrorMessage('Error reading local file');
            return;
        }

        // Check if remote scriptlet exists; create if not
        let checkResponse: Response;
        try {
            checkResponse = await this.apiClient.fetch(`/scriptlet/inventory/code?set=${encodeURIComponent(set)}&name=${encodeURIComponent(name)}`);
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Network error checking remote existence: ${errorMessage}`);
            return;
        }
        if (checkResponse.status === 404) {
            try {
                const createData = { set, name, type: treeItem.scriptletType! };
                const createResponse = await this.apiClient.fetch('/scriptlet/inventory/item', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(createData)
                });
                if (!createResponse.ok) {
                    vscode.window.showErrorMessage(`Failed to auto-create remote scriptlet ${name}: ${createResponse.statusText}`);
                    return;
                }
                vscode.window.showInformationMessage(`Auto-created remote ${name}; writing draft.`);
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Network error auto-creating remote scriptlet ${name}: ${errorMessage}`);
                return;
            }
        } else if (!checkResponse.ok) {
            vscode.window.showErrorMessage(`Failed to check remote existence: ${checkResponse.statusText}`);
            return;
        }

        const data = { set, name, code };
        try {
            const response = await this.apiClient.fetch('/scriptlet/inventory/draft', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            if (response.ok) {
                vscode.window.showInformationMessage(`Wrote local changes to remote draft for ${name}`);
                this.managerTreeProvider.refresh();
            } else {
                vscode.window.showErrorMessage(`Failed to write draft: ${response.statusText}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error writing draft: ${errorMessage}`);
        }

    }

    async saveRemoteDraft(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!this.apiClient.hasValidConfig()) {
            vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
            return;
        }
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const set = treeItem.set!;
        const name = treeItem.scriptletName!;
        try {
            const statusResponse = await this.apiClient.fetch(`/scriptlet/inventory/status?set=${set}&name=${name}`);
            if (!statusResponse.ok) {
                vscode.window.showErrorMessage('Failed to check draft status');
                return;
            }
            const statusData = await statusResponse.json();
            const hasUnstaged = (statusData as { has_unstaged: boolean }).has_unstaged;
            if (!hasUnstaged) {
                vscode.window.showInformationMessage(`No remote draft to save for ${name}; no unstaged changes.`);
                return;
            }
            const codeResponse = await this.apiClient.fetch(`/scriptlet/inventory/code?set=${set}&name=${name}`);
            if (!codeResponse.ok) {
                vscode.window.showErrorMessage('No remote draft to save');
                return;
            }
            const codeResponseData = await codeResponse.json();
            const draftCode = (codeResponseData as { code: string }).code;
            const data = { set, name, code: draftCode };
            const saveResponse = await this.apiClient.fetch('/scriptlet/inventory/item', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            if (saveResponse.ok) {
                this.managerTreeProvider.refresh();
                vscode.window.showInformationMessage(`Saved remote draft for ${name}`);
            } else {
                vscode.window.showErrorMessage(`Failed to save: ${saveResponse.statusText}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error saving draft: ${errorMessage}`);
        }
    }

    async diffLocalRemote(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!this.apiClient.hasValidConfig()) {
            vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
            return;
        }
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const set = treeItem.set!;
        const name = treeItem.scriptletName!;
        try {
            const response = await this.apiClient.fetch(`/scriptlet/inventory/code?set=${set}&name=${name}`);
            if (response.ok) {
                const responseData = await response.json();
                const code = (responseData as { code: string }).code;
                const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
                const pyopsConfig = vscode.workspace.getConfiguration('pyops');
                const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
                if (!fs.existsSync(localPath)) {
                    vscode.window.showWarningMessage('Local file not found; create first');
                    return;
                }
                const encodedCode = encodeURIComponent(code);
                const remoteUri = vscode.Uri.parse(`pyops-remote:/${set}/${name}?type=${treeItem.scriptletType!}#${encodedCode}`);
                const localUri = vscode.Uri.file(localPath);
                await vscode.commands.executeCommand('vscode.diff', remoteUri, localUri, 'Remote vs Local');
            } else {
                vscode.window.showErrorMessage(`Failed to fetch remote for diff: ${response.statusText}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error in diff: ${errorMessage}`);
        }
    }

    async deleteScriptlet(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!this.apiClient.hasValidConfig()) {
            vscode.window.showWarningMessage('No valid PyOps configuration set. Please configure in settings.');
            return;
        }
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const set = treeItem.set!;
        const name = treeItem.scriptletName!;
        const type = treeItem.scriptletType!;
        const confirm = await vscode.window.showWarningMessage(
            `Delete scriptlet ${set}::${name} (${type})? This cannot be undone.`,
            { modal: true },
            'Delete'
        );
        if (confirm !== 'Delete') return;
        const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
        try {
            const response = await this.apiClient.fetch(`/scriptlet/inventory/item?set=${set}&name=${name}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                // remote deleted
                if (fs.existsSync(localPath)) {
                    try {
                        fs.unlinkSync(localPath);
                    } catch (unlinkError: unknown) {
                        console.error('Failed to delete local file:', unlinkError);
                    }
                }
                this.managerTreeProvider.refresh();
                vscode.window.showInformationMessage(`Deleted remote and local ${set}::${name} (${type})`);
            } else if (response.status === 404) {
                // local only
                if (fs.existsSync(localPath)) {
                    try {
                        fs.unlinkSync(localPath);
                    } catch (unlinkError: unknown) {
                        console.error('Failed to delete local file:', unlinkError);
                    }
                }
                this.managerTreeProvider.refresh();
                vscode.window.showInformationMessage(`Deleted local ${set}::${name} (${type})`);
            } else {
                const err = await response.text();
                vscode.window.showErrorMessage(`Failed to delete remote: ${err}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error deleting scriptlet: ${errorMessage}`);
        }
    }


    async openLocalFile(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const workspaceRoot = vscode.workspace.workspaceFolders![0].uri.fsPath;
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
        if (!fs.existsSync(localPath)) {
            vscode.window.showWarningMessage(`Local file not found: ${localPath}. Run refresh to download.`);
            return;
        }
        const localUri = vscode.Uri.file(localPath);
        await vscode.workspace.openTextDocument(localUri).then(doc => vscode.window.showTextDocument(doc));
    }
}
