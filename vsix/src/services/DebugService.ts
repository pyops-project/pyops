import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ApiClient } from '../apiClient';

/**
 * Per-set walk of `localPyopsDir/<set>/*.py` returning a `{set::name: code}`
 * dict (the override payload pyops accepts) plus the workspace→remote
 * path mappings VSCode's debugpy needs to map breakpoints back to
 * editor files.
 */
export interface BuildOverridesResult {
    overrides: Record<string, string>;
    pathMappings: Array<{ remoteRoot: string; localRoot: string }>;
}

/**
 * One active debug-port lease. Held in-memory per VSCode session — when
 * the debug session ends or `release()` is called, the port is returned
 * to the pool for the next debug job.
 */
interface PortLease {
    port: number;
    jobId: string;
}

export class DebugService {
    /**
     * In-memory lease map: port → owning job id. Allocated by
     * `leasePort()`, released by `releasePort()`. Two concurrent debug
     * sessions from the same VSCode window can't double-allocate the
     * same port because this is a single-process map; cross-process
     * (e.g. multiple VSCode windows) needs the operator to size the
     * `pyops.debugPorts` pool accordingly.
     */
    private static _leases: Map<number, string> = new Map();

    constructor(private apiClient: ApiClient, private outputChannel: vscode.OutputChannel) {}

    static buildOverrides(set: string, workspaceRoot: string): BuildOverridesResult {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const localPyopsDir = pyopsConfig.get('localPyopsDir', 'pyops') as string;
        const setPath = path.join(workspaceRoot, localPyopsDir, set);
        const overrides: Record<string, string> = {};
        if (!fs.existsSync(setPath)) {
            return { overrides, pathMappings: [] };
        }
        const walkDir = (dir: string) => {
            const files = fs.readdirSync(dir);
            for (const file of files) {
                const fullPath = path.join(dir, file);
                const stat = fs.statSync(fullPath);
                if (stat.isDirectory()) {
                    walkDir(fullPath);
                } else if (file.endsWith('.py')) {
                    const relative = path.relative(setPath, fullPath);
                    const name = path.basename(relative, '.py');
                    const remoteKey = `${set}::${name}`;
                    overrides[remoteKey] = fs.readFileSync(fullPath, 'utf8');
                }
            }
        };
        walkDir(setPath);
        const pathMappings = [{
            localRoot: `\${workspaceFolder}/${localPyopsDir}`,
            remoteRoot: './.debug/src',
        }];
        return { overrides, pathMappings };
    }

    /**
     * Compose the `param` body the server runs verbatim: a debugpy
     * bootstrap that opens a listener, waits for VSCode to attach, and
     * pauses execution at the user's first line. Pyops no longer
     * injects this server-side, so VSIX is responsible for putting
     * exactly the bootstrap it wants on the wire.
     *
     * `#!pip debugpy` is a pyops install directive — the runner
     * ensures debugpy is in the per-set venv before exec(param) runs.
     *
     * `in_process_debug_adapter=True` makes debugpy serve DAP from the
     * program's own server thread rather than spawning a daemonised
     * adapter subprocess. Critical for clean teardown: debugpy's
     * default adapter actively detaches itself from the parent process
     * group (its own setsid+fork), so `killpg(job_pgid, ...)` from
     * pyops can't reach it — it survives the job, keeps holding the
     * port, and poisons the next debug job that tries to bind. With
     * in-process mode there's no separate process to leak; the DAP
     * endpoint dies with the program.
     */
    static composeDebugpyBootstrap(port: number): string {
        return [
            '#!pip debugpy',
            'import debugpy',
            `debugpy.listen(("0.0.0.0", ${port}), in_process_debug_adapter=True)`,
            'debugpy.wait_for_client()',
            'debugpy.breakpoint()',
            '',
        ].join('\n');
    }

    /**
     * Lease the first free port from the workspace `pyops.debugPorts`
     * pool. Throws when the pool is empty or every port is already
     * leased to another active debug session.
     */
    leasePort(jobIdHint: string): PortLease {
        const pyopsConfig = vscode.workspace.getConfiguration('pyops');
        const pool = pyopsConfig.get<number[]>('debugPorts', [5678]) || [];
        if (pool.length === 0) {
            throw new Error(
                "pyops.debugPorts is empty — set it in workspace/user settings before starting a debug job",
            );
        }
        for (const port of pool) {
            if (!DebugService._leases.has(port)) {
                DebugService._leases.set(port, jobIdHint);
                this.outputChannel.appendLine(
                    `[DEBUG leasePort] leased ${port} for ${jobIdHint}`,
                );
                return { port, jobId: jobIdHint };
            }
        }
        throw new Error(
            `pyops.debugPorts pool [${pool.join(', ')}] is fully leased; ` +
            `release a port (end the debug session that holds it) or add more ports.`,
        );
    }

