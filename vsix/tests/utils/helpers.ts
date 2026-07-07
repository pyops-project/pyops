declare var jest: any;

declare function require(name: string): any;

declare namespace jest {
  interface Mock<T = any> {
    mockImplementation(fn: (...args: any[]) => any): this;
  }
}

import type { ExtensionContext } from 'vscode';

// Schema defaults from package.json, returned by inspect().defaultValue for
// keys the test didn't explicitly set (mirrors real VSCode behaviour).
const SCHEMA_DEFAULTS: Record<string, any> = {
  profile: '',
  serverUrl: 'http://localhost:8000',
  authMode: 'basic',
  timeout: 30,
  useRemoteLinter: true,
  localPyopsDir: 'pyops',
  autoRefreshInterval: 10,
  excludedSets: [],
  includedSets: [],
  debugPorts: [5678],
};

export function setupConfigMock(options: Record<string, any> = {}) {
  const pyopsConfig = {
    localPyopsDir: 'pyops',
    serverUrl: 'http://localhost:8000',
    username: 'testuser',
    excludedSets: [],
    ...options
  };
  // VSCode's WorkspaceConfiguration.get(key, defaultValue) returns the
  // configured value if present, else `defaultValue`. Honour that
  // signature here so callers passing `get<T>('foo', [1, 2])` get the
  // typed default when 'foo' isn't in the test's config object.
  const pyopsMockGet = jest.fn((key: string, defaultValue?: any) => {
    if (Object.prototype.hasOwnProperty.call(pyopsConfig, key)) {
      return (pyopsConfig as any)[key];
    }
    return defaultValue !== undefined ? defaultValue : '';
  });
  // inspect() lets the connectivity resolver tell an explicit override from a
  // schema default: any key present in the test's config object is reported as
  // an explicit workspaceValue; everything else is default-only.
  const pyopsMockInspect = jest.fn((key: string) => ({
    key: `pyops.${key}`,
    defaultValue: SCHEMA_DEFAULTS[key],
    globalValue: undefined,
    workspaceValue: Object.prototype.hasOwnProperty.call(pyopsConfig, key)
      ? (pyopsConfig as any)[key]
      : undefined,
    workspaceFolderValue: undefined,
  }));
  (require('vscode').workspace.getConfiguration as jest.Mock).mockImplementation((section?: string) => {
    if (section === 'pyops' || section === undefined || section === '') {
      return { get: pyopsMockGet, inspect: pyopsMockInspect };
    }
    return { get: jest.fn((_k: string, d?: any) => d), inspect: jest.fn(() => undefined) };
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
    secrets: {
      get: jest.fn(),
      store: jest.fn(),
      delete: jest.fn(),
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
    fetch: jest.fn(),
    fetchJson: jest.fn(),
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

// --- PyOps client-config profile fixtures -------------------------------------------
// ProfileService reads a real TOML file via fs + process.env.PYOPSCTL_CONFIG.
// These helpers write a temp fixture under tests/.cache and point the loader
// at it, isolating unit tests from the host's real ~/.config/pyops/config.toml.

const nodeFs = require('fs');
const nodeOs = require('os');
const nodePath = require('path');

// Guaranteed-absent path used as the default so a test that never sets a
// fixture resolves no profile (legacy VSCode-only connectivity).
export const NONEXISTENT_CONFIG = nodePath.join(nodeOs.tmpdir(), 'pyops-vsix-tests-no-such-config.toml');

export function setProfileConfig(toml: string): string {
  const dir = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), 'pyops-vsix-cfg-'));
  const file = nodePath.join(dir, 'config.toml');
  nodeFs.writeFileSync(file, toml);
  process.env.PYOPSCTL_CONFIG = file;
  require('../../src/services/ProfileService')._clearProfileCache();
  return file;
}

export function clearProfileConfig() {
  process.env.PYOPSCTL_CONFIG = NONEXISTENT_CONFIG;
  require('../../src/services/ProfileService')._clearProfileCache();
}
