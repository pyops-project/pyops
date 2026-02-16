import * as vscode from 'vscode';

export class RemoteContentProvider implements vscode.TextDocumentContentProvider {
    private _onDidChange = new vscode.EventEmitter<vscode.Uri>();

    get onDidChange(): vscode.Event<vscode.Uri> {
        return this._onDidChange.event;
    }

    provideTextDocumentContent(uri: vscode.Uri): vscode.ProviderResult<string> {
        if (uri.fragment) {
            try {
                return decodeURIComponent(uri.fragment);
            } catch (e: unknown) {
                const errorMessage = e instanceof Error ? e.message : String(e);
                return `Error decoding content: ${errorMessage}`;
            }
        }
        return "No content provided";
    }
}

export function getLanguageId(type: string): string | undefined {
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
        default:
            return undefined;
    }
}

export function registerRemoteLanguageModeSetter(): vscode.Disposable {
    return vscode.workspace.onDidOpenTextDocument((document: vscode.TextDocument) => {
        if (document.uri.scheme === 'pyops-remote') {
            const params = new URLSearchParams(document.uri.query);
            const type = params.get('type');
            const languageId = type ? getLanguageId(type) : undefined;
            if (languageId) {
                vscode.languages.setTextDocumentLanguage(document, languageId);
            }
        }
    });
}
