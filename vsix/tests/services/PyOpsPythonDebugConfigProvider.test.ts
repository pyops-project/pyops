import { PyOpsPythonDebugConfigProvider } from '../../src/services/PyOpsPythonDebugConfigProvider';
import { DebugService } from '../../src/services/DebugService';

jest.mock('vscode');
jest.mock('path');

jest.mock('../../src/services/DebugService', () => {
  const mockDebugService = jest.fn().mockImplementation(() => ({
    enqueueDebugJob: jest.fn()
  }));
  // @ts-ignore
  mockDebugService.buildOverrides = jest.fn();
  return { DebugService: mockDebugService };
});

const mockPath = require('path');
mockPath.sep = '/';
mockPath.relative = jest.fn((from: string, to: string) => {
  if (to.startsWith(from + '/')) return to.slice(from.length + 1);
  return to;
});
mockPath.dirname = jest.fn((p: string) => p.split('/').slice(0, -1).join('/') || '.');
mockPath.basename = jest.fn((p: string, ext?: string) => {
  const base = p.split('/').pop() || '';
  return ext ? base.replace(new RegExp(ext + '$'), '') : base;
});
mockPath.join = jest.fn((...parts: string[]) => parts.join('/'));

describe('PyOpsPythonDebugConfigProvider', () => {
    let provider: PyOpsPythonDebugConfigProvider;
    let mockDebugService: jest.Mocked<DebugService>;
    let buildOverridesSpy: jest.SpyInstance;
    let enqueueDebugJobSpy: jest.SpyInstance;

    beforeEach(() => {
        const mockOutputChannel = {
            appendLine: jest.fn(),
            name: 'PyOps',
            append: jest.fn(),
            replace: jest.fn(),
            clear: jest.fn(),
            show: jest.fn(),
            hide: jest.fn(),
            dispose: jest.fn(),
        };
        mockDebugService = new DebugService({} as any, mockOutputChannel as any) as jest.Mocked<DebugService>;
        provider = new PyOpsPythonDebugConfigProvider(mockDebugService);
        buildOverridesSpy = jest.spyOn(DebugService, 'buildOverrides');
        enqueueDebugJobSpy = jest.spyOn(mockDebugService, 'enqueueDebugJob');

        const pathModule = require('path');
        pathModule.relative.mockImplementation((from: string, to: string) => {
            if (to.startsWith(from + '/')) return to.slice(from.length + 1);
            return to;
        });
        pathModule.dirname.mockImplementation((p: string) => p.split('/').slice(0, -1).join('/') || '.');
        pathModule.basename.mockImplementation((p: string, ext?: string) => {
            const base = p.split('/').pop() || '';
            return ext ? base.replace(new RegExp(ext.replace(/\./g, '\\.') + '$'), '') : base;
        });

        (require('vscode').workspace as any).workspaceFolders = [{ uri: { fsPath: '/workspace' } }];
        (require('vscode').window as any).activeTextEditor = { document: { languageId: 'python', uri: { fsPath: '/workspace/pyops/set1/task/script.py' } } };
        (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
            get: jest.fn((key: string) => {
                if (key === 'localPyopsDir') return 'pyops';
                return undefined;
            })
        });
    });

    afterEach(() => {
        jest.clearAllMocks();
        buildOverridesSpy.mockRestore();
        enqueueDebugJobSpy.mockRestore();
    });

    it('should return config unchanged if no folder provided', async () => {
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(undefined, config);
        expect(result).toEqual(config);
    });

    it('should return attach config unchanged without enqueuing', async () => {
        const config = { name: 'test', type: 'python', request: 'attach' } as any;
        const result = await provider.resolveDebugConfiguration({ uri: { fsPath: '/workspace' } } as any, config);
        expect(result).toEqual(config);
        expect(enqueueDebugJobSpy).not.toHaveBeenCalled();
    });

    it('should return config unchanged if no workspace folders', async () => {
        (require('vscode').workspace as any).workspaceFolders = undefined;

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return config unchanged if no active text editor', async () => {
        (require('vscode').window as any).activeTextEditor = undefined;

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return config unchanged if active editor is not Python', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'javascript', uri: { fsPath: '/workspace/pyops/task/set1/script.py' } }
        };

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return config unchanged if file is not in pyops/task/ directory', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/workspace/pyops/library/set1/script.py' } }
        };

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return config unchanged if relative path starts with ..', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/outside/pyops/task/set1/script.py' } }
        };

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return config unchanged if path parts are insufficient', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/workspace/pyops/task/script.py' } }
        };

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);
        expect(result).toEqual(config);
    });

    it('should return attach config for valid pyops task file', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/workspace/pyops/set1/task/script.py' } }
        };

        (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
            get: jest.fn((key: string) => {
                if (key === 'localPyopsDir') return 'pyops';
                return undefined;
            })
        });

        const mockOverrides = { 'set1::override1': 'code1', 'set1::override2': 'code2' };
        const mockPathMappings = [{ localRoot: '${workspaceFolder}/pyops', remoteRoot: './.debug/src' }];
        buildOverridesSpy.mockReturnValue({ overrides: mockOverrides, pathMappings: mockPathMappings });
        enqueueDebugJobSpy.mockResolvedValue({ jobId: 'job123', port: 5678 });

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);

        expect(buildOverridesSpy).toHaveBeenCalledWith('set1', '/workspace');
        expect(enqueueDebugJobSpy).toHaveBeenCalledWith('set1', 'script', mockOverrides);
        expect(result).toEqual({
            name: 'Attach PyOps set1::script',
            type: 'python',
            request: 'attach',
            connect: { host: 'localhost', port: 5678 },
            path: '/workspace/pyops/set1/task/script.py',
            pathMappings: mockPathMappings,
            jobId: 'job123'
        });
    });

    it('should return undefined and show error if enqueueDebugJob fails', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/workspace/pyops/set1/task/script.py' } }
        };

        (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
            get: jest.fn((key: string) => {
                if (key === 'localPyopsDir') return 'pyops';
                return undefined;
            })
        });

        buildOverridesSpy.mockReturnValue({ overrides: {}, pathMappings: [] });
        enqueueDebugJobSpy.mockResolvedValue({ jobId: undefined, port: 0 });

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);

        expect(require('vscode').window.showErrorMessage).toHaveBeenCalledWith('Failed to enqueue PyOps debug job');
        expect(result).toBeUndefined();
    });

    it('should handle custom localPyopsDir config', async () => {
        (require('vscode').window as any).activeTextEditor = {
            document: { languageId: 'python', uri: { fsPath: '/workspace/custom/set1/task/script.py' } }
        };

        (require('vscode').workspace.getConfiguration as jest.Mock).mockReturnValue({
            get: jest.fn((key: string) => {
                if (key === 'localPyopsDir') return 'custom';
                return undefined;
            })
        });

        buildOverridesSpy.mockReturnValue({ overrides: {}, pathMappings: [] });
        enqueueDebugJobSpy.mockResolvedValue({ jobId: 'job456', port: 9999 });

        const folder = { uri: { fsPath: '/workspace' } } as any;
        const config = { name: 'test', type: 'python', request: 'launch' } as any;
        const result = await provider.resolveDebugConfiguration(folder, config);

        expect(buildOverridesSpy).toHaveBeenCalledWith('set1', '/workspace');
        expect(enqueueDebugJobSpy).toHaveBeenCalledWith('set1', 'script', {});
        expect(result).toEqual({
            name: 'Attach PyOps set1::script',
            type: 'python',
            request: 'attach',
            connect: { host: 'localhost', port: 9999 },
            path: '/workspace/custom/set1/task/script.py',
            pathMappings: [],
            jobId: 'job456'
        });
    });
});
