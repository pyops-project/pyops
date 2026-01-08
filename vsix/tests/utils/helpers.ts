declare var jest: any;

declare function require(name: string): any;

declare namespace jest {
  interface Mock<T = any> {
    mockImplementation(fn: (...args: any[]) => any): this;
  }
}

import type { ExtensionContext } from 'vscode';

export function setupConfigMock(options: Record<string, any> = {}) {
  const pyopsConfig = {
    localPyopsDir: 'pyops',
    serverUrl: 'http://localhost:8000',
    username: 'testuser',
    excludedSets: [],
    ...options
  };
  const pyopsMockGet = jest.fn((key: string) => (pyopsConfig as any)[key] ?? '');
  (require('vscode').workspace.getConfiguration as jest.Mock).mockImplementation((section?: string) => {
    if (section === 'pyops' || section === undefined || section === '') {
      return { get: pyopsMockGet };
    }
    return { get: jest.fn(() => undefined) };
  });
}

export function createMockOutputChannel() {
  return {
    appendLine: jest.fn(),
    append: jest.fn(),
    show: jest.fn(),
    clear: jest.fn(),
    dispose: jest.fn(),
  } as any;
}

export function createMockContext() {
  return {
    subscriptions: [],
    workspaceState: {
      get: jest.fn(),
      update: jest.fn(),
    },
  } as any;
}

export function createMockVscodeWindow() {
  const mockWindow = {
    showInformationMessage: jest.fn().mockResolvedValue(undefined),
    showWarningMessage: jest.fn().mockResolvedValue(undefined),
    showErrorMessage: jest.fn().mockResolvedValue(undefined),
    showQuickPick: jest.fn().mockResolvedValue(undefined),
    showInputBox: jest.fn().mockResolvedValue(undefined),
    showSaveDialog: jest.fn().mockResolvedValue(undefined),
  } as any;
  const mockVscode = require('vscode');
  Object.assign(mockVscode.window, mockWindow);
  return mockWindow;
}

interface ApiClientMockOptions {
  headers?: Record<string, string>;
  serverUrl?: string;
  validConfig?: boolean;
}

export function createMockApiClient(
  ApiClientClass: any,
  options: Partial<ApiClientMockOptions> = {}
) {
  const mockInstance = {
    context: {} as any,
    getHeaders: jest.fn().mockResolvedValue(options.headers || { 'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M=' }),
    getServerUrl: jest.fn().mockReturnValue(options.serverUrl || 'http://localhost:8000'),
    hasValidConfig: jest.fn().mockReturnValue(options.validConfig ?? true),
  };

  ApiClientClass.mockImplementation(() => mockInstance);
  (ApiClientClass as any).hasValidConfigStatic = jest.fn().mockReturnValue(options.validConfig ?? true);
  return mockInstance;
}

export function createMockTreeProvider(providerType: 'jobs' | 'manager') {
  const mock = {
    refresh: jest.fn(),
  } as any;
  return mock;
}

export const fsMockFactory = () => ({
  existsSync: jest.fn().mockReturnValue(false),
  readFileSync: jest.fn(),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
  unlinkSync: jest.fn(),
  readdirSync: jest.fn().mockReturnValue([]),
  statSync: jest.fn(() => ({ isDirectory: () => false })),
});

export const pathMockFactory = () => ({
  join: jest.fn((...args: string[]) => args.join('/')),
  dirname: jest.fn((p: string) => p.substring(0, p.lastIndexOf('/'))),
  basename: jest.fn(),
  relative: jest.fn(),
});
