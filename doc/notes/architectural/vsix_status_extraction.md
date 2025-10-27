# VSIX Status Extraction Patterns

## Overview
Extraction of status bar logic from extension.ts to status/statusBar.ts for modularity. Preserves connection checks, config listeners, and refresh command.

## Key Changes (StatusBarManager Example)
- **File Creation**: New status/statusBar.ts with class export; imports vscode.
- **No Renaming**: Retains updateConnectionStatus logic as method.
- **Dependencies**: Relies on workspace config, context for state/subscriptions, outputChannel for logs.
- **Testing**: Unit in status.test.ts: Mock vscode.window.createStatusBarItem, workspace.getConfiguration, fetch (inject or sinon), assert text/color/show calls.

## Interactions
- Fetches server root for connection (inline auth with Buffer.from).
- Used in extension.ts: new StatusBarManager(context, outputChannel); statusBarManager.init(); deactivate: statusBarManager.dispose().
- No breaking changes; test gate ensures status updates on config change/refresh.

## Best Practices Applied
- Isolation: Mocks for vscode APIs, fetch; no real config/FS.
- Coverage: >80% for methods (nyc); edges (no config, fetch fail, ok response).
