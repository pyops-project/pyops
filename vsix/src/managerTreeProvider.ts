import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { TreeDataProvider, TreeItem, EventEmitter } from 'vscode';
import { inferExt } from './utils';
import { ApiClient } from './apiClient';

interface ScriptletTreeItem extends TreeItem {
    set?: string;
    scriptletName?: string;
    scriptletType?: string;
}

class ManagerTreeDataProvider implements TreeDataProvider<TreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<TreeItem | undefined | null | void> = new EventEmitter<TreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<TreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    private _context: vscode.ExtensionContext;
    private _outputChannel: vscode.OutputChannel;
    private _apiClient: ApiClient;
    private _scriptlets: unknown[] = [];
    private _syncStatus: Map<string, boolean> = new Map();
    private _lastScriptletErrorShown: number = 0;

    constructor(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel, apiClient: ApiClient) {
        this._context = context;
        this._outputChannel = outputChannel;
        this._apiClient = apiClient;
        this.refresh();
    }

    private async getHeaders(): Promise<Record<string, string>> {
        return this._apiClient.getHeaders();
    }

    private getServerUrl(): string {
        return this._apiClient.getServerUrl();
    }

    getTreeItem(element: TreeItem): TreeItem {
        return element;
    }

    async getChildren(element?: TreeItem): Promise<TreeItem[]> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
            return [new TreeItem('No Workspace Open', vscode.TreeItemCollapsibleState.None)];
        }

        const isValidConfig = this._apiClient.hasValidConfig();
        if (!isValidConfig && !element) {
            const msgItem = new TreeItem('No PyOps serverUrl set. Please configure serverUrl in settings.', vscode.TreeItemCollapsibleState.None);
            msgItem.contextValue = 'noConfig';
            msgItem.iconPath = new vscode.ThemeIcon('warning');
            msgItem.tooltip = 'Open settings (Ctrl+,) to configure serverUrl.';
            return [msgItem];
        }

        if (element) {
            if (element.contextValue === 'commands') {
                // Children of commands node
                const createItem = new TreeItem('Create Scriptlet', vscode.TreeItemCollapsibleState.None);
                createItem.command = { command: 'pyops.createScriptlet', title: 'Create Scriptlet' };
                const refreshItem = new TreeItem('Refresh', vscode.TreeItemCollapsibleState.None);
                refreshItem.command = { command: 'pyops.refreshManager', title: 'Refresh' };
                return [createItem, refreshItem];
            } else if (element.contextValue?.startsWith('pyopsSet')) {
                // Children of set: items (2-level: set > name (type))
                const set = element.label as string;
                const setItems = this._scriptlets.filter((s: unknown) => (s as { set: string }).set === set);
                const itemTrees: TreeItem[] = setItems.map((item: unknown) => {
                    const itemObj = item as { name: string; type: string };
                    const label = `${itemObj.name} (${itemObj.type})`;
                    const ext = inferExt(itemObj.type);
                    const key = `${set}-${itemObj.name}-${itemObj.type}`;
                    const isSynced = this._syncStatus.get(key) ?? true;
                    const treeItem = new TreeItem(label, vscode.TreeItemCollapsibleState.None) as ScriptletTreeItem;
                    treeItem.description = ext ? `.${ext}` : '';
                    treeItem.contextValue = `pyopsItem-${itemObj.type}`;
                    treeItem.id = key;
                    treeItem.iconPath = isSynced ? new vscode.ThemeIcon('check') : new vscode.ThemeIcon('warning');
                    treeItem.tooltip = `Set: ${set}, Name: ${itemObj.name}, Type: ${itemObj.type}, Sync: ${isSynced ? 'Synced' : 'Unsynced'}`;
                    treeItem.set = set;
                    treeItem.scriptletName = itemObj.name;
                    treeItem.scriptletType = itemObj.type;
                    treeItem.command = { command: 'pyops.openLocalFile', title: 'Open Local File', arguments: [treeItem] };
                    return treeItem;
                });
                return itemTrees.sort((a, b) => (a.label as string).localeCompare(b.label as string));
            }
            return [];
        } else {
            // Root: sets only (commands via toolbar)
            if (this._scriptlets.length === 0) {
                if (isValidConfig) {
                    await this.fetchScriptlets();
                    await this.downloadScriptlets(this._scriptlets);
                }
                if (this._scriptlets.length === 0) {
                    return [new TreeItem('No scriptlets available', vscode.TreeItemCollapsibleState.None)];
                }
            }

            const sets = [...new Set(this._scriptlets.map((s: unknown) => (s as { set: string }).set))].sort();
            const setItems: TreeItem[] = sets.map(g => {
                const gi = new TreeItem(g, vscode.TreeItemCollapsibleState.Collapsed);
                gi.contextValue = 'pyopsSet';
                gi.iconPath = new vscode.ThemeIcon('folder');
                gi.id = `set-${g}`;
                return gi;
            });

            return setItems;
        }
    }

    private async fetchScriptlets(): Promise<unknown[]> {
        const serverUrl = this.getServerUrl();
        const headers = await this.getHeaders();

        try {
            const response = await fetch(`${serverUrl}/scriptlet/inventory/list`, { headers });
            if (response.ok) {
                this._lastScriptletErrorShown = 0;
                return await response.json() as unknown[];
            } else {
                const now = Date.now();
                if (now - this._lastScriptletErrorShown > 30000) {
                    vscode.window.showErrorMessage(`Failed to fetch scriptlets: ${response.statusText}`);
                    this._lastScriptletErrorShown = now;
                }
                this._outputChannel.appendLine(`Failed to fetch scriptlets: ${response.statusText}`);
                return [];
            }
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            const now = Date.now();
            if (now - this._lastScriptletErrorShown > 30000) {
                vscode.window.showErrorMessage(`Error fetching scriptlets: ${errorMessage}`);
                this._lastScriptletErrorShown = now;
            }
            this._outputChannel.appendLine(`Error fetching scriptlets: ${errorMessage}`);
            return [];
        }
    }

    private async downloadScriptlets(scriptlets: unknown[]): Promise<void> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) return;

        this._syncStatus.clear();

        const serverUrl = this.getServerUrl();
        const headers = await this.getHeaders();

        const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
        const localPyopsDir = vscode.workspace.getConfiguration('pyops').get('localPyopsDir', 'pyops') as string;
        const baseLocalPath = path.join(workspaceRoot, localPyopsDir);

        const downloadPromises = scriptlets.map(async (s: unknown) => {
            const sObj = s as { set: string; name: string; type: string };
            const key = `${sObj.set}-${sObj.name}-${sObj.type}`;
            try {
                const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${sObj.set}&name=${sObj.name}`, { headers });
                if (response.ok) {
                    const data = await response.json() as {code: string};
                    const remoteCode = data.code;
                    const ext = inferExt(sObj.type);
                    const dirPath = path.join(baseLocalPath, sObj.set, sObj.type);
                    if (!fs.existsSync(dirPath)) {
                        fs.mkdirSync(dirPath, { recursive: true });
                    }
                    const localPath = path.join(dirPath, `${sObj.name}.${ext}`);
                    let isSynced = true;
                    if (fs.existsSync(localPath)) {
                        const localCode = fs.readFileSync(localPath, 'utf8');
                        isSynced = localCode.trim() === remoteCode.trim();
                    }
                    if (!fs.existsSync(localPath)) {
                        fs.writeFileSync(localPath, remoteCode);
                    }
                    this._syncStatus.set(key, isSynced);
                } else {
                    this._syncStatus.set(key, false);
                    console.error(`Failed to fetch ${sObj.set}::${sObj.name}: ${response.statusText}`);
                }
            } catch (error: unknown) {
                this._syncStatus.set(key, false);
                const errorMessage = error instanceof Error ? error.message : String(error);
                console.error(`Error downloading ${sObj.set}::${sObj.name}:`, errorMessage);
            }
        });

        await Promise.all(downloadPromises);
    }

    private getExcludedSets(): string[] {
        return vscode.workspace.getConfiguration('pyops').get('excludedSets', []) as string[];
    }

    async downloadScriptlet(scriptletItem: ScriptletTreeItem): Promise<void> {
        if (!scriptletItem.set || !scriptletItem.scriptletName || !scriptletItem.scriptletType) return;

        const serverUrl = this.getServerUrl();
        const headers = await this.getHeaders();

        const sObj = { set: scriptletItem.set, name: scriptletItem.scriptletName, type: scriptletItem.scriptletType };
        const key = `${sObj.set}-${sObj.name}-${sObj.type}`;
        try {
            const response = await fetch(`${serverUrl}/scriptlet/inventory/code?set=${sObj.set}&name=${sObj.name}`, { headers });
            if (response.ok) {
                const data = await response.json() as {code: string};
                const remoteCode = data.code;
                const ext = inferExt(sObj.type);
                if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) return;
                const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
                const localPyopsDir = vscode.workspace.getConfiguration('pyops').get('localPyopsDir', 'pyops') as string;
                const dirPath = path.join(workspaceRoot, localPyopsDir, sObj.set, sObj.type);
                if (!fs.existsSync(dirPath)) {
                    fs.mkdirSync(dirPath, { recursive: true });
                }
                const localPath = path.join(dirPath, `${sObj.name}.${ext}`);
                fs.writeFileSync(localPath, remoteCode);
                this._syncStatus.set(key, true);
            } else {
                this._syncStatus.set(key, false);
                console.error(`Failed to fetch ${sObj.set}::${sObj.name}: ${response.statusText}`);
            }
        } catch (error: unknown) {
            this._syncStatus.set(key, false);
            const errorMessage = error instanceof Error ? error.message : String(error);
            console.error(`Error downloading ${sObj.set}::${sObj.name}:`, errorMessage);
        }
    }

    async refresh(): Promise<void> {
        const isValidConfig = this._apiClient.hasValidConfig();
        if (!isValidConfig) {
            this._scriptlets = [];
            this._onDidChangeTreeData.fire();
            return;
        }

        const excludedSets = this.getExcludedSets();
        const scriptlets = await this.fetchScriptlets();
        this._scriptlets = scriptlets.filter((s: unknown) => !excludedSets.includes((s as { set: string }).set));
        if (this._scriptlets.length > 0) {
            await this.downloadScriptlets(this._scriptlets);
        }
        this._onDidChangeTreeData.fire();
    }
}

export { ManagerTreeDataProvider, ScriptletTreeItem };
