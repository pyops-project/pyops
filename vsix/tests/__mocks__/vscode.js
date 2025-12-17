"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.workspace3Alias = exports.debug2Alias = exports.commands2Alias = exports.window2Alias = exports.StatusBarAlignmentAlias = exports.envAlias = exports.workspace2Alias = exports.languagesAlias = exports.debugAlias = exports.workspaceAlias = exports.commandsAlias = exports.windowAlias = exports.TreeItemCollapsibleState = exports.Job = exports.TreeItem = exports.TreeDataProvider = exports.EventEmitter = exports.CancellationToken = exports.WorkspaceFolder = exports.DebugConfiguration = exports.DebugConfigurationProvider = exports.TextDocument = exports.TextDocumentContentProvider = exports.InputBoxOptions = exports.QuickPickItem = exports.workspace3 = exports.debug2 = exports.commands2 = exports.window2 = exports.StatusBarAlignment = exports.env = exports.workspace2 = exports.languages = exports.debug = exports.Uri = exports.commands = exports.ThemeColor = exports.ThemeIcon = exports.window = exports.workspace = void 0;
exports.workspace = {
    workspaceFolders: [{ uri: { fsPath: '/test/workspace' } }],
    getConfiguration: jest.fn(),
    onDidChangeConfiguration: jest.fn(),
    openTextDocument: jest.fn(),
    registerTextDocumentContentProvider: jest.fn(),
};
exports.workspaceAlias = exports.workspace;
exports.window = {
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
        constructor(label, collapsibleState) {
            this.label = label;
            this.collapsibleState = collapsibleState;
        }
    },
    TreeItemCollapsibleState: {
        None: 0,
        Collapsed: 1,
    },
};
exports.windowAlias = exports.window;
const ThemeIcon = class MockThemeIcon {
    constructor(id) {
        this.id = id;
    }
};
exports.ThemeIcon = ThemeIcon;
const ThemeColor = class MockThemeColor {
    constructor(id) {
        this.id = id;
    }
};
exports.ThemeColor = ThemeColor;
exports.commands = {
    executeCommand: jest.fn(),
    registerCommand: jest.fn(),
};
exports.commandsAlias = exports.commands;
exports.Uri = {
    file: jest.fn((path) => ({ fsPath: path })),
};
exports.debug = {
    registerDebugConfigurationProvider: jest.fn(),
    onDidTerminateDebugSession: jest.fn(),
};
exports.debugAlias = exports.debug;
exports.languages = {
    setTextDocumentLanguage: jest.fn(),
};
exports.languagesAlias = exports.languages;
exports.workspace2 = {
    registerTextDocumentContentProvider: jest.fn(),
    onDidOpenTextDocument: jest.fn(),
};
exports.workspace2Alias = exports.workspace2;
exports.env = {
    clipboard: {
        writeText: jest.fn(),
    },
};
exports.envAlias = exports.env;
exports.StatusBarAlignment = {
    Right: 1,
};
exports.StatusBarAlignmentAlias = exports.StatusBarAlignment;
exports.window2 = {
    createStatusBarItem: jest.fn(() => ({
        text: '',
        color: undefined,
        tooltip: '',
        command: '',
        show: jest.fn(),
        dispose: jest.fn(),
    })),
};
exports.window2Alias = exports.window2;
exports.commands2 = {
    registerCommand: jest.fn(),
};
exports.commands2Alias = exports.commands2;
exports.debug2 = {
    onDidTerminateDebugSession: jest.fn(),
};
exports.debug2Alias = exports.debug2;
exports.workspace3 = {
    onDidChangeConfiguration: jest.fn(),
    fs: {
        writeFile: jest.fn(),
    },
};
exports.workspace3Alias = exports.workspace3;
exports.QuickPickItem = {};
exports.InputBoxOptions = {};
const TextDocumentContentProvider = class {
};
exports.TextDocumentContentProvider = TextDocumentContentProvider;
exports.TextDocument = {};
const DebugConfigurationProvider = class {
};
exports.DebugConfigurationProvider = DebugConfigurationProvider;
exports.DebugConfiguration = {};
exports.WorkspaceFolder = {};
exports.CancellationToken = {};
const EventEmitter = class {
    constructor() {
        this.event = {};
        this.fire = jest.fn();
    }
};
exports.EventEmitter = EventEmitter;
const TreeDataProvider = class {
};
exports.TreeDataProvider = TreeDataProvider;
const TreeItem = class {
    constructor(label, collapsibleState) {
        this.label = label;
        this.collapsibleState = collapsibleState;
    }
};
exports.TreeItem = TreeItem;
exports.Job = {};
exports.TreeItemCollapsibleState = {
    None: 0,
    Collapsed: 1,
    Expanded: 2,
};
//# sourceMappingURL=vscode.js.map