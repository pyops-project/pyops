import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// Test data utilities moved from suite/index.ts
export async function createTestScriptlet(set: string, name: string, type: string, code: string): Promise<void> {
  const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';

  // First create the scriptlet
  const createResponse = await fetch(`${serverUrl}/scriptlet/inventory/item`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ set, name, type }),
  });

  if (!createResponse.ok) {
    throw new Error(`Failed to create test scriptlet: ${createResponse.statusText}`);
  }

  // Then update with the code
  const updateResponse = await fetch(`${serverUrl}/scriptlet/inventory/item`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ set, name, code }),
  });

  if (!updateResponse.ok) {
    throw new Error(`Failed to update test scriptlet: ${updateResponse.statusText}`);
  }
}

export async function createTestJob(set: string, name: string, param: string = ''): Promise<string> {
  const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';

  const response = await fetch(`${serverUrl}/scriptlet/job/item`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      set,
      name,
      param,
      drop_done: false,
      kind: 'task'
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create test job: ${response.statusText}`);
  }

  const result = await response.json() as { job: string };
  // The response is {"job": "job_id_string"}
  if (typeof result.job !== 'string') {
    throw new Error(`Unexpected job creation response format: ${JSON.stringify(result)}`);
  }
  return result.job;
}

export async function deleteTestScriptlet(set: string, name: string): Promise<void> {
  const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';

  const response = await fetch(`${serverUrl}/scriptlet/inventory/item?set=${set}&name=${name}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new Error(`Failed to delete test scriptlet: ${response.status} ${response.statusText}`);
  }
}



// New utility functions for enhanced testing
export function createTempDir(): string {
  if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'pyops-test-'));
  }
  const wsRoot = vscode.workspace.workspaceFolders[0].uri.fsPath;
  const tempName = `pyops-test-${Math.random().toString(36).substring(2, 11)}`;
  const tempDir = path.join(wsRoot, tempName);
  fs.mkdirSync(tempDir, { recursive: true });
  return tempDir;
}

const JOB_STATE_TIMEOUT = parseInt(process.env.PYOPS_JOB_STATE_TIMEOUT || '5000', 10);

// States reachable from enqueue; used when the caller only needs to sync "job exists and has begun
// progressing" — avoids a race where the runner moves past 'todo' before the first poll lands.
export const JOB_PROGRESSED_STATES = ['todo', 'dequeued', 'loading', 'running', 'done', 'failed', 'cancelled'];

export async function waitForJobState(jobId: string, expectedState: string | string[], timeoutMs: number = JOB_STATE_TIMEOUT): Promise<void> {
  const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';
  const startTime = Date.now();
  const expected = Array.isArray(expectedState) ? expectedState : [expectedState];

  while (Date.now() - startTime < timeoutMs) {
    const response = await fetch(`${serverUrl}/scriptlet/job/item?id=${jobId}`);
    if (!response.ok) {
      if (response.status === 404) {
        // Job deleted
        if (expected.includes('removed')) {
          return;
        }
      }
      throw new Error(`Failed to fetch job: ${response.status}`);
    }
    const job = await response.json() as { state: string };
    if (expected.includes(job.state)) {
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000)); // Poll every 1s
  }
  throw new Error(`Job ${jobId} did not reach state ${expected.join('|')} within ${timeoutMs}ms`);
}


export async function pollTree(provider: any, expectedChildLabels: string[], timeoutMs: number = 5000): Promise<void> {
  const startTime = Date.now();

  while (Date.now() - startTime < timeoutMs) {
    const children = await provider.getChildren();
    const childLabels = children.map((child: any) => child.label);
    if (expectedChildLabels.every(label => childLabels.includes(label))) {
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 500)); // Poll every 0.5s
  }
  throw new Error(`Tree did not show expected children ${expectedChildLabels} within ${timeoutMs}ms`);
}

// Helper to edit local file
export function editLocalFile(localPath: string, appendText: string = '// test change\n'): void {
  const content = fs.readFileSync(localPath, 'utf8') + appendText;
  fs.writeFileSync(localPath, content);
}

// Helper to find item in children by label
export function findTreeItemByLabel(children: any[], label: string): any {
  return children.find((item: any) => item.label === label);
}

