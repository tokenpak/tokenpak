/**
 * TokenPak CacheManager
 * Wraps the /cache/* HTTP endpoints.
 */
import { CacheStats, TokenPakConfig } from './types';
export declare class CacheManager {
    private readonly client;
    constructor(config?: TokenPakConfig);
    /**
     * Get a cached value by key.
     * Returns null if the key is not found or has expired.
     */
    get(key: string): Promise<string | null>;
    /**
     * Store a value in the cache.
     *
     * @param key   Cache key
     * @param value Value to store
     * @param ttl   Time-to-live in seconds (0 = no expiry)
     */
    set(key: string, value: string, ttl?: number): Promise<void>;
    /**
     * Delete a cached entry by key.
     */
    delete(key: string): Promise<boolean>;
    /**
     * Clear all cached entries.
     */
    clear(): Promise<void>;
    /**
     * Retrieve cache statistics (hit rate, entry count, memory usage).
     */
    stats(): Promise<CacheStats>;
}
//# sourceMappingURL=CacheManager.d.ts.map