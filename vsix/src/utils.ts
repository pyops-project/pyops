import * as vscode from 'vscode';

export function appendIncrementalOutput(
    channel: vscode.OutputChannel,
    jobObj: { stdout?: string; stderr?: string },
    tracker: { prevStdoutLen: number; prevStderrLen: number }
): void {
    if (jobObj.stdout) {
        const newOut = jobObj.stdout.slice(tracker.prevStdoutLen);
        if (newOut.length > 0) {
            channel.appendLine('[STDOUT]');
            channel.append(newOut);
        }
        tracker.prevStdoutLen = jobObj.stdout.length;
    }
    if (jobObj.stderr) {
        const newErr = jobObj.stderr.slice(tracker.prevStderrLen);
        if (newErr.length > 0) {
            channel.appendLine('[STDERR]');
            channel.append(newErr);
            channel.show(true);
        }
        tracker.prevStderrLen = jobObj.stderr.length;
    }
}

export function inferExt(type: string): string {
    switch (type) {
        case 'task':
        case 'service':
        case 'library':
        case 'controller':
            return 'py';
        case 'view':
            return 'html';
        case 'markdown':
            return 'md';
        case 'jslibrary':
            return 'js';
        default:
            return '';
    }
}
