"use strict";
/**
 * TokenPak CacheManager
 * Wraps the /cache/* HTTP endpoints.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CacheManager = void 0;
const client_1 = require("./client");
class CacheManager {
    constructor(config) {
        this.client = new client_1.TokenPakHttpClient(config);
    }
    /**
     * Get a cached value by key.
     * Returns null if the key is not found or has expired.
     */
    async get(key) {
        try {
            const raw = await this.client.get(`/cache/${encodeURIComponent(key)}`);
            return raw.value;
        }
        catch {
            return null;
        }
    }
    /**
     * Store a value in the cache.
     *
     * @param key   Cache key
     * @param value Value to store
     * @param ttl   Time-to-live in seconds (0 = no expiry)
     */
    async set(key, value, ttl = 0) {
        await this.client.post('/cache', { key, value, ttl });
    }
    /**
     * Delete a cached entry by key.
     */
    async delete(key) {
        try {
            await this.client.post(`/cache/${encodeURIComponent(key)}/delete`, {});
            return true;
        }
        catch {
            return false;
        }
    }
    /**
     * Clear all cached entries.
     */
    async clear() {
        await this.client.post('/cache/clear', {});
    }
    /**
     * Retrieve cache statistics (hit rate, entry count, memory usage).
     */
    async stats() {
        const raw = await this.client.get('/cache/stats');
        return {
            totalEntries: raw.total_entries,
            hitRate: raw.hit_rate,
            totalHits: raw.total_hits,
            totalMisses: raw.total_misses,
            memoryUsageBytes: raw.memory_usage_bytes,
        };
    }
}
exports.CacheManager = CacheManager;
//# sourceMappingURL=CacheManager.js.map