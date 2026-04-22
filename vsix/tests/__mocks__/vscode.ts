export const workspace = {
  workspaceFolders: [{ uri: { fsPath: '/test/workspace' } }],
  getConfiguration: jest.fn(),
  onDidChangeConfiguration: jest.fn(),
  openTextDocument: jest.fn(),
  registerTextDocumentContentProvider: jest.fn(),
};

export const window = {
  showErrorMessage: jest.fn(),
  showInformationMessage: jest.fn(),
  showWarningMessage: jest.fn(),
  showQuickPick: jest.fn(),
  showInputBox: jest.fn(),
  showTextDocument: jest.fn(),
  createOutputChannel: jest.fn(() => ({
    appendLine: jest.fn(),
  })),
  createStatusBarItem: jest.fn(() => ({
    text: '',
    color: undefined,
    tooltip: '',
    command: '',
    show: jest.fn(),
    dispose: jest.fn(),
  })),
  registerTreeDataProvider: jest.fn(),
};

export const ThemeIcon = class MockThemeIcon {
  constructor(id: string) {
    this.id = id;
  }
  id: string;
};

export const ThemeColor = class MockThemeColor {
  constructor(id: string) {
    this.id = id;
  }
  id: string;
};

export const commands = {
  executeCommand: jest.fn(),
  registerCommand: jest.fn(),
};

export const Uri = {
  file: jest.fn((path) => ({ fsPath: path })),
};

export const debug = {
  registerDebugConfigurationProvider: jest.fn(),
  onDidTerminateDebugSession: jest.fn(),
  startDebugging: jest.fn(),
};

export const languages = {
  setTextDocumentLanguage: jest.fn(),
};

export const env = {
  clipboard: {
    writeText: jest.fn(),
  },
};

export const StatusBarAlignment = {
  Right: 1,
};

export const QuickPickItem = {};
export const InputBoxOptions = {};
export const TextDocumentContentProvider = class {};
export const TextDocument = {};
export const DebugConfigurationProvider = class {};
export const DebugConfiguration = {};
export const WorkspaceFolder = {};
export const CancellationToken = {};
export const EventEmitter = class {
  constructor() {
    this.event = {};
    this.fire = jest.fn();
  }
  event: any;
  fire: any;
};
export const TreeDataProvider = class {};
export const TreeItem = class {
  constructor(label: string, collapsibleState?: any) {
    this.label = label;
    this.collapsibleState = collapsibleState;
  }
  label: string;
  collapsibleState?: any;
  contextValue?: string;
  iconPath?: any;
  tooltip?: string;
  id?: string;
  command?: any;
};
export const TreeItemCollapsibleState = {
  None: 0,
  Collapsed: 1,
  Expanded: 2,
};

// Re-export with proper names
export { window as windowAlias, commands as commandsAlias, workspace as workspaceAlias, debug as debugAlias, languages as languagesAlias, env as envAlias, StatusBarAlignment as StatusBarAlignmentAlias };
