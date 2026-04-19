# TokenPak Environment Variables

All configuration is via environment variables (or `~/.tokenpak/config.yaml`).
Env vars always take precedence over config file values.

## Core Server

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_PORT` | `8766` | int | Proxy listen port |
| `TOKENPAK_BIND_ADDRESS` | `127.0.0.1` | str | Proxy listen address (use `0.0.0.0` for LAN/Docker) |
| `TOKENPAK_PROXY_KEY` | `""` | str | Optional pre-shared key required on all requests (empty = disabled) |
| `TOKENPAK_DASHBOARD_AUTH` | `true` | bool | Require auth token to access `/stats`, `/health`, `/vault` dashboards |
| `TOKENPAK_DB` | `<proxy_dir>/monitor.db` | str | Path to SQLite monitor database |
| `TOKENPAK_PROFILE` | `balanced` | str | Active cost/quality profile: `balanced`, `economy`, `quality` |

## Compression / Context Reduction

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_COMPACT` | `true` | bool | Master on/off switch for context compaction |
| `TOKENPAK_MODE` | `hybrid` | str | Compilation mode: `strict`, `hybrid`, `aggressive` |
| `TOKENPAK_COMPACT_MAX_CHARS` | `120` | int | Max chars per compacted text segment |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | int | Skip compaction below this token count |
| `TOKENPAK_COMPACT_MAX_TOKENS` | `2000` | int | Hard cap on tokens injected from compaction |
| `TOKENPAK_COMPACT_CACHE_SIZE` | `2000` | int | LRU cache size for compaction results |
| `MAX_COMPRESSION_TIME_MS` | `5000` | int | Max time budget (ms) for entire compression pipeline; 0 = no cap |

## Vault / Retrieval

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_VAULT_INDEX` | `~/vault/.tokenpak` | str | Path to vault `.tokenpak` index directory |
| `TOKENPAK_VAULT_INDEX_RELOAD_INTERVAL` | `300` | int | Seconds between vault index reloads |
| `TOKENPAK_VAULT_MEMORY_MAX` | `268435456` (256MB) | int | Max bytes for Tier 2 LRU content cache |
| `TOKENPAK_VAULT_CACHE_PRELOAD` | `200` | int | Number of recently-modified blocks to preload into cache at startup |
| `TOKENPAK_INJECT_BUDGET` | `4000` | int | Max tokens to inject from vault per request |
| `TOKENPAK_INJECT_TOP_K` | `5` | int | Max vault blocks to inject per request |
| `TOKENPAK_INJECT_MIN_SCORE` | `2.0` | float | Minimum BM25 score to include a block |
| `TOKENPAK_INJECT_SKIP_MODELS` | `haiku` | str | Comma-separated model name prefixes to skip vault injection |
| `TOKENPAK_INJECT_MIN_PROMPT` | `1000` | int | Minimum prompt chars before vault injection is attempted |
| `TOKENPAK_RETRIEVAL_BACKEND` | `json_blocks` | str | Vault retrieval backend: `json_blocks` or `sqlite` |

## Upstream / Routing

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_UPSTREAM_TIMEOUT` | `300` | int | Upstream request timeout in seconds |
| `TOKENPAK_ROUTER_ENABLED` | `true` | bool | Enable model/provider router |
| `TOKENPAK_OLLAMA_UPSTREAM` | `http://100.80.241.118:11434` | str | Ollama upstream URL |
| `TOKENPAK_OLLAMA_TIMEOUT` | `20` | int | Ollama connection timeout in seconds |
| `TOKENPAK_MAX_RETRIES` | `3` | int | Max upstream retry attempts |
| `TOKENPAK_BACKOFF_BASE` | `1.0` | float | Retry backoff base multiplier (seconds) |
| `TOKENPAK_BACKOFF_CAP` | `32.0` | float | Retry backoff maximum cap (seconds) |
| `TOKENPAK_KEY_ROTATION` | `failover` | str | API key rotation strategy: `failover`, `round-robin` |

