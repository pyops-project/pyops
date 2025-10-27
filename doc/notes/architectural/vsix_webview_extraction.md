# VSIX Webview Provider Extraction

## Overview
Extracted PyOpsWebviewProvider from inline extension.ts to providers/webviewProvider.ts for modularity. Preserves logic: resolveWebviewView sets webview.html via _getHtmlForWebview (fetch HTML, rewrite paths, inject viewId).

## Key Changes
- **File Creation**: New providers/webviewProvider.ts with class export; imports vscode, fetch, Buffer.
- **Renaming**: PyOpsWebviewViewProvider → PyOpsWebviewProvider (simpler, no 'View' suffix).
- **Dependencies**: Relies on config for serverUrl/headers; no direct state/jobManager ties.
- **Testing**: Unit in providers.test.ts: Mock fetch response with sample HTML, assert replaced paths (e.g., "/files/css/bootstrap/" → "http://server/files/css/bootstrap/"), viewId presence.

## Interactions
- Fetches from app /sites/scriptlet/*.html.
- Used in extension.ts: new PyOpsWebviewProvider(context.extensionUri, 'pyopsManager', 'PyOps Manager', context); registerWebviewViewProvider.
- No breaking changes; test gate ensures equivalence.

## Best Practices Applied
- Isolation: Mocks prevent real HTTP; sinon for vscode APIs.
- Coverage: >80% for new methods (nyc report).
