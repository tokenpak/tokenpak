/**
 * TokenPak HTTP Client
 * Low-level HTTP client for the TokenPak Python service.
 */
import { TokenPakConfig, HealthStatus } from './types';
export declare class TokenPakHttpClient {
    private readonly http;
    readonly baseUrl: string;
    constructor(config?: TokenPakConfig);
    get<T>(path: string): Promise<T>;
    post<T>(path: string, body: unknown): Promise<T>;
    health(): Promise<HealthStatus>;
    private wrapError;
}
//# sourceMappingURL=client.d.ts.map