    /**
     * Release a previously-leased port. Idempotent; safe to call on a
     * port that was never leased (e.g. when the lease threw upstream
     * before binding). Call this on debug-session end or on
     * enqueueDebugJob failure so the pool reclaims the port for the
     * next attempt.
     */
    releasePort(port: number): void {
        if (DebugService._leases.delete(port)) {
            this.outputChannel.appendLine(`[DEBUG releasePort] released ${port}`);
        }
    }

    /**
     * Replace the leased port's owner. Used after the server returns
     * the real job id, to overwrite the placeholder we leased with.
     */
    rebindLease(port: number, jobId: string): void {
        if (DebugService._leases.has(port)) {
            DebugService._leases.set(port, jobId);
        }
    }

    /**
     * Snapshot the current lease table — only used by tests + the
     * status output channel.
     */
    static currentLeases(): Array<{ port: number; jobId: string }> {
        return Array.from(DebugService._leases.entries()).map(
            ([port, jobId]) => ({ port, jobId }),
        );
    }

    /**
     * Reverse lookup: which port did this VSCode session lease to the
     * given job id? Used by `pyops.attachDebugJob` so the operator can
     * resume into an existing debug job without remembering ports.
     * Returns `undefined` if no lease in this session matches.
     */
    findPortForJob(jobId: string): number | undefined {
        for (const [port, holder] of DebugService._leases.entries()) {
            if (holder === jobId) {
                return port;
            }
        }
        return undefined;
    }

    /**
     * Enqueue a debug job whose `param` carries the debugpy bootstrap.
     * Leases a port from the workspace pool, composes the bootstrap,
     * PUTs the job, rebinds the lease to the real job id on success
     * or releases on failure.
     */
    async enqueueDebugJob(
        set: string,
        name: string,
        overrides: Record<string, string> = {},
    ): Promise<{ jobId?: string; port?: number }> {
        const placeholder = `pending_${Date.now()}_${Math.random().toString(36).slice(2)}`;
        let lease: PortLease;
        try {
            lease = this.leasePort(placeholder);
        } catch (error: unknown) {
            const msg = error instanceof Error ? error.message : String(error);
            this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] lease failed: ${msg}`);
            vscode.window.showErrorMessage(`PyOps debug: ${msg}`);
            return {};
        }

        const param = DebugService.composeDebugpyBootstrap(lease.port);
        const data = {
            set, name, drop_done: false,
            kind: 'debug',
            param,
            overrides,
        };
        this.outputChannel.appendLine(
            `[DEBUG enqueueDebugJob] PUT /scriptlet/job/item set=${set} name=${name} port=${lease.port}`,
        );
        try {
            const response = await this.apiClient.fetch('/scriptlet/job/item', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            if (!response.ok) {
                const errorText = await response.text();
                this.outputChannel.appendLine(
                    `[DEBUG enqueueDebugJob] server returned ${response.status}: ${errorText}`,
                );
                this.releasePort(lease.port);
                return {};
            }
            const responseData = await response.json();
            const jobId = (responseData as { job: string }).job;
            this.rebindLease(lease.port, jobId);
            this.outputChannel.appendLine(
                `[DEBUG enqueueDebugJob] success, jobId=${jobId} port=${lease.port}`,
            );
            return { jobId, port: lease.port };
        } catch (error: unknown) {
            const msg = error instanceof Error ? error.message : String(error);
            this.outputChannel.appendLine(`[DEBUG enqueueDebugJob] exception: ${msg}`);
            this.releasePort(lease.port);
            return {};
        }
    }

    public getDebugHost(): string {
        const serverUrl = this.apiClient.getServerUrl();
        try {
            return new URL(serverUrl).hostname;
        } catch {
            this.outputChannel.appendLine(
                `[DEBUG getDebugHost] invalid serverUrl ${serverUrl}, defaulting to localhost`,
            );
            return 'localhost';
        }
    }
}
