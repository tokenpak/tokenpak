/**
 * TokenPak — Deterministic context compression for LLMs
 *
 * @packageDocumentation
 *
 * @example
 * ```typescript
 * import { CompressionEngine } from 'tokenpak';
 *
 * const engine = new CompressionEngine({ baseUrl: 'http://localhost:8000' });
 * const result = await engine.compress("Very long prompt text...");
 * console.log(`Saved ${result.savingsPct.toFixed(1)}% tokens`);
 * ```
 */

export { CompressionEngine } from './CompressionEngine';
export { CacheManager } from './CacheManager';
export { BlockRegistry } from './BlockRegistry';
export { TelemetryCollector } from './TelemetryCollector';
export { TokenPakHttpClient } from './client';

export type {
  // Config
  TokenPakConfig,
  // Compression
  CompressOptions,
  CompressResult,
  ConversationMessage,
  ConversationCompressOptions,
  ConversationCompressResult,
  CompressionStrategy,
  // Cache
  CacheEntry,
  CacheStats,
  // Blocks
  Block,
  BlockRegistryStats,
  // Telemetry
  TelemetryEvent,
  TelemetryStats,
  // Health
  HealthStatus,
} from './types';

export {
  TokenPakError,
  TokenPakConnectionError,
  TokenPakTimeoutError,
} from './types';

/** Package version */
export const VERSION = '1.0.0';
