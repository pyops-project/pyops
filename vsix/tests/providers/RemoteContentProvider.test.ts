import { RemoteContentProvider, getLanguageId, registerRemoteLanguageModeSetter } from '../../src/providers/RemoteContentProvider';

jest.mock('vscode');

describe('RemoteContentProvider', () => {
  describe('provideTextDocumentContent', () => {
    it('returns decoded fragment', () => {
      const provider = new RemoteContentProvider();
      const mockUri = { fragment: 'Hello%20World' } as any;
      expect(provider.provideTextDocumentContent(mockUri)).toBe('Hello World');
    });

    it('returns "No content provided" if no fragment', () => {
      const provider = new RemoteContentProvider();
      const mockUri = {} as any;
      expect(provider.provideTextDocumentContent(mockUri)).toBe('No content provided');
    });

    it('returns error message on decode failure', () => {
      const provider = new RemoteContentProvider();
      const mockUri = { fragment: '%invalid' } as any;
      const result = provider.provideTextDocumentContent(mockUri);
      expect(result).toContain('Error decoding content');
    });
  });

  describe('onDidChange', () => {
    it('provides event emitter', () => {
      const provider = new RemoteContentProvider();
      expect(provider.onDidChange).toBeDefined();
    });
  });
});

describe('getLanguageId', () => {
  it('returns python for task', () => {
    expect(getLanguageId('task')).toBe('python');
  });

  it('returns python for library', () => {
    expect(getLanguageId('library')).toBe('python');
  });

  it('returns python for controller', () => {
    expect(getLanguageId('controller')).toBe('python');
  });

  it('returns html for view', () => {
    expect(getLanguageId('view')).toBe('html');
  });

  it('returns markdown for markdown', () => {
    expect(getLanguageId('markdown')).toBe('markdown');
  });

  it('returns undefined for unknown type', () => {
    expect(getLanguageId('unknown')).toBeUndefined();
  });

  it('returns undefined for service type', () => {
    expect(getLanguageId('service')).toBeUndefined();
  });
});

describe('registerRemoteLanguageModeSetter', () => {
  beforeEach(() => {
    const mockVscode = require('vscode');
    mockVscode.workspace.onDidOpenTextDocument = jest.fn();
    mockVscode.languages.setTextDocumentLanguage = jest.fn();
  });

  it('registers event listener for document open', () => {
    const mockVscode = require('vscode');
    mockVscode.workspace.onDidOpenTextDocument.mockReturnValue({ dispose: jest.fn() });
    const disposable = registerRemoteLanguageModeSetter();

    expect(mockVscode.workspace.onDidOpenTextDocument).toHaveBeenCalled();
    expect(disposable).toBeDefined();
  });

  it('sets language for pyops-remote scheme documents', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '?type=task' },
    };

    // Get the callback that was registered
    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).toHaveBeenCalledWith(mockDocument, 'python');
  });

  it('does not set language for non-pyops-remote scheme documents', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'file', query: '?type=task' },
    };

    // Get the callback that was registered
    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).not.toHaveBeenCalled();
  });

  it('handles documents without type parameter', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '' },
    };

    // Get the callback that was registered
    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).not.toHaveBeenCalled();
  });

  it('handles documents with unknown type parameter', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '?type=unknown' },
    };

    // Get the callback that was registered
    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).not.toHaveBeenCalled();
  });
});
