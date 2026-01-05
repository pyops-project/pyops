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
  TreeItem: class {
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
  },
  TreeItemCollapsibleState: {
    None: 0,
    Collapsed: 1,
  },
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

export const workspace2 = {
  registerTextDocumentContentProvider: jest.fn(),
  onDidOpenTextDocument: jest.fn(),
};

export const env = {
  clipboard: {
    writeText: jest.fn(),
  },
};

export const StatusBarAlignment = {
  Right: 1,
};

export const window2 = {
  createStatusBarItem: jest.fn(() => ({
    text: '',
    color: undefined,
    tooltip: '',
    command: '',
    show: jest.fn(),
    dispose: jest.fn(),
  })),
};

export const commands2 = {
  registerCommand: jest.fn(),
};

export const debug2 = {
  onDidTerminateDebugSession: jest.fn(),
};

export const workspace3 = {
  onDidChangeConfiguration: jest.fn(),
  fs: {
    writeFile: jest.fn(),
  },
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
export const Job = {};
export const TreeItemCollapsibleState = {
  None: 0,
  Collapsed: 1,
  Expanded: 2,
};

// Re-export with proper names
export { window as windowAlias, commands as commandsAlias, workspace as workspaceAlias, debug as debugAlias, languages as languagesAlias, workspace2 as workspace2Alias, env as envAlias, StatusBarAlignment as StatusBarAlignmentAlias, window2 as window2Alias, commands2 as commands2Alias, debug2 as debug2Alias, workspace3 as workspace3Alias };