// Poll tree item icon
export async function pollTreeItemIcon(provider: any, parentLabel: string | undefined, expectedLabel: string, expectedIconId: string, timeoutMs: number = 5000): Promise<void> {
  const startTime = Date.now();
  while (Date.now() - startTime < timeoutMs) {
    let children: any[];
    if (parentLabel) {
      const parentChildren = await provider.getChildren();
      const parent = parentChildren.find((p: any) => p.label === parentLabel);
      if (!parent) throw new Error(`Parent ${parentLabel} not found`);
      children = await provider.getChildren(parent);
    } else {
      children = await provider.getChildren();
    }
    const item = findTreeItemByLabel(children, expectedLabel);
    if (item && item.iconPath && (item.iconPath as vscode.ThemeIcon).id === expectedIconId) {
      return;
    }
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Item ${expectedLabel} did not show ${expectedIconId} icon within ${timeoutMs}ms`);
}

// Cleanup all test jobs (anonymous_test_*) across states for clean test env
export async function cleanupJobs(waitForPortRelease: boolean = false): Promise<void> {
  const serverUrl = process.env.PYOPS_SERVER_URL || 'http://localhost:8000';
  const timeoutMs = 15000;
  const pollIntervalMs = 500;
  const activeStates = ['todo', 'dequeued', 'running'];
  const terminalStates = ['done', 'failed', 'cancelled', 'killed', 'remove', 'removed'];

  // Cancel test jobs (todo/dequeued/running)
  let listResponse = await fetch(`${serverUrl}/scriptlet/job/list`);
  if (!listResponse.ok) return; // Graceful

  let jobs = await listResponse.json() as any[];

  const testJobs = jobs.filter(job => job.id?.startsWith('anonymous_test_'));

  // Cancel active test jobs
  for (const job of testJobs) {
    if (activeStates.includes(job.state)) {
      await fetch(`${serverUrl}/scriptlet/job/cancel?id=${job.id}`);
    }
  }

  // Poll until all test jobs reach terminal states
  const startTime = Date.now();
  while (Date.now() - startTime < timeoutMs) {
    listResponse = await fetch(`${serverUrl}/scriptlet/job/list`);
    if (!listResponse.ok) {
      throw new Error(`Failed to list jobs during cleanup: ${listResponse.status}`);
    }
    jobs = await listResponse.json() as any[];
    const remainingNonTerminalTestJobs = jobs.filter(job => job.id?.startsWith('anonymous_test_') && !terminalStates.includes(job.state));
    if (remainingNonTerminalTestJobs.length === 0) {
      break;
    }
    await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
  }

  // Check if timeout
  const remainingNonTerminal = jobs.filter(job => job.id?.startsWith('anonymous_test_') && !terminalStates.includes(job.state));
  if (remainingNonTerminal.length > 0) {
    throw new Error(`Timeout waiting for test jobs to reach terminal states: ${remainingNonTerminal.map(j => `${j.id}:${j.state}`).join(', ')}`);
  }

  // Remove jobs in terminal states (except already removed)
  for (const job of jobs.filter(job => job.id?.startsWith('anonymous_test_') && terminalStates.includes(job.state) && job.state !== 'removed')) {
    await fetch(`${serverUrl}/scriptlet/job/item?id=${job.id}`, { method: 'DELETE' });
  }

  // Final poll to ensure removals complete
  const finalStartTime = Date.now();
  while (Date.now() - finalStartTime < timeoutMs) {
    listResponse = await fetch(`${serverUrl}/scriptlet/job/list`);
    if (!listResponse.ok) {
      throw new Error(`Failed to list jobs during final cleanup: ${listResponse.status}`);
    }
    jobs = await listResponse.json() as any[];
    const remainingTestJobs = jobs.filter(job => job.id?.startsWith('anonymous_test_'));
    if (remainingTestJobs.length === 0) {
      break;
    }
    await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
  }

  // Final check
  const finalRemaining = jobs.filter(job => job.id?.startsWith('anonymous_test_'));
  if (finalRemaining.length > 0) {
    throw new Error(`Timeout waiting for test jobs to be removed: ${finalRemaining.map(j => `${j.id}:${j.state}`).join(', ')}`);
  }

  // Wait for port cleanup to complete (debug jobs hold ports)
  if (waitForPortRelease) {
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}
