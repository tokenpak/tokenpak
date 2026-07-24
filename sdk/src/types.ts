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

export type HealthCircuitState = 'closed' | 'open' | 'half_open';

export interface HealthMemoryGuardConfiguration {
  source: 'default' | 'environment' | 'managed' | 'managed_error';
  mode: 'off' | 'observe' | 'auto';
  plan_sha256: string | null;
  managed_config_path: string | null;
  managed_file_present: boolean;
  managed_file_ignored: boolean;
  triggering_env: string[];
  warning: string | null;
}

export interface HealthMemoryGuardCallbacks {
  compact: boolean;
  token: boolean;
  semantic: boolean;
}

export interface HealthMemoryMeasurementSupport {
  supported: boolean;
  platform: string;
  source: 'procfs' | 'psutil' | null;
  reason: string | null;
}

export interface HealthMemoryGuardRuntimeConfig {
  action_mode: 'observe' | 'auto';
  target_mb: number;
  ceiling_mb: number;
  hysteresis_mb: number;
  sys_low_mb: number;
  check_interval_secs: number;
  cooldown_secs: number;
}

export interface HealthMemoryGuardDisabled {
  enabled: false;
  state: 'disabled';
  thread_alive: false;
  callback_policy: 'disabled';
  configuration: HealthMemoryGuardConfiguration;
  callbacks: HealthMemoryGuardCallbacks;
}

export interface HealthMemoryGuardEnabled {
  enabled: true;
  state:
    | 'created'
    | 'unsupported'
    | 'running'
    | 'start_failed'
    | 'stopped'
    | 'stop_timeout'
    | 'degraded';
  thread_alive: boolean;
  callback_policy:
    | 'caller_supplied_eviction_callbacks'
    | 'gc_trim_only_no_unbounded_disposable_proxy_cache';
  configuration: HealthMemoryGuardConfiguration;
  callbacks: HealthMemoryGuardCallbacks;
  checks: number;
  measurement_errors: number;
  gc_runs: number;
  trim_runs: number;
  yellow_triggers: number;
  red_triggers: number;
  sys_low_triggers: number;
  suppressed_actions: number;
  observed_pressure_checks: number;
  compact_evictions: number;
  token_evictions: number;
  semantic_evictions: number;
  peak_rss_mb: number;
  last_rss_mb: number | null;
  last_sys_avail_mb: number | null;
  last_level: 'UNKNOWN' | 'GREEN' | 'YELLOW' | 'RED' | 'RECOVERY';
  last_reclaimed_mb: number;
  total_reclaimed_mb: number;
  pressure_latched: boolean;
  last_error: string | null;
  measurement: HealthMemoryMeasurementSupport;
  thread_ident: number | null;
  stopping: boolean;
  config: HealthMemoryGuardRuntimeConfig;
}

export type HealthMemoryGuard = HealthMemoryGuardDisabled | HealthMemoryGuardEnabled;

export interface HealthAdmission {
  limit: number;
  available: number;
  rejected: number;
}

export type HealthAgentConcurrency =
  | { enabled: false }
  | {
      enabled: true;
      max_parallel_subagents: number;
      effective_cap: number;
      degraded_serial: boolean;
      in_flight: number;
      queued: number;
      queue_depth_max: number;
      admitted_total: number;
      queued_total: number;
      rejected_queue_full: number;
      rejected_wait_timeout: number;
      source: string;
    };

export interface HealthConnectionPool {
  http2_enabled: boolean;
  active_providers: string[];
  total_requests: number;
  reused_connections: number;
  new_connections: number;
  errors: number;
  evicted_clients: number;
  reuse_rate: number;
  cleanup_pending_close: number;
  cleanup_queued: number;
  cleanup_in_progress: number;
  cleanup_retrying: number;
  cleanup_failures_total: number;
  cleanup_worker_start_failures_total: number;
  cleanup_completed_total: number;
  cleanup_oldest_pending_seconds: number;
  cleanup_workers_alive: number;
  client_slots_used: number;
  client_slots_max: number;
  client_capacity_rejections_total: number;
  cleanup_saturated: boolean;
  retired_pending_close: number;
}

export interface HealthCircuitBreakerProvider {
  state: HealthCircuitState;
  failures_in_window: number;
  successes_in_window: number;
  failure_ratio: number;
  failure_threshold: number;
  min_failure_ratio: number;
  time_until_probe_seconds: number | null;
  total_trips: number;
  total_successes: number;
  total_failures: number;
}

export interface HealthCircuitBreakers {
  enabled: boolean;
  any_open: boolean;
  providers: Record<string, HealthCircuitBreakerProvider>;
}

export interface HealthProviderDiagnostic {
  name: string;
  status: HealthCircuitState | 'unknown';
}

export type HealthMemoryMeasurement =
  | { rss_mb: number; available: true; reason?: never }
  | {
      rss_mb: null;
      available: false;
      reason: 'optional_dependency_unavailable' | 'probe_failed';
    };

export type HealthDiskMeasurement =
  | { available_gb: number; available: true; reason?: never }
  | { available_gb: null; available: false; reason: 'probe_failed' };

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'shutting_down';
  uptime_seconds: number;
  version: string;
  requests_total: number;
  requests_errors: number;
  compression_ratio_avg: number;
  is_degraded: boolean;
  is_shutting_down: boolean;
  in_flight_requests: number;
  memory_guard: HealthMemoryGuard;
  admission: HealthAdmission;
  agent_concurrency: HealthAgentConcurrency;
  timestamp: string;
  connection_pool: HealthConnectionPool;
  circuit_breakers: HealthCircuitBreakers;
  /** Additive fields returned only by `GET /health?deep=true`. */
  providers?: HealthProviderDiagnostic[];
  memory?: HealthMemoryMeasurement;
  disk?: HealthDiskMeasurement;
}

export interface DeepHealthStatus extends HealthStatus {
  providers: HealthProviderDiagnostic[];
  memory: HealthMemoryMeasurement;
  disk: HealthDiskMeasurement;
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
