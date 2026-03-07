/**
 * TokenPak TypeScript SDK — Type Definitions
 * @module types
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface TokenPakConfig {
  /** Base URL of the TokenPak API server (default: http://localhost:8000) */
  baseUrl?: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeout?: number;
  /** API key for authentication (if TokenPak server requires it) */
  apiKey?: string;
  /** HTTP headers to include in every request */
  headers?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Compression
// ---------------------------------------------------------------------------

export type CompressionStrategy = 'heuristic' | 'semantic' | 'aggressive' | 'conservative';

export interface CompressOptions {
  /** Target token count (optional) */
  targetTokens?: number;
  /** Compression strategy (default: 'heuristic') */
  strategy?: CompressionStrategy;
  /** Whether to use cache for repeated inputs (default: true) */
  cache?: boolean;
  /** Preserve code blocks (default: true) */
  preserveCode?: boolean;
  /** Preserve headers/structure (default: true) */
  preserveStructure?: boolean;
}

export interface CompressResult {
  /** Original uncompressed text */
  originalText: string;
  /** Compressed output text */
  compressedText: string;
  /** Estimated token count before compression */
  originalTokens: number;
  /** Estimated token count after compression */
  compressedTokens: number;
  /** Percentage of tokens saved (0–100) */
  savingsPct: number;
  /** Whether this result was served from cache */
  cacheHit: boolean;
  /** Round-trip time in milliseconds */
  elapsedMs: number;
}

export interface ConversationMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ConversationCompressOptions {
  /** Number of recent messages to keep uncompressed (default: 3) */
  keepRecent?: number;
  /** Target total token budget (default: 4000) */
  targetTokens?: number;
}

export interface ConversationCompressResult {
  /** Compressed message array (ready to send to LLM) */
  messages: ConversationMessage[];
  /** Total token savings across conversation */
  totalSavings: number;
}

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------

export interface CacheEntry {
  key: string;
  value: string;
  createdAt: Date;
  expiresAt: Date | null;
  hits: number;
}

export interface CacheStats {
  totalEntries: number;
  hitRate: number;
  totalHits: number;
  totalMisses: number;
  memoryUsageBytes: number;
}

// ---------------------------------------------------------------------------
// Blocks
// ---------------------------------------------------------------------------

export interface Block {
  id: string;
  type: string;
  content: string;
  tokenCount: number;
  metadata?: Record<string, unknown>;
}

export interface BlockRegistryStats {
  totalBlocks: number;
  totalTokens: number;
  blocksByType: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Telemetry
// ---------------------------------------------------------------------------

export interface TelemetryEvent {
  eventType: string;
  timestamp: Date;
  data: Record<string, unknown>;
  sessionId?: string;
  model?: string;
}

export interface TelemetryStats {
  totalEvents: number;
  totalCost: number;
  totalTokensIn: number;
  totalTokensOut: number;
  averageLatencyMs: number;
  modelBreakdown: Record<string, {
    calls: number;
    cost: number;
    tokensIn: number;
    tokensOut: number;
  }>;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down';
  version: string;
  uptimeSeconds: number;
  stats: {
    requests: number;
    tokensSaved: number;
    cacheHits: number;
  };
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class TokenPakError extends Error {
  constructor(
    message: string,
    public readonly statusCode?: number,
    public readonly cause?: unknown
  ) {
    super(message);
    this.name = 'TokenPakError';
  }
}

export class TokenPakConnectionError extends TokenPakError {
  constructor(baseUrl: string, cause?: unknown) {
    super(`Cannot connect to TokenPak server at ${baseUrl}`, undefined, cause);
    this.name = 'TokenPakConnectionError';
  }
}

export class TokenPakTimeoutError extends TokenPakError {
  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = 'TokenPakTimeoutError';
  }
}
