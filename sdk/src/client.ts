/**
 * TokenPak HTTP Client
 * Low-level HTTP client for the TokenPak Python service.
 */

import axios, { AxiosInstance, AxiosError } from 'axios';
import {
  TokenPakConfig,
  TokenPakError,
  TokenPakConnectionError,
  TokenPakTimeoutError,
  HealthStatus,
} from './types';

const DEFAULT_BASE_URL = 'http://localhost:8000';
const DEFAULT_TIMEOUT_MS = 30_000;

export class TokenPakHttpClient {
  private readonly http: AxiosInstance;
  readonly baseUrl: string;

  constructor(config: TokenPakConfig = {}) {
    this.baseUrl = config.baseUrl ?? DEFAULT_BASE_URL;

    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: config.timeout ?? DEFAULT_TIMEOUT_MS,
      headers: {
        'Content-Type': 'application/json',
        ...(config.apiKey ? { 'X-API-Key': config.apiKey } : {}),
        ...config.headers,
      },
    });
  }

  async get<T>(path: string): Promise<T> {
    try {
      const resp = await this.http.get<T>(path);
      return resp.data;
    } catch (err) {
      throw this.wrapError(err);
    }
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    try {
      const resp = await this.http.post<T>(path, body);
      return resp.data;
    } catch (err) {
      throw this.wrapError(err);
    }
  }

  async health(): Promise<HealthStatus> {
    return this.get<HealthStatus>('/health');
  }

  private wrapError(err: unknown): TokenPakError {
    if (axios.isAxiosError(err)) {
      const axiosErr = err as AxiosError;
      if (axiosErr.code === 'ECONNREFUSED' || axiosErr.code === 'ENOTFOUND') {
        return new TokenPakConnectionError(this.baseUrl, err);
      }
      if (axiosErr.code === 'ECONNABORTED' || axiosErr.message?.includes('timeout')) {
        return new TokenPakTimeoutError(DEFAULT_TIMEOUT_MS);
      }
      return new TokenPakError(
        axiosErr.message,
        axiosErr.response?.status,
        err
      );
    }
    return new TokenPakError(String(err), undefined, err);
  }
}
