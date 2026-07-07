import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { parse as parseToml } from 'smol-toml';

// Fallback X-User identity when a profile carries no api_key and no user,
// mirroring pyopsctl's DEFAULT_X_USER. Re-exported from apiClient for the
// connectivity layer; kept here so ProfileService has no circular import.
const CONFIG_ENV = 'PYOPSCTL_CONFIG';
const PROFILE_ENV = 'PYOPSCTL_PROFILE';

/**
 * A connectivity profile resolved from the shared PyOps client config
 * (`~/.config/pyops/config.toml`) — the single connectivity source of truth
 * read by every PyOps client (the pyopsctl CLI, this extension, …). Field names
 * mirror pyopsctl's `config.Profile` so all clients read identical values:
 *   - `url`        → server base URL
 *   - `user`       → identity (Basic username / X-User value)
 *   - `apiKey`     → Basic-Auth password; empty → X-User pathway
 *   - `debugPorts` → per-profile debug port pool
 */
export interface ResolvedProfile {
    name: string;
    url: string;
    user: string;
    apiKey: string;
    debugPorts: number[];
}

interface FileCacheEntry {
    mtimeMs: number;
    profiles: Record<string, any>;
    defaultProfile: string | undefined;
}

// Parse cache keyed by absolute config path; invalidated on mtime change so
// edits to the TOML are picked up without reparsing on every HTTP call.
const _fileCache = new Map<string, FileCacheEntry>();

/**
 * Absolute path of the PyOps client config file, honouring `PYOPSCTL_CONFIG`
 * (the historical env-var name, still the shared override used by tests and
 * non-default installs) and otherwise `~/.config/pyops/config.toml`.
 */
export function configPath(): string {
    return process.env[CONFIG_ENV] || path.join(os.homedir(), '.config', 'pyops', 'config.toml');
}

/** The PyOps client config exists but cannot be parsed. */
export class ProfileConfigError extends Error {}

function loadFile(): FileCacheEntry | null {
    const p = configPath();
    let stat: fs.Stats;
    try {
        stat = fs.statSync(p);
    } catch {
        return null; // no config file → no profiles (legacy VSCode-only mode)
    }
    const cached = _fileCache.get(p);
    if (cached && cached.mtimeMs === stat.mtimeMs) {
        return cached;
    }
    let parsed: any;
    try {
        parsed = parseToml(fs.readFileSync(p, 'utf8'));
    } catch (error: unknown) {
        const reason = error instanceof Error ? error.message : String(error);
        throw new ProfileConfigError(`Cannot parse PyOps client config ${p}: ${reason}`);
    }
    const entry: FileCacheEntry = {
        mtimeMs: stat.mtimeMs,
        profiles: (parsed && typeof parsed.profiles === 'object' && parsed.profiles) || {},
        defaultProfile: typeof parsed?.default_profile === 'string' ? parsed.default_profile : undefined,
    };
    _fileCache.set(p, entry);
    return entry;
}

function validateDebugPorts(raw: unknown): number[] {
    if (!Array.isArray(raw)) {
        return [];
    }
    const out: number[] = [];
    for (const entry of raw) {
        if (typeof entry === 'number' && Number.isInteger(entry) && entry >= 1025 && entry <= 65535) {
            out.push(entry);
        }
    }
    return out;
}

/**
 * Resolve the effective profile for the given name, with the same precedence
 * every PyOps client uses: explicit `name` (the `pyops.profile` setting) >
 * `PYOPSCTL_PROFILE` env > the config's `default_profile`. Returns null when
 * there is no config file, no profile is selected, or the named profile is
 * unknown — every one of which falls back to pure VSCode-settings connectivity.
 * Throws ProfileConfigError when the config file cannot be parsed.
 */
export function resolveProfile(name?: string): ResolvedProfile | null {
    const file = loadFile();
    if (!file) {
        return null;
    }
    const chosen = (name && name.trim()) || process.env[PROFILE_ENV] || file.defaultProfile;
    if (!chosen) {
        return null;
    }
    const base = file.profiles[chosen];
    if (!base || typeof base !== 'object') {
        return null; // unknown profile → legacy fallback, never a hard error
    }
    return {
        name: chosen,
        url: typeof base.url === 'string' ? base.url.replace(/\/+$/, '') : '',
        user: typeof base.user === 'string' ? base.user : '',
        apiKey: typeof base.api_key === 'string' ? base.api_key : '',
        debugPorts: validateDebugPorts(base.debug_ports),
    };
}

// Test hook: drop the parse cache so a rewritten fixture is re-read.
export function _clearProfileCache(): void {
    _fileCache.clear();
}
