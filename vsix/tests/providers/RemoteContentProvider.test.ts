import { RemoteContentProvider, getLanguageId, registerRemoteLanguageModeSetter } from '../../src/providers/RemoteContentProvider';

jest.mock('vscode');

describe('RemoteContentProvider', () => {
  describe('provideTextDocumentContent', () => {
    it('returns content from content map', () => {
      const provider = new RemoteContentProvider();
      provider.setContent('/testSet/testName', 'Hello World');
      const mockUri = { path: '/testSet/testName' } as any;
      expect(provider.provideTextDocumentContent(mockUri)).toBe('Hello World');
    });

    it('returns "No content provided" if path not in map', () => {
      const provider = new RemoteContentProvider();
      const mockUri = { path: '/unknown' } as any;
      expect(provider.provideTextDocumentContent(mockUri)).toBe('No content provided');
    });

    it('overwrites previous content for same path', () => {
      const provider = new RemoteContentProvider();
      provider.setContent('/testSet/testName', 'old content');
      provider.setContent('/testSet/testName', 'new content');
      const mockUri = { path: '/testSet/testName' } as any;
      expect(provider.provideTextDocumentContent(mockUri)).toBe('new content');
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

  it('returns javascript for jslibrary', () => {
    expect(getLanguageId('jslibrary')).toBe('javascript');
  });

  it('returns undefined for unknown type', () => {
    expect(getLanguageId('unknown')).toBeUndefined();
  });

  it('returns undefined for service type', () => {
    expect(getLanguageId('service')).toBeUndefined();
  });

  describe('data type', () => {
    it('defaults to yaml without ext hint', () => {
      expect(getLanguageId('data')).toBe('yaml');
    });

    it('returns yaml for yaml ext', () => {
      expect(getLanguageId('data', 'yaml')).toBe('yaml');
    });

    it('returns yaml for yml ext (alias)', () => {
      expect(getLanguageId('data', 'yml')).toBe('yaml');
    });

    it('returns json for json ext', () => {
      expect(getLanguageId('data', 'json')).toBe('json');
    });
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

  it('sets json language for data type with ext=json in query', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '?type=data&ext=json' },
    };

    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).toHaveBeenCalledWith(mockDocument, 'json');
  });

  it('sets yaml language for data type with ext=yaml in query', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '?type=data&ext=yaml' },
    };

    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).toHaveBeenCalledWith(mockDocument, 'yaml');
  });

  it('defaults to yaml for data type without ext in query', () => {
    const mockVscode = require('vscode');
    registerRemoteLanguageModeSetter();

    const mockDocument = {
      uri: { scheme: 'pyops-remote', query: '?type=data' },
    };

    const callback = mockVscode.workspace.onDidOpenTextDocument.mock.calls[0][0];
    callback(mockDocument);

    expect(mockVscode.languages.setTextDocumentLanguage).toHaveBeenCalledWith(mockDocument, 'yaml');
  });
});
