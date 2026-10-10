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
    // For type='data' the on-disk extension ('yaml' | 'yml' | 'json'),
    // propagated from `ScriptletInfo.ext`. Local mirror naming, diff URI
    // language mode, and inferExt() calls all use this so yaml vs json
    // round-trip correctly.
    scriptletExt?: string;
}

interface ScriptletInfo {
    set: string;
    name: string;
    type: string;
    // For type='data' the server reports the on-disk extension
    // ('yaml' | 'yml' | 'json') so the client mirror filename and
    // editor language mode match the remote source.
    ext?: string;
    isLocalOnly?: boolean;
}

class ManagerTreeDataProvider implements TreeDataProvider<TreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<TreeItem | undefined | null | void> = new EventEmitter<TreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<TreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    private _context: vscode.ExtensionContext;
    private _outputChannel: vscode.OutputChannel;
    private _apiClient: ApiClient;
    private _scriptlets: ScriptletInfo[] = [];
    private _syncStatus: Map<string, boolean> = new Map();
    // Overlapping refreshes (auto-refresh timer + refresh command) can answer out
    // of order; only the most recently started one may update the tree.
    private _refreshSeq: number = 0;
    private _lastScriptletErrorShown: number = 0;

    constructor(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel, apiClient: ApiClient) {
        this._context = context;
        this._outputChannel = outputChannel;
        this._apiClient = apiClient;
        this.refresh();
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
                const setItems = this._scriptlets.filter(s => s.set === set);
                const itemTrees: TreeItem[] = setItems.map(item => {
                    const itemObj = item;
                    const label = `${itemObj.name} (${itemObj.type})`;
                    const ext = inferExt(itemObj.type, itemObj.ext);
                    const key = `${set}-${itemObj.name}-${itemObj.type}`;
                    const treeItem = new TreeItem(label, vscode.TreeItemCollapsibleState.None) as ScriptletTreeItem;
                    treeItem.description = ext ? `.${ext}` : '';
                    treeItem.id = key;
                    treeItem.set = set;
                    treeItem.scriptletName = itemObj.name;
                    treeItem.scriptletType = itemObj.type;
                    treeItem.scriptletExt = itemObj.ext;
                    treeItem.command = { command: 'pyops.openLocalFile', title: 'Open Local File', arguments: [treeItem] };
                    if (itemObj.isLocalOnly) {
                        treeItem.iconPath = new vscode.ThemeIcon('diff-added');
                        treeItem.contextValue = `pyopsItem-${itemObj.type}-local`;
                        treeItem.tooltip = `Local only - Set: ${itemObj.set}, Name: ${itemObj.name}, Type: ${itemObj.type}`;
                        treeItem.description += ' (local)';
                    } else {
                        const isSynced = this._syncStatus.get(key) ?? true;
                        treeItem.iconPath = isSynced ? new vscode.ThemeIcon('check') : new vscode.ThemeIcon('warning');
                        treeItem.contextValue = `pyopsItem-${itemObj.type}`;
                        treeItem.tooltip = `Set: ${itemObj.set}, Name: ${itemObj.name}, Type: ${itemObj.type}, Sync: ${isSynced ? 'Synced' : 'Unsynced'}`;
                    }
                    return treeItem;
                });

                return itemTrees.sort((a, b) => (a.label as string).localeCompare(b.label as string));
            }
            return [];
        } else {
            // Root: sets only (commands via toolbar)
            if (this._scriptlets.length === 0 && isValidConfig) {
                await this.refresh();
            }
            if (this._scriptlets.length === 0) {
                return [new TreeItem('No scriptlets available', vscode.TreeItemCollapsibleState.None)];
            }

            const sets = [...new Set(this._scriptlets.map(s => s.set))].sort();
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

    private async fetchScriptlets(): Promise<ScriptletInfo[]> {
        try {
            const response = await this._apiClient.fetch('/scriptlet/inventory/list');
            if (response.ok) {
                this._lastScriptletErrorShown = 0;
                const scriptlets = await response.json();
                return Array.isArray(scriptlets) ? scriptlets as ScriptletInfo[] : [];

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

    private async downloadScriptlets(scriptlets: ScriptletInfo[]): Promise<void> {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) return;

        this._syncStatus.clear();

        const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
        const localPyopsDir = vscode.workspace.getConfiguration('pyops').get('localPyopsDir', 'pyops') as string;
        const baseLocalPath = path.join(workspaceRoot, localPyopsDir);

        const downloadPromises = scriptlets.map(async (s) => {
            const sObj = { set: s.set, name: s.name, type: s.type, ext: s.ext };
            const key = `${sObj.set}-${sObj.name}-${sObj.type}`;
            try {
                const response = await this._apiClient.fetch(`/scriptlet/inventory/code?set=${sObj.set}&name=${sObj.name}`);
                if (response.ok) {
                    const data = await response.json() as {code: string};
                    const remoteCode = data.code;
                    const ext = inferExt(sObj.type, sObj.ext);
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

    private getBaseLocalPath(): string {
        if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
            throw new Error('No workspace open');
        }
        const workspaceRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
        const localPyopsDir = vscode.workspace.getConfiguration('pyops').get('localPyopsDir', 'pyops') as string;
        return path.join(workspaceRoot, localPyopsDir);
    }

    private async scanLocalScriptlets(): Promise<ScriptletInfo[]> {
        const baseLocalPath = this.getBaseLocalPath();
        if (!fs.existsSync(baseLocalPath)) {
            return [];
        }
        const localScriptlets: ScriptletInfo[] = [];
        const setDirs = fs.readdirSync(baseLocalPath).filter(dir => {
            const dirPath = path.join(baseLocalPath, dir);
            return fs.statSync(dirPath).isDirectory();
        }).sort();
        const knownTypes = ['task', 'service', 'library', 'controller', 'view', 'markdown', 'jslibrary', 'data'];
        for (const set of setDirs) {
            const setPath = path.join(baseLocalPath, set);
            for (const type of knownTypes) {
                const typePath = path.join(setPath, type);
                if (!fs.existsSync(typePath) || !fs.statSync(typePath).isDirectory()) continue;
                const files = fs.readdirSync(typePath);
                for (const file of files) {
                    const ext = path.extname(file);
                    if (type === 'data') {
                        // yaml/yml/json are all valid for data; record the
                        // actual extension so the tree shows the right
                        // language icon and round-trip writes preserve it.
                        if (ext === '.yaml' || ext === '.yml' || ext === '.json') {
                            const name = path.basename(file, ext);
                            localScriptlets.push({ set, name, type, ext: ext.slice(1), isLocalOnly: true });
                        }
                        continue;
                    }
                    const expectedExtNoDot = inferExt(type);
                    const expectedExt = `.${expectedExtNoDot}`;
                    if (ext === expectedExt) {
                        const name = path.basename(file, ext);
                        localScriptlets.push({ set, name, type, isLocalOnly: true });
                    }
                }
            }
        }
        return localScriptlets;
    }

    private getExcludedSets(): string[] {
        return vscode.workspace.getConfiguration('pyops').get('excludedSets', []) as string[];
    }

    private getIncludedSets(): string[] {
        return vscode.workspace.getConfiguration('pyops').get('includedSets', []) as string[];
    }

    // Whitelist wins: if `includedSets` is non-empty, show only those sets and
    // ignore `excludedSets`; otherwise fall back to the `excludedSets` blacklist.
    private filterSets(scriptlets: ScriptletInfo[]): ScriptletInfo[] {
        const includedSets = this.getIncludedSets();
        if (includedSets.length > 0) {
            return scriptlets.filter(s => includedSets.includes(s.set));
        }
        const excludedSets = this.getExcludedSets();
        return scriptlets.filter(s => !excludedSets.includes(s.set));
    }


    async downloadScriptlet(scriptletItem: ScriptletTreeItem): Promise<void> {
        if (!scriptletItem.set || !scriptletItem.scriptletName || !scriptletItem.scriptletType) return;

        const sObj = { set: scriptletItem.set, name: scriptletItem.scriptletName, type: scriptletItem.scriptletType };
        const key = `${sObj.set}-${sObj.name}-${sObj.type}`;
        try {
            const response = await this._apiClient.fetch(`/scriptlet/inventory/code?set=${sObj.set}&name=${sObj.name}`);
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
        const seq = ++this._refreshSeq;
        const isValidConfig = this._apiClient.hasValidConfig();
        if (!isValidConfig) {
            this._scriptlets = [];
            this._onDidChangeTreeData.fire();
            return;
        }

        const remoteScriptlets = await this.fetchScriptlets();
        const remoteKeys = new Set(remoteScriptlets.map(s => `${s.set}-${s.name}-${s.type}`));
        const localScriptlets = await this.scanLocalScriptlets();
        if (seq !== this._refreshSeq) {
            return;
        }
        const localOnlyScriptlets = localScriptlets.filter(l => !remoteKeys.has(`${l.set}-${l.name}-${l.type}`));
        const allScriptlets = this.filterSets([...remoteScriptlets, ...localOnlyScriptlets]);
        this._scriptlets = allScriptlets;
        this._syncStatus.clear();
        const remoteOnly = allScriptlets.filter(s => !s.isLocalOnly);
        if (remoteOnly.length > 0) {
            await this.downloadScriptlets(remoteOnly);
        }
        this._onDidChangeTreeData.fire();
    }

}

export { ManagerTreeDataProvider, ScriptletTreeItem };
