import * as vscode from 'vscode';

export function inferExt(type: string): string {
    switch (type) {
        case 'task':
        case 'library':
        case 'controller':
            return 'py';
        case 'view':
            return 'html';
        case 'markdown':
            return 'md';
        default:
            return '';
    }
}

export function hasValidConfig(): boolean {
    const pyopsConfig = vscode.workspace.getConfiguration('pyops');
    const serverUrl = pyopsConfig.get('serverUrl') as string || '';
    return !!serverUrl;
}