## Rate Limiting & Request Guards

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_RATE_LIMIT_RPM` | `60` | int | Max requests per minute per client IP (0 = disabled) |
| `TOKENPAK_MAX_REQUEST_SIZE` | `10485760` (10MB) | int | Max request body size in bytes |
| `TOKENPAK_STRICT_MODE` | `false` | bool | Strict request validation (reject ambiguous/malformed requests) |
| `TOKENPAK_VALIDATION_GATE` | `true` | bool | Enable pre-flight token budget validation gate |
| `TOKENPAK_VALIDATION_GATE_BUDGET_CAP` | `120000` | int | Max tokens allowed through validation gate |
| `TOKENPAK_VALIDATION_GATE_SOFT` | `true` | bool | Soft mode: warn instead of reject on validation failures |

## Budget & Cost Control

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_BUDGET_TOTAL` | `12000` | int | Per-request token budget target |
| `TOKENPAK_BUDGET_DAILY_LIMIT_USD` | `0` | float | Daily spend cap in USD (0 = disabled) |
| `TOKENPAK_BUDGET_ALERT_PCT` | `80` | float | Alert threshold as % of daily limit |

## Feature Flags (Tier 1 — default OFF)

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_SEMANTIC_CACHE` | `false` | bool | Short-circuit cache for duplicate/similar queries |
| `TOKENPAK_PREFIX_REGISTRY` | `false` | bool | Stable prefix tracking for cache optimization |
| `TOKENPAK_COMPRESSION_DICT` | `false` | bool | Post-compaction dictionary compression |
| `TOKENPAK_TRACE` | `false` | bool | Pipeline tracing (debug) |

## Feature Flags (Tier 2A — default OFF)

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_ERROR_NORMALIZER` | `false` | bool | Normalize error responses across providers |
| `TOKENPAK_BUDGET_CONTROLLER` | `false` | bool | Enforce per-request token budget limits |
| `TOKENPAK_REQUEST_LOGGER` | `false` | bool | Structured request/response logging |
| `TOKENPAK_SALIENCE_ROUTER` | `false` | bool | Content-type-aware extraction before compaction |

## Feature Flags (Tier 2B — default OFF)

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_CACHE_REGISTRY` | `false` | bool | Unified stable/volatile cache registry |
| `TOKENPAK_RETRIEVAL_WATCHDOG` | `false` | bool | Monitor retrieval latency and quality |
| `TOKENPAK_FAILURE_MEMORY` | `false` | bool | Track and learn from upstream failures |
| `TOKENPAK_FIDELITY_TIERS` | `false` | bool | Tiered response fidelity based on cost/quality tradeoffs |

## Feature Flags (Phase 3 — default OFF)

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_SESSION_CAPSULES` | `false` | bool | Session-scoped capsule summarization |
| `TOKENPAK_PRECONDITION_GATES` | `false` | bool | Guard rails before expensive operations |
| `TOKENPAK_QUERY_REWRITER` | `false` | bool | Automatic query rewriting for better retrieval |
| `TOKENPAK_STABILITY_SCORER` | `false` | bool | Score and rank response stability |

## Other Features

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_CAPSULE_BUILDER` | `false` | bool | Enable capsule builder compression stage |
| `TOKENPAK_CAPSULE_MIN_CHARS` | `400` | int | Minimum block chars for capsule compression |
| `TOKENPAK_CAPSULE_HOT_WINDOW` | `2` | int | Trailing messages excluded from capsule compression |
| `TOKENPAK_SKELETON_ENABLED` | `true` | bool | Strip function bodies from code blocks before injection |
| `TOKENPAK_SHADOW_ENABLED` | `true` | bool | Shadow reader for passive observation |
| `TOKENPAK_TERM_RESOLVER_ENABLED` | `false` | bool | Term-card resolver (inline term definitions) |
| `TOKENPAK_TERM_RESOLVER_TOP_K` | `3` | int | Max term cards to inject per request |
| `TOKENPAK_TERM_RESOLVER_MAX_BYTES` | `200` | int | Max bytes per term card |
| `TOKENPAK_CHAT_FOOTER` | `false` | bool | Append cost/token footer to chat responses |
| `TOKENPAK_HTTP100_KEEPALIVE` | `false` | bool | Send HTTP 100 Continue for keepalive |

## WebSocket

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TOKENPAK_WS_PORT` | `8767` | int | WebSocket proxy port |
| `TOKENPAK_WS_MAX_CONNECTIONS` | `50` | int | Max concurrent WebSocket connections |

---

*Generated from `proxy.py` and `packages/core/proxy.py` on 2026-03-28.*
*To add a variable, use `_cfg("config.key", default, "TOKENPAK_VAR_NAME", type)` in proxy.py.*
