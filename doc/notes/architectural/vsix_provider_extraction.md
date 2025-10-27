# VSIX Provider Extraction Patterns

## Overview
Extraction of tree providers (e.g., ManagerTreeDataProvider) from extension.ts to providers/*.ts for modularity. Preserves logic: getChildren builds UI, fetch/download via API with utils.

## Key Changes (ManagerTreeDataProvider Example)
- **File Creation**: New providers/managerTreeProvider.ts with class/interface export; imports vscode, fs/path/fetch/Buffer, utils.
- **No Renaming**: Retains ManagerTreeDataProvider/ScriptletTreeItem.
- **Dependencies**: Relies on config (via headers), utils for icons/ext/headers; fs for downloads.
- **Testing**: Unit in providers.test.ts: Mock fetch/fs, assert tree array, file writes, icon paths.

## Interactions
- Fetches from app /scriptlet/inventory/*.
- Used in extension.ts: new ManagerTreeDataProvider(context); registerTreeDataProvider.
- No breaking changes; test gate ensures tree population/downloads work.

## Best Practices Applied
- Isolation: Mocks for all external (fetch/fs/vscode); no real API/FS.
- Coverage: >80% for methods (nyc); edges (empty response, errors).
