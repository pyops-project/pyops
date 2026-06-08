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

    it('returns js for jslibrary', () => {
      expect(inferExt('jslibrary')).toBe('js');
    });

    it('returns empty string for unknown type', () => {
      expect(inferExt('unknown')).toBe('');
    });

    it('returns py for service type', () => {
      expect(inferExt('service')).toBe('py');
    });

    describe('data type', () => {
      it('defaults to yaml without hint', () => {
        expect(inferExt('data')).toBe('yaml');
      });

      it('returns yaml for explicit yaml hint', () => {
        expect(inferExt('data', 'yaml')).toBe('yaml');
      });

      it('returns yml for yml hint (passthrough — both are YAML on disk)', () => {
        expect(inferExt('data', 'yml')).toBe('yml');
      });

      it('returns json for explicit json hint', () => {
        expect(inferExt('data', 'json')).toBe('json');
      });

      it('falls back to yaml for unknown hint', () => {
        expect(inferExt('data', 'xml')).toBe('yaml');
      });
    });
  });


});
