jest.mock('vscode');

import * as vscode from 'vscode';
import { ApiClient } from '../src/apiClient';

describe('ApiClient', () => {
  let context: any;
  let apiClient: ApiClient;

  beforeEach(() => {
    (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
      get: jest.fn((key: string) => {
        if (key === 'serverUrl') return 'http://localhost:8000';
        if (key === 'username') return 'testuser';
        return undefined;
      }),
    });
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
      (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return undefined;
          return undefined;
        }),
      });
      const url = apiClient.getServerUrl();
      expect(url).toBe('http://localhost:8000');
    });

    it('should return custom default when serverUrl not set', () => {
      (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'serverUrl') return undefined;
          return undefined;
        }),
      });
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
      (vscode.workspace.getConfiguration as jest.Mock).mockReturnValue({
        get: jest.fn((key: string) => {
          if (key === 'username') return undefined;
          return undefined;
        }),
      });
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({});
    });

    it('should return empty headers when password is not set', async () => {
      (context.workspaceState.get as jest.Mock).mockReturnValue(undefined);
      const headers = await apiClient.getHeaders();
      expect(headers).toEqual({});
    });
  });
});
