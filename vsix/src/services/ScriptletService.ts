import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ApiClient } from '../apiClient';
import { errorText } from '../errorText';
import { ManagerTreeDataProvider, ScriptletTreeItem } from '../managerTreeProvider';
import { RemoteContentProvider } from '../providers/RemoteContentProvider';
import { inferExt } from '../utils';


export class ScriptletService {
    constructor(private apiClient: ApiClient, private managerTreeProvider: ManagerTreeDataProvider, private remoteProvider: RemoteContentProvider) {}

    private getWorkspaceRoot(): string | undefined {
        return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    }

    private getLocalPath(treeItem: ScriptletTreeItem, workspaceRoot: string, localPyopsDir: string): string {
        // Pass treeItem.scriptletExt as hint so data-type yaml/json files
        // mirror to the right local extension; non-data types ignore the
        // hint (it's just `undefined` for them).
        const ext = inferExt(treeItem.scriptletType!, treeItem.scriptletExt);
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
                const workspaceRoot = this.getWorkspaceRoot();
                if (!workspaceRoot) {
                    vscode.window.showWarningMessage('No workspace folder open');
                    return;
                }
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
                vscode.window.showErrorMessage(`Failed to fetch remote: ${await errorText(response)}`);
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
        const workspaceRoot = this.getWorkspaceRoot();
        if (!workspaceRoot) {
            vscode.window.showWarningMessage('No workspace folder open');
            return;
        }
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
                    vscode.window.showErrorMessage(`Failed to auto-create remote scriptlet ${name}: ${await errorText(createResponse)}`);
                    return;
                }
                vscode.window.showInformationMessage(`Auto-created remote ${name}; writing draft.`);
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Network error auto-creating remote scriptlet ${name}: ${errorMessage}`);
                return;
            }
        } else if (!checkResponse.ok) {
            vscode.window.showErrorMessage(`Failed to check remote existence: ${await errorText(checkResponse)}`);
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
                vscode.window.showErrorMessage(`Failed to write draft: ${await errorText(response)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error writing draft: ${errorMessage}`);
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
                const workspaceRoot = this.getWorkspaceRoot();
                if (!workspaceRoot) {
                    vscode.window.showWarningMessage('No workspace folder open');
                    return;
                }
                const pyopsConfig = vscode.workspace.getConfiguration('pyops');
                const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
                const localPath = this.getLocalPath(treeItem, workspaceRoot, localPyopsDir);
                if (!fs.existsSync(localPath)) {
                    vscode.window.showWarningMessage('Local file not found; create first');
                    return;
                }
                const uriPath = `/${set}/${name}`;
                this.remoteProvider.setContent(uriPath, code);
                // Pass `ext` query param so RemoteContentProvider's language
                // setter picks `json` vs `yaml` for data files. Without it,
                // the data-type branch defaults to yaml regardless of the
                // actual on-disk format.
                const extParam = treeItem.scriptletExt ? `&ext=${treeItem.scriptletExt}` : '';
                const remoteUri = vscode.Uri.parse(`pyops-remote:${uriPath}?type=${treeItem.scriptletType!}${extParam}`);
                const localUri = vscode.Uri.file(localPath);
                await vscode.commands.executeCommand('vscode.diff', remoteUri, localUri, 'Remote vs Local');
            } else {
                vscode.window.showErrorMessage(`Failed to fetch remote for diff: ${await errorText(response)}`);
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
        const workspaceRoot = this.getWorkspaceRoot();
        if (!workspaceRoot) {
            vscode.window.showWarningMessage('No workspace folder open');
            return;
        }
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
                vscode.window.showErrorMessage(`Failed to delete remote: ${await errorText(response)}`);
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`Error deleting scriptlet: ${errorMessage}`);
        }
    }


    async openLocalFile(treeItem?: ScriptletTreeItem): Promise<void> {
        if (!treeItem || !treeItem.set || !treeItem.scriptletName || !treeItem.scriptletType) return;
        const workspaceRoot = this.getWorkspaceRoot();
        if (!workspaceRoot) {
            vscode.window.showWarningMessage('No workspace folder open');
            return;
        }
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
