/**
 * TokenPak CacheManager
 * Wraps the /cache/* HTTP endpoints.
 */

import { TokenPakHttpClient } from './client';
import { CacheEntry, CacheStats, TokenPakConfig } from './types';

interface RawCacheEntry {
  key: string;
  value: string;
  created_at: string;
  expires_at: string | null;
  hits: number;
}

interface RawCacheStats {
  total_entries: number;
  hit_rate: number;
  total_hits: number;
  total_misses: number;
  memory_usage_bytes: number;
}

export class CacheManager {
  private readonly client: TokenPakHttpClient;

  constructor(config?: TokenPakConfig) {
    this.client = new TokenPakHttpClient(config);
  }

  /**
   * Get a cached value by key.
   * Returns null if the key is not found or has expired.
   */
  async get(key: string): Promise<string | null> {
    try {
      const raw = await this.client.get<RawCacheEntry>(`/cache/${encodeURIComponent(key)}`);
      return raw.value;
    } catch {
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
  async set(key: string, value: string, ttl = 0): Promise<void> {
    await this.client.post('/cache', { key, value, ttl });
  }

  /**
   * Delete a cached entry by key.
   */
  async delete(key: string): Promise<boolean> {
    try {
      await this.client.post(`/cache/${encodeURIComponent(key)}/delete`, {});
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Clear all cached entries.
   */
  async clear(): Promise<void> {
    await this.client.post('/cache/clear', {});
  }

  /**
   * Retrieve cache statistics (hit rate, entry count, memory usage).
   */
  async stats(): Promise<CacheStats> {
    const raw = await this.client.get<RawCacheStats>('/cache/stats');
    return {
      totalEntries: raw.total_entries,
      hitRate: raw.hit_rate,
      totalHits: raw.total_hits,
      totalMisses: raw.total_misses,
      memoryUsageBytes: raw.memory_usage_bytes,
    };
  }
}
