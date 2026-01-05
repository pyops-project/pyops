import { inferExt } from '../src/utils';

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


});
