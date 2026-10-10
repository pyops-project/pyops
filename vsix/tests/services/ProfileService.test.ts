import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import { configPath, ProfileConfigError, resolveProfile, _clearProfileCache } from '../../src/services/ProfileService';
import { testCacheDir } from '../utils/helpers';

describe('ProfileService', () => {
  let dir: string;
  let file: string;
  const savedEnv = { ...process.env };

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(testCacheDir(), 'profilesvc-'));
    file = path.join(dir, 'config.toml');
    process.env.PYOPSCTL_CONFIG = file;
    delete process.env.PYOPSCTL_PROFILE;
    _clearProfileCache();
  });

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true });
    process.env = { ...savedEnv };
    _clearProfileCache();
  });

  function write(toml: string) {
    fs.writeFileSync(file, toml);
    _clearProfileCache();
  }

  describe('configPath', () => {
    it('honours PYOPSCTL_CONFIG', () => {
      expect(configPath()).toBe(file);
    });

    it('defaults to ~/.config/pyops/config.toml', () => {
      delete process.env.PYOPSCTL_CONFIG;
      expect(configPath()).toBe(path.join(os.homedir(), '.config', 'pyops', 'config.toml'));
    });
  });

  it('returns null when the config file is absent', () => {
    expect(resolveProfile('prod')).toBeNull();
  });

  it('throws ProfileConfigError naming the file when the config is unparseable', () => {
    write('this is = = not valid toml [[[');
    expect(() => resolveProfile('prod')).toThrow(ProfileConfigError);
    expect(() => resolveProfile('prod')).toThrow(/^Cannot parse PyOps client config .*config\.toml: /);
  });

  it('resolves a named profile with all fields', () => {
    write(`
default_profile = "dev"
[profiles.prod]
url = "https://pyops.example/"
user = "alice"
api_key = "s3cret"
debug_ports = [5678, 5679]
`);
    const p = resolveProfile('prod');
    expect(p).toEqual({
      name: 'prod',
      url: 'https://pyops.example', // trailing slash stripped
      user: 'alice',
      apiKey: 's3cret',
      debugPorts: [5678, 5679],
    });
  });

  it('falls back to default_profile when no name is given', () => {
    write(`
default_profile = "dev"
[profiles.dev]
url = "http://localhost:8000"
user = "bob"
`);
    const p = resolveProfile();
    expect(p?.name).toBe('dev');
    expect(p?.user).toBe('bob');
    expect(p?.apiKey).toBe('');
    expect(p?.debugPorts).toEqual([]);
  });

  it('honours PYOPSCTL_PROFILE over default_profile', () => {
    write(`
default_profile = "dev"
[profiles.dev]
url = "http://dev"
[profiles.prod]
url = "http://prod"
`);
    process.env.PYOPSCTL_PROFILE = 'prod';
    _clearProfileCache();
    expect(resolveProfile()?.url).toBe('http://prod');
  });

  it('an explicit name beats PYOPSCTL_PROFILE and default_profile', () => {
    write(`
default_profile = "dev"
[profiles.dev]
url = "http://dev"
[profiles.prod]
url = "http://prod"
[profiles.staging]
url = "http://staging"
`);
    process.env.PYOPSCTL_PROFILE = 'prod';
    _clearProfileCache();
    expect(resolveProfile('staging')?.url).toBe('http://staging');
  });

  it('returns null for an unknown profile (legacy fallback, not an error)', () => {
    write(`
[profiles.dev]
url = "http://dev"
`);
    expect(resolveProfile('nope')).toBeNull();
  });

  it('returns null when no profile is selected and there is no default', () => {
    write(`
[profiles.dev]
url = "http://dev"
`);
    expect(resolveProfile()).toBeNull();
  });

  it('drops invalid debug_ports entries and keeps valid ones', () => {
    write(`
[profiles.dev]
url = "http://dev"
debug_ports = [5678, 70000, 100, "x", 6000]
`);
    expect(resolveProfile('dev')?.debugPorts).toEqual([5678, 6000]);
  });

  it('coerces missing/wrong-typed fields to safe empties', () => {
    write(`
[profiles.dev]
url = "http://dev"
`);
    const p = resolveProfile('dev');
    expect(p).toEqual({ name: 'dev', url: 'http://dev', user: '', apiKey: '', debugPorts: [] });
  });

  it('re-reads the file after its mtime changes', () => {
    write(`
[profiles.dev]
url = "http://one"
`);
    expect(resolveProfile('dev')?.url).toBe('http://one');
    // Rewrite with a bumped mtime so the cache invalidates.
    const later = new Date(Date.now() + 2000);
    fs.writeFileSync(file, `
[profiles.dev]
url = "http://two"
`);
    fs.utimesSync(file, later, later);
    expect(resolveProfile('dev')?.url).toBe('http://two');
  });

  it('serves the cached parse while the mtime is unchanged', () => {
    const mtime = new Date('2020-01-01T00:00:00Z');
    write(`
[profiles.dev]
url = "http://one"
`);
    fs.utimesSync(file, mtime, mtime);
    expect(resolveProfile('dev')?.url).toBe('http://one');
    // Rewrite but restore the same mtime: the cache must answer, not a re-parse.
    fs.writeFileSync(file, `
[profiles.dev]
url = "http://two"
`);
    fs.utimesSync(file, mtime, mtime);
    expect(resolveProfile('dev')?.url).toBe('http://one');
  });
});
