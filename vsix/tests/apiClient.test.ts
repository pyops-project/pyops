jest.mock('vscode');

import * as vscode from 'vscode';
import { ApiClient } from '../src/apiClient';

describe('ApiClient', () => {
  let context: any;
  let apiClient: ApiClient;

  beforeEach(() => {
    require('./utils/helpers').setupConfigMock({serverUrl: 'http://localhost:8000', username: 'testuser'});
    context = {
      secrets: {
        get: jest.fn((key: string) => key === 'pyops.password' ? Promise.resolve('testpassword') : Promise.resolve(undefined)),
        store: jest.fn(),
        delete: jest.fn(),
      },
    };
    apiClient = new ApiClient(context);
  });

  describe('getServerUrl', () => {
    it('should return configured serverUrl when set', () => {
      const url = apiClient.getServerUrl();
      expect(url).toBe('http://localhost:8000');
    });

    it('should return default when serverUrl not set', () => {
      require('./utils/helpers').setupConfigMock({serverUrl: undefined});
      const url = apiClient.getServerUrl();
      expect(url).toBe('http://localhost:8000');
    });

    it('should return custom default when serverUrl not set', () => {
      require('./utils/helpers').setupConfigMock({serverUrl: undefined});
      const url = apiClient.getServerUrl('http://custom:8000');
      expect(url).toBe('http://custom:8000');
    });
  });

  describe('getHeaders', () => {
    it('should return headers with authorization when username and password are set', async () => {
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({
        'Authorization': 'Basic ' + Buffer.from('testuser:testpassword').toString('base64'),
      });
    });

    it('should return empty headers when username is not set', async () => {
      require('./utils/helpers').setupConfigMock({username: undefined});
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({});
    });

    it('should return empty headers when password is not set', async () => {
      (context.secrets.get as jest.Mock).mockResolvedValue(undefined);
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({});
    });

    it('sends X-User (from username) and no Authorization in x-user mode', async () => {
      require('./utils/helpers').setupConfigMock({authMode: 'x-user', username: 'alice'});
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({ 'X-User': 'alice' });
      expect(context.secrets.get).not.toHaveBeenCalled();
    });

    it('falls back to DEFAULT_X_USER when username is unset in x-user mode', async () => {
      require('./utils/helpers').setupConfigMock({authMode: 'x-user', username: undefined});
      const { DEFAULT_X_USER } = require('../src/apiClient');
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({ 'X-User': DEFAULT_X_USER });
    });
  });

  describe('hasValidConfig', () => {
    it('delegates to hasValidConfigStatic', () => {
      require('./utils/helpers').setupConfigMock();
      expect(apiClient.hasValidConfig()).toBe(true);
    });
  });

  describe('fetch', () => {
    beforeEach(() => {
      global.fetch = jest.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    });

    afterEach(() => {
      delete (global as any).fetch;
    });

    it('constructs URL from serverUrl and path', async () => {
      await apiClient.fetch('/test/path');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/test/path',
        expect.objectContaining({ headers: expect.any(Object) })
      );
    });

    it('merges auth headers with provided init headers', async () => {
      await apiClient.fetch('/test', { headers: { 'Content-Type': 'application/json' } });
      const callArgs = (global.fetch as jest.Mock).mock.calls[0];
      expect(callArgs[1].headers).toEqual(expect.objectContaining({
        'Authorization': expect.stringContaining('Basic'),
        'Content-Type': 'application/json',
      }));
    });

    it('passes method and body through', async () => {
      await apiClient.fetch('/test', { method: 'POST', body: '{"key":"val"}' });
      const callArgs = (global.fetch as jest.Mock).mock.calls[0];
      expect(callArgs[1].method).toBe('POST');
      expect(callArgs[1].body).toBe('{"key":"val"}');
    });

    it('works with no init parameter', async () => {
      await apiClient.fetch('/test');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/test',
        expect.objectContaining({ headers: expect.any(Object) })
      );
    });
  });

  describe('fetchJson', () => {
    beforeEach(() => {
      global.fetch = jest.fn();
    });

    afterEach(() => {
      delete (global as any).fetch;
    });

    it('returns parsed JSON on success', async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ data: 'test' }),
      });
      const result = await apiClient.fetchJson('/test');
      expect(result).toEqual({ data: 'test' });
    });

    it('throws on non-ok response', async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: () => Promise.resolve({}),
      });
      await expect(apiClient.fetchJson('/test')).rejects.toThrow('404 Not Found');
    });
  });

  describe('hasValidConfigStatic', () => {
    it('returns true when serverUrl is configured', () => {
      require('./utils/helpers').setupConfigMock();

      expect(ApiClient.hasValidConfigStatic()).toBe(true);
    });

    it('returns false when serverUrl is empty', () => {
      require('./utils/helpers').setupConfigMock({serverUrl: ''});

      expect(ApiClient.hasValidConfigStatic()).toBe(false);
    });

    it('returns false when serverUrl is undefined', () => {
      require('./utils/helpers').setupConfigMock({serverUrl: undefined});

      expect(ApiClient.hasValidConfigStatic()).toBe(false);
    });

    it('returns false when serverUrl is null', () => {
      require('./utils/helpers').setupConfigMock({serverUrl: null});

      expect(ApiClient.hasValidConfigStatic()).toBe(false);
    });
  });
});
