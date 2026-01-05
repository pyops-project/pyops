import * as assert from 'assert';
import * as vscode from 'vscode';

export async function run(): Promise<void> {
  console.log('Running Content Provider Tests...');

  try {
    // Setup: Wait for extension to activate
    const extension = vscode.extensions.getExtension('pyops-team.pyops');
    if (!extension || !extension.isActive) {
      throw new Error('Extension not active');
    }

    // Test 1: Valid remote content and language mode
    console.log('Test 1: Valid remote content and language mode...');
    const testContent = 'print("Hello from remote")';
    const encodedContent = encodeURIComponent(testContent);
    const uri = vscode.Uri.parse(`pyops-remote://test/set?name=test&type=task#${encodedContent}`);
    const document = await vscode.workspace.openTextDocument(uri);
    // Wait for language setter
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.strictEqual(document.getText(), testContent, 'Content must match decoded fragment');
    assert.strictEqual(document.languageId, 'python', 'Language must be set to python for task');
    await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    console.log('✓ Test 1 passed');

    // Test 2: Invalid encoding
    console.log('Test 2: Invalid encoding...');
    const invalidUri = vscode.Uri.parse('pyops-remote://test/set?name=test&type=task#%invalid');
    const invalidDoc = await vscode.workspace.openTextDocument(invalidUri);
    assert.ok(invalidDoc.getText().startsWith('Error decoding content:'), 'Must show error for invalid encoding');
    await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    console.log('✓ Test 2 passed');

    // Test 3: Missing type (no language set)
    console.log('Test 3: Missing type...');
    const noTypeUri = vscode.Uri.parse(`pyops-remote://test/set?name=test#${encodedContent}`);
    const noTypeDoc = await vscode.workspace.openTextDocument(noTypeUri);
    assert.strictEqual(noTypeDoc.getText(), testContent, 'Content must still be provided');
    assert.strictEqual(noTypeDoc.languageId, 'plaintext', 'Language must default to plaintext if no type');
    await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    console.log('✓ Test 3 passed');

    // Test 4: Unsupported type (no language mapping)
    console.log('Test 4: Unsupported type...');
    const unsupportedUri = vscode.Uri.parse(`pyops-remote://test/set?name=test&type=unknown#${encodedContent}`);
    const unsupportedDoc = await vscode.workspace.openTextDocument(unsupportedUri);
    assert.strictEqual(unsupportedDoc.getText(), testContent, 'Content must be provided');
    assert.strictEqual(unsupportedDoc.languageId, 'plaintext', 'Language must default to plaintext for unknown type');
    await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    console.log('✓ Test 4 passed');

    // Test 5: No fragment
    console.log('Test 5: No fragment...');
    const noFragUri = vscode.Uri.parse('pyops-remote://test/set?name=test&type=task');
    const noFragDoc = await vscode.workspace.openTextDocument(noFragUri);
    assert.strictEqual(noFragDoc.getText(), 'No content provided', 'Must show no content message');
    await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
    console.log('✓ Test 5 passed');

    console.log('All Content Provider Tests passed!');
  } finally {
    // Cleanup: Close any open editors
    await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  }
}
