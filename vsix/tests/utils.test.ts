import { inferExt, hasValidConfig } from '../src/utils';

jest.mock('vscode');

describe('utils', () => {
  describe('inferExt', () => {
    it('returns py for task', () => {
      expect(inferExt('task')).toBe('py');
    });

    it('returns py for library', () => {
      expect(inferExt('library')).toBe('py');
    });

    it('returns py for controller', () => {
      expect(inferExt('controller')).toBe('py');
    });

    it('returns html for view', () => {
      expect(inferExt('view')).toBe('html');
    });

    it('returns md for markdown', () => {
      expect(inferExt('markdown')).toBe('md');
    });

    it('returns empty string for unknown type', () => {
      expect(inferExt('unknown')).toBe('');
    });

    it('returns empty string for service type', () => {
      expect(inferExt('service')).toBe('');
    });
  });

  describe('hasValidConfig', () => {
    it('returns true when serverUrl is configured', () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return 'http://localhost:8000';
          return '';
        }),
      });

      expect(hasValidConfig()).toBe(true);
    });

    it('returns false when serverUrl is empty', () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return '';
          return '';
        }),
      });

      expect(hasValidConfig()).toBe(false);
    });

    it('returns false when serverUrl is undefined', () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return undefined;
          return '';
        }),
      });

      expect(hasValidConfig()).toBe(false);
    });

    it('returns false when serverUrl is null', () => {
      const mockVscode = require('vscode');
      mockVscode.workspace.getConfiguration.mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return null;
          return '';
        }),
      });

      expect(hasValidConfig()).toBe(false);
    });
  });
});
