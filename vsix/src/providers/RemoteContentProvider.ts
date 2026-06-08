import * as vscode from 'vscode';

export class RemoteContentProvider implements vscode.TextDocumentContentProvider {
    private _onDidChange = new vscode.EventEmitter<vscode.Uri>();
    private contentMap = new Map<string, string>();

    get onDidChange(): vscode.Event<vscode.Uri> {
        return this._onDidChange.event;
    }

    setContent(uriPath: string, content: string): void {
        this.contentMap.set(uriPath, content);
    }

    provideTextDocumentContent(uri: vscode.Uri): vscode.ProviderResult<string> {
        const mapped = this.contentMap.get(uri.path);
        if (mapped !== undefined) {
            return mapped;
        }
        if (!uri.fragment) {
            return "No content provided";
        }
        try {
            return decodeURIComponent(uri.fragment);
        } catch {
            return `Error decoding content: ${uri.fragment}`;
        }
    }
}

export function getLanguageId(type: string, ext?: string): string | undefined {
    switch (type) {
        case 'task':
        case 'library':
        case 'controller':
            return 'python';
        case 'view':
            return 'html';
        case 'markdown':
            return 'markdown';
        case 'jslibrary':
            return 'javascript';
        case 'data':
            if (ext === 'json') return 'json';
            // yaml is the default for new data files; yml folds in too
            return 'yaml';
        default:
            return undefined;
    }
}

export function registerRemoteLanguageModeSetter(): vscode.Disposable {
    return vscode.workspace.onDidOpenTextDocument((document: vscode.TextDocument) => {
        if (document.uri.scheme === 'pyops-remote') {
            const params = new URLSearchParams(document.uri.query);
            const type = params.get('type');
            const ext = params.get('ext') || undefined;
            const languageId = type ? getLanguageId(type, ext) : undefined;
            if (languageId) {
                vscode.languages.setTextDocumentLanguage(document, languageId);
            }
        }
    });
}
