jest.mock('vscode');

import { resolveConnectivity, usesPromptPassword } from '../src/connectivity';
import { setupConfigMock, setProfileConfig, clearProfileConfig } from './utils/helpers';

const PROFILE_TOML = `
default_profile = "dev"
[profiles.dev]
url = "https://dev.example"
user = "profuser"
api_key = "profkey"
debug_ports = [6000, 6001]
[profiles.direct]
url = "https://direct.example"
user = "directuser"
[profiles.named]
url = "https://named.example"
user = "nameduser"
api_key = "namedkey"
`;

// No pyops.* connectivity keys explicitly set → everything inherits from the
// profile. (undefined values are present-but-unset, i.e. not overrides.)
function inheritAll() {
  setupConfigMock({ serverUrl: undefined, username: undefined, excludedSets: [] });
}

afterEach(() => {
  clearProfileConfig();
});

describe('resolveConnectivity', () => {
  describe('no profile (legacy VSCode-only)', () => {
    beforeEach(() => clearProfileConfig());

    it('uses VSCode settings verbatim and marks username as override', () => {
      setupConfigMock({ serverUrl: 'http://host:9000', username: 'alice' });
      const c = resolveConnectivity();
      expect(c.serverUrl).toBe('http://host:9000');
      expect(c.username).toBe('alice');
      expect(c.userOverridden).toBe(true);
      expect(c.authMode).toBe('basic');
      expect(c.profileApiKey).toBeUndefined();
      expect(c.debugPorts).toEqual([5678]);
      expect(c.profileName).toBeUndefined();
    });

    it('empty username is not an override', () => {
      setupConfigMock({ username: undefined });
      expect(resolveConnectivity().userOverridden).toBe(false);
    });
  });

  describe('profile resolved, no overrides', () => {
    beforeEach(() => {
      setProfileConfig(PROFILE_TOML);
      inheritAll();
    });

    it('inherits url/user/ports and basic mode from the api_key profile', () => {
      const c = resolveConnectivity();
      expect(c.serverUrl).toBe('https://dev.example');
      expect(c.username).toBe('profuser');
      expect(c.userOverridden).toBe(false);
      expect(c.authMode).toBe('basic');
      expect(c.profileApiKey).toBe('profkey');
      expect(c.debugPorts).toEqual([6000, 6001]);
      expect(c.profileName).toBe('dev');
    });

    it('a keyless profile resolves to x-user mode', () => {
      setupConfigMock({ serverUrl: undefined, username: undefined, profile: 'direct' });
      const c = resolveConnectivity();
      expect(c.authMode).toBe('x-user');
      expect(c.username).toBe('directuser');
      expect(c.profileApiKey).toBeUndefined();
    });

    it('the pyops.profile setting selects a non-default profile', () => {
      setupConfigMock({ serverUrl: undefined, username: undefined, profile: 'named' });
      const c = resolveConnectivity();
      expect(c.profileName).toBe('named');
      expect(c.serverUrl).toBe('https://named.example');
      expect(c.profileApiKey).toBe('namedkey');
    });
  });

  describe('overrides on top of a profile', () => {
    beforeEach(() => setProfileConfig(PROFILE_TOML));

    it('serverUrl override replaces the profile url', () => {
      setupConfigMock({ serverUrl: 'http://override:1234', username: undefined });
      expect(resolveConnectivity().serverUrl).toBe('http://override:1234');
    });

    it('username override withholds the profile api_key (prompt path)', () => {
      setupConfigMock({ serverUrl: undefined, username: 'mapped' });
      const c = resolveConnectivity();
      expect(c.username).toBe('mapped');
      expect(c.userOverridden).toBe(true);
      expect(c.authMode).toBe('basic'); // still basic (profile has a key)
      expect(c.profileApiKey).toBeUndefined(); // key belongs to profuser, not mapped
    });

    it('authMode override wins over the profile-derived mode', () => {
      setupConfigMock({ serverUrl: undefined, username: undefined, authMode: 'x-user' });
      expect(resolveConnectivity().authMode).toBe('x-user');
    });

    it('debugPorts override fully replaces the profile pool', () => {
      setupConfigMock({ serverUrl: undefined, username: undefined, debugPorts: [7000] });
      expect(resolveConnectivity().debugPorts).toEqual([7000]);
    });
  });
});

describe('unparseable client config', () => {
  it('shows the parse error once and fails instead of falling back', () => {
    const vscode = require('vscode');
    vscode.window.showErrorMessage.mockClear();
    setupConfigMock({ serverUrl: 'http://host:9000', username: 'alice' });
    setProfileConfig('this is = = not valid toml [[[');
    expect(() => resolveConnectivity()).toThrow(/^Cannot parse PyOps client config /);
    expect(() => resolveConnectivity()).toThrow(/^Cannot parse PyOps client config /);
    expect(vscode.window.showErrorMessage).toHaveBeenCalledTimes(1);
    expect(vscode.window.showErrorMessage.mock.calls[0][0]).toMatch(/^Cannot parse PyOps client config /);
  });

  it('shows the error again once the config was valid in between', () => {
    const vscode = require('vscode');
    vscode.window.showErrorMessage.mockClear();
    const fs = require('fs');
    const { _clearProfileCache } = require('../src/services/ProfileService');
    const rewrite = (file: string, toml: string) => { fs.writeFileSync(file, toml); _clearProfileCache(); };
    inheritAll();
    const file = setProfileConfig('broken = = [');
    expect(() => resolveConnectivity()).toThrow();
    rewrite(file, PROFILE_TOML);
    expect(resolveConnectivity().serverUrl).toBe('https://dev.example');
    rewrite(file, 'broken = = [');
    expect(() => resolveConnectivity()).toThrow();
    expect(vscode.window.showErrorMessage).toHaveBeenCalledTimes(2);
  });
});

describe('usesPromptPassword', () => {
  it('true for basic with no usable profile key', () => {
    expect(usesPromptPassword({ authMode: 'basic' } as any)).toBe(true);
  });

  it('false when the profile api_key supplies the password', () => {
    expect(usesPromptPassword({ authMode: 'basic', profileApiKey: 'k', userOverridden: false } as any)).toBe(false);
  });

  it('true when the user is remapped even if the profile has a key', () => {
    expect(usesPromptPassword({ authMode: 'basic', profileApiKey: undefined, userOverridden: true } as any)).toBe(true);
  });

  it('false for x-user', () => {
    expect(usesPromptPassword({ authMode: 'x-user' } as any)).toBe(false);
  });
});
