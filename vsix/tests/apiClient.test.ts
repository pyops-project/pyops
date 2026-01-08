jest.mock('vscode');

import * as vscode from 'vscode';
import { ApiClient } from '../src/apiClient';

describe('ApiClient', () => {
  let context: any;
  let apiClient: ApiClient;

  beforeEach(() => {
    require('./utils/helpers').setupConfigMock({serverUrl: 'http://localhost:8000', username: 'testuser'});
    context = {
      workspaceState: {
        get: jest.fn((key) => key === 'pyops.password' ? 'testpassword' : undefined),
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
      (context.workspaceState.get as jest.Mock).mockReturnValue(undefined);
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({});
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
