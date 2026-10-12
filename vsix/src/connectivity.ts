import * as vscode from 'vscode';

import { ProfileConfigError, resolveProfile, ResolvedProfile } from './services/ProfileService';

export type AuthMode = 'basic' | 'x-user';

/**
 * Effective connectivity for the current workspace, layering a PyOps
 * client-config profile (base) under the `pyops.*` VSCode settings (full
 * override).
 *
 * Sourcing rules (per field: explicit VSCode setting > profile value > default):
 *   - `serverUrl`  — override `pyops.serverUrl` else `profile.url` else ''.
 *   - `username`   — override `pyops.username` else `profile.user`. When the
 *                    setting is explicitly set, `userOverridden` is true and the
 *                    identity is remapped onto the profile's connection.
 *   - `authMode`   — override `pyops.authMode` else basic, the setting's
 *                    default as the settings UI shows it; a profile does not
 *                    change it.
 *   - `debugPorts` — override `pyops.debugPorts` else `profile.debugPorts` else
 *                    [5678]. Override fully replaces (no union).
 *
 * The Basic-Auth password itself is resolved by the caller (async secret read),
 * driven by `profileApiKey`/`userOverridden` — see `usesPromptPassword`.
 */
export interface Connectivity {
    serverUrl: string;
    username: string;
    userOverridden: boolean;
    authMode: AuthMode;
    /**
     * The profile's api_key, exposed only when it is usable as-is: a profile
     * exists, it carries a key, and the user has NOT been remapped. When the
     * user is overridden the key belongs to a different identity and is withheld
     * (the caller prompts for / reads a stored password instead).
     */
    profileApiKey?: string;
    debugPorts: number[];
    profileName?: string;
}

/**
 * The explicitly-configured value of a `pyops.*` key, distinguishing a real
 * user override from a mere schema default. `get()` cannot tell these apart for
 * keys with non-empty defaults (serverUrl/authMode/debugPorts), so we inspect
 * the scope-specific values and ignore `defaultValue`.
 */
function explicitOverride<T>(cfg: vscode.WorkspaceConfiguration, key: string): T | undefined {
    const insp = cfg.inspect<T>(key);
    if (!insp) {
        return undefined;
    }
    return insp.workspaceFolderValue ?? insp.workspaceValue ?? insp.globalValue;
}

// The config error last shown, so each distinct error is shown once rather
// than on every connectivity lookup.
let _shownConfigError: string | undefined;

/**
 * Throws ProfileConfigError when the PyOps client config cannot be parsed —
 * no connection is made with a fallback; the error is also shown to the user
 * (once per distinct error).
 */
export function resolveConnectivity(): Connectivity {
    const cfg = vscode.workspace.getConfiguration('pyops');
    const profileName = (explicitOverride<string>(cfg, 'profile') || '').trim();
    let profile: ResolvedProfile | null;
    try {
        profile = resolveProfile(profileName);
    } catch (error: unknown) {
        if (error instanceof ProfileConfigError && error.message !== _shownConfigError) {
            _shownConfigError = error.message;
            vscode.window.showErrorMessage(error.message);
        }
        throw error;
    }
    _shownConfigError = undefined;

    const urlOverride = explicitOverride<string>(cfg, 'serverUrl');
    const serverUrl = urlOverride !== undefined ? urlOverride : (profile?.url ?? '');

    const userOverrideRaw = explicitOverride<string>(cfg, 'username');
    const userOverridden = userOverrideRaw !== undefined && userOverrideRaw !== '';
    const username = userOverridden ? (userOverrideRaw as string) : (profile?.user ?? '');

    const modeOverride = explicitOverride<AuthMode>(cfg, 'authMode');
    const authMode: AuthMode = modeOverride === 'x-user' ? 'x-user' : 'basic';

    const portsOverride = explicitOverride<number[]>(cfg, 'debugPorts');
    let debugPorts: number[];
    if (portsOverride !== undefined) {
        debugPorts = portsOverride;
    } else if (profile && profile.debugPorts.length) {
        debugPorts = profile.debugPorts;
    } else {
        debugPorts = [5678];
    }

    // The api_key is only usable as the Basic password for the profile's own
    // identity; a remapped user must supply their own password.
    const profileApiKey = profile && profile.apiKey && !userOverridden ? profile.apiKey : undefined;

    return {
        serverUrl,
        username,
        userOverridden,
        authMode,
        profileApiKey,
        debugPorts,
        profileName: profile?.name,
    };
}

/**
 * Whether Basic Auth needs a prompted / stored password. False when the
 * profile's api_key supplies it directly (nothing for the user to enter) or the
 * connection is x-user. Used by AuthService (skip prompt) and
 * ConnectionStatusManager (skip 401 clear-and-reprompt — a bad api_key is fixed
 * in the TOML, not by re-entering a password).
 */
export function usesPromptPassword(c: Connectivity): boolean {
    return c.authMode === 'basic' && (c.userOverridden || !c.profileApiKey);
}
