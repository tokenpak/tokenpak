# TokenPak API Reference

**Complete reference for the TokenPak proxy HTTP API, SDK adapters, and CLI.**

---

## Table of Contents

1. [Proxy HTTP API](#proxy-http-api)
   - [Authentication](#authentication)
   - [GET Endpoints](#get-endpoints)
   - [POST Endpoints](#post-endpoints)
   - [Error Format](#error-format)
2. [SDK Adapters](#sdk-adapters)
   - [Base Adapter (TokenPakAdapter)](#base-adapter-tokenpakadapter)
   - [AnthropicAdapter](#anthropicadapter)
   - [OpenAIAdapter](#openaiadapter)
   - [LangChainAdapter](#langchainadapter)
   - [LiteLLMAdapter](#litellmadapter)
   - [Exception Hierarchy](#exception-hierarchy)
3. [CLI Commands](#cli-commands)
   - [Proxy Lifecycle](#proxy-lifecycle)
   - [Indexing & Search](#indexing--search)
   - [Monitoring & Stats](#monitoring--stats)
   - [Diagnostics](#diagnostics)
   - [Config Management](#config-management)
   - [Advanced Commands](#advanced-commands)
4. [Configuration Reference](#configuration-reference)
   - [Environment Variables](#environment-variables)
   - [config.yaml](#configyaml)

---

## Proxy HTTP API

The TokenPak proxy runs on `localhost:8766` by default. It accepts standard HTTP requests and transparently forwards them to upstream providers after applying compression and context injection.

### Authentication

By default, TokenPak allows unauthenticated requests from localhost. For remote clients, authentication is required via header:

| Header | Value | Notes |
|--------|-------|-------|
| `X-TokenPak-Key` | `<your-proxy-key>` | Required for non-localhost clients |
| `x-api-key` | `<provider-api-key>` | Provider key, forwarded to upstream |
| `Authorization` | `Bearer <token>` | Alternative to `x-api-key` |

Requests from non-localhost without `X-TokenPak-Key` receive `401 Unauthorized`.

---

### GET Endpoints

#### `GET /`

Welcome / status endpoint. Returns proxy identity and available endpoints.

**Response:**
```json
{
  "name": "TokenPak",
  "version": "0.5.0",
  "status": "running",
  "endpoints": {
    "health": "/health",
    "stats": "/stats",
    "docs": "/docs",
    "proxy": "/v1/messages (POST), /v1/chat/completions (POST)"
  },
  "docs": "https://github.com/kaywhy331/tokenpak"
}
```

---

#### `GET /health`

Lightweight health check. Cached for 1 second to reduce overhead.

**Response:**
```json
{
  "status": "ok",
  "compilation_mode": "hybrid",
  "vault_index": {
    "available": true,
    "blocks": 42,
    "path": "/home/user/vault/.tokenpak"
  },
  "router": {
    "enabled": true,
    "rules_loaded": 5
  },
  "capsule_available": false,
  "budget": {
    "enabled": true,
    "total_tokens": 4000
  },
  "circuit_breakers": {
    "anthropic": { "open": false, "failures": 0 }
  },
  "stats": {
    "requests": 142,
    "input_tokens": 380000,
    "sent_input_tokens": 210000,
    "saved_tokens": 170000,
    "errors": 2,
    "cache_hits": 37,
    "cost": 0.85
  },
  "latency": {
    "p50_latency_ms": 320,
    "p99_latency_ms": 1840,
    "samples": 100
  }
}
```

Also supports `HEAD /health` (returns 200 with no body — useful for Kubernetes liveness probes).

---

#### `GET /stats`

Full session statistics. Heavier than `/health` — includes per-model breakdown and recent requests.

**Response:**
```json
{
  "session": {
    "requests": 142,
    "input_tokens": 380000,
    "sent_input_tokens": 210000,
    "saved_tokens": 170000,
    "output_tokens": 95000,
    "cost": 0.85,
    "cost_saved": 0.42,
    "start_time": 1711584000.0,
    "errors": 2,
    "cache_hits": 37
  },
  "compilation_mode": "hybrid",
  "vault_index": {
    "available": true,
    "blocks": 42
  },
  "router": { "enabled": true },
  "today": { ... },
  "by_model": {
    "claude-sonnet-4-6": {
      "requests": 100,
      "input_tokens": 250000,
      "cost": 0.60
    }
  },
  "recent": [ ... ]
}
```

---

#### `GET /stats/last`

Per-request stats for the most recent proxied request.

**Response:**
```json
{
  "request_id": "req_abc123",
  "timestamp": "2026-03-28T16:00:00Z",
  "model": "claude-sonnet-4-6",
  "tokens_saved": 1240,
  "percent_saved": 28.3,
  "cost_saved": 0.0037,
  "session_total_saved": 0.42,
  "session_requests": 142,
  "input_tokens_raw": 4380,
  "input_tokens_sent": 3140,
  "output_tokens": 512
}
```

**Error (no requests yet):**
```json
{
  "error": "no_requests",
  "message": "No requests captured yet. Send a message to see stats."
}
```

---

#### `GET /stats/session`

Session aggregate summary with uptime and average savings.

**Response:**
```json
{
  "session_requests": 142,
  "session_total_saved": 0.42,
  "tokens_saved": 170000,
  "tokens_sent": 210000,
  "tokens_raw": 380000,
  "output_tokens": 95000,
  "total_cost": 0.85,
  "uptime_hours": 4.5,
  "errors": 2,
  "avg_savings_pct": 44.7
}
```

---

#### `GET /savings[?since=<ISO-date>]`

Savings report, optionally filtered by start date.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `since` | ISO-8601 date string | Filter to requests on/after this date |

**Example:** `GET /savings?since=2026-03-01`

---

#### `GET /cache-stats`

Detailed cache hit/miss breakdown.

---

#### `GET /recent`

Last 50 requests with per-request metadata.

**Response:**
```json
{
  "recent": [
    {
      "timestamp": "2026-03-28T16:00:00Z",
      "model": "claude-sonnet-4-6",
      "input_tokens": 4380,
      "output_tokens": 512,
      "latency_ms": 320,
      "status_code": 200,
      "tokens_saved": 1240
    }
  ]
}
```

---

#### `GET /trace/last`

Full pipeline trace for the most recent request (debugging).

**Response:**
```json
{
  "request_id": "req_abc123",
  "timestamp": "2026-03-28T16:00:00Z",
  "stages": [
    { "name": "compaction", "duration_ms": 45, "tokens_before": 4380, "tokens_after": 3140 },
    { "name": "vault_inject", "duration_ms": 12, "blocks_injected": 2 },
    { "name": "upstream_forward", "duration_ms": 260, "provider": "anthropic" }
  ]
}
```

---

#### `GET /trace/<request_id>`

Pipeline trace for a specific request by ID.

---

#### `GET /traces`

All stored pipeline traces (up to last N requests).

**Response:**
```json
{
  "traces": [ ... ],
  "count": 10
}
```

---

#### `GET /vault`

Vault index debug info — lists all indexed blocks.

**Response:**
```json
{
  "available": true,
  "blocks": 42,
  "total_tokens": 185000,
  "path": "/home/user/vault/.tokenpak",
  "block_list": [
    {
      "block_id": "vault_001",
      "source_path": "04_KNOWLEDGE/concepts/tokenpak.md",
      "risk_class": "safe",
      "raw_tokens": 1240
    }
  ]
}
```

---

#### `GET /metrics`

Prometheus-compatible metrics in text format.

**Content-Type:** `text/plain; version=0.0.4; charset=utf-8`

**Example output:**
```
# HELP tokenpak_requests_total Total proxied requests
# TYPE tokenpak_requests_total counter
tokenpak_requests_total 142
tokenpak_tokens_input_total 380000
tokenpak_tokens_saved_total 170000
tokenpak_errors_total 2
tokenpak_uptime_seconds 16200
```

---

#### `GET /metrics/dashboard`

Comprehensive dashboard metrics with 8 key metrics in JSON format.

**Response:**
```json
{
  "timestamp": "2026-03-28T16:00:00Z",
  "uptime_seconds": 16200,
  "requests": {
    "total": 142,
    "throughput_req_per_sec": 0.009,
    "24h_window": true
  },
  "latency": {
    "p50_ms": 320.0,
    "p95_ms": 980.0,
    "p99_ms": 1840.0,
    "avg_ms": 415.0,
    "samples": 100
  },
  "models": {
    "claude-sonnet-4-6": { "requests": 100, "input_tokens": 250000, "cost": 0.60 }
  },
  "routing": { "smart_routing_hit_rate": 0.0 },
  "cache": {
    "hit_ratio": 0.42,
    "read_tokens": 85000,
    "creation_tokens": 118000
  },
  "errors": {
    "error_rate": 0.014,
    "error_count": 2,
    "top_failures": { "429": 1, "503": 1 }
  },
  "streaming": { "count": 0, "percentage": 0.0 },
  "window_24h": {
    "input_tokens": 380000,
    "output_tokens": 95000,
    "total_cost": 0.85
  }
}
```

---

#### `GET /dashboard` / `GET /dashboard/<path>`

Serves the built-in HTML monitoring dashboard.

---

#### `GET /docs` / `GET /docs/`

Serves the built-in API documentation page (HTML).

---

#### `GET /openapi.yaml`

OpenAPI 3.0 spec for the proxy HTTP API.

---

### POST Endpoints

#### `POST /v1/messages`

Anthropic Messages API — the primary proxy path for Claude models.

TokenPak intercepts this request, applies compression, and forwards to the upstream Anthropic API. The response is transparently passed back.

**Headers:**

| Header | Value | Required |
|--------|-------|----------|
| `Content-Type` | `application/json` | Yes |
| `x-api-key` | `<anthropic-api-key>` | Yes |
| `anthropic-version` | `2023-06-01` | Recommended |

**Request Body:**
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": "Explain quantum entanglement."
    }
  ],
  "system": "You are a helpful physics tutor.",
  "stream": false
}
```

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model ID (e.g. `claude-sonnet-4-6`) |
| `messages` | array | Yes | Conversation history — `role` + `content` pairs |
| `max_tokens` | integer | Yes | Maximum tokens in the response |
| `system` | string | No | System prompt |
| `stream` | boolean | No | Enable SSE streaming (default: false) |
| `temperature` | float | No | Sampling temperature (0.0–1.0) |
| `top_p` | float | No | Nucleus sampling threshold |
| `stop_sequences` | array | No | Custom stop strings |
| `tools` | array | No | Tool/function definitions |
| `tool_choice` | object | No | Tool selection policy |

**Response:**
```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [
    { "type": "text", "text": "Quantum entanglement is..." }
  ],
  "model": "claude-sonnet-4-6",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 3140,
    "output_tokens": 512,
    "cache_read_input_tokens": 1200,
    "cache_creation_input_tokens": 800
  }
}
```

---

#### `POST /v1/chat/completions`

OpenAI Chat Completions API — compatible path for OpenAI SDK clients, LangChain, and LiteLLM.

**Headers:**

| Header | Value | Required |
|--------|-------|----------|
| `Content-Type` | `application/json` | Yes |
| `Authorization` | `Bearer <api-key>` | Yes |

**Request Body:**
```json
{
  "model": "gpt-4o",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "Hello!" }
  ],
  "max_tokens": 1024,
  "stream": false
}
```

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model ID |
| `messages` | array | Yes | Message list with `role` and `content` |
| `max_tokens` | integer | No | Maximum response tokens |
| `stream` | boolean | No | Enable SSE streaming |
| `temperature` | float | No | Sampling temperature |
| `functions` | array | No | Function/tool definitions (legacy) |
| `tools` | array | No | Tool definitions |

---

#### `POST /ingest` / `POST /ingest/batch`

Ingest context blocks directly into the vault index at runtime.

**Request Body:**
```json
{
  "block_id": "my-context-001",
  "content": "This is important context to inject...",
  "source_path": "custom/context.md",
  "risk_class": "safe"
}
```

**Batch variant** (`/ingest/batch`) accepts an array of blocks.

---

#### `POST /config/reload`

Hot-reload configuration from environment variables (localhost only).

Equivalent to sending `SIGHUP` to the proxy process.

**Response:**
```json
{
  "status": "ok",
  "message": "Config reloaded: TOKENPAK_MODE=hybrid, TOKENPAK_COMPACT=1"
}
```

**Note:** Only accepts requests from `127.0.0.1` or `::1`. Remote calls receive `403 Forbidden`.

---

### Error Format

All error responses use a consistent JSON structure:

```json
{
  "error": {
    "type": "error_type",
    "message": "Human-readable description"
  }
}
```

**Common error types:**

| HTTP Status | `error.type` | Description |
|-------------|-------------|-------------|
| 400 | `bad_request` | Malformed request body |
| 401 | `unauthorized` | Missing or invalid `X-TokenPak-Key` |
| 403 | `forbidden` | Operation not allowed from this IP |
| 404 | `not_found` | Unknown endpoint path |
| 405 | `method_not_allowed` | Wrong HTTP method (e.g. GET on POST-only path) |
| 429 | `rate_limit_exceeded` | Too many requests from this IP |
| 500 | `internal_error` | Proxy-side error |
| 503 | `circuit_open` | Upstream provider circuit breaker open |
| 503 | `upstream_unreachable` | Cannot reach upstream provider |

---

## SDK Adapters

TokenPak provides adapters that route requests through the proxy while preserving the native API shape of each SDK.

### Base Adapter (`TokenPakAdapter`)

All adapters inherit from `TokenPakAdapter` and implement four lifecycle hooks.

```python
from tokenpak.adapters.base import TokenPakAdapter
```

**Constructor Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `base_url` | str | Yes | — | Proxy URL, e.g. `http://127.0.0.1:8766` |
| `api_key` | str | Yes | — | Provider API key (forwarded to upstream) |
| `timeout_s` | float | No | `120.0` | Request timeout in seconds |

**Lifecycle Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `prepare_request` | `(request: dict) -> dict` | Validate and normalise request |
| `send` | `(prepared: dict) -> dict` | POST to proxy, return raw response |
| `parse_response` | `(response: dict) -> dict` | Convert to SDK-native format |
| `extract_tokens` | `(response: dict) -> dict` | Extract `{input, output, cache_read, cache_creation}` token counts |

**High-level call method:**

```python
# Convenience: calls prepare_request → send → parse_response
response = adapter.call(request_dict)

# Extract token usage
tokens = adapter.extract_tokens(response)
# tokens = {"input_tokens": 3140, "output_tokens": 512, ...}
```

---

### AnthropicAdapter

Routes requests to `/v1/messages` on the proxy.

```python
from tokenpak.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    base_url="http://127.0.0.1:8766",
    api_key="sk-ant-api03-...",
)

response = adapter.call({
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [
        {"role": "user", "content": "What is 2 + 2?"}
    ],
})

print(response["content"][0]["text"])
tokens = adapter.extract_tokens(response)
print(f"Input tokens: {tokens['input_tokens']}")
```

**Proxy Path:** `POST /v1/messages`

**Required Request Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Claude model ID |
| `messages` | list | Non-empty list of `{role, content}` dicts |
| `max_tokens` | integer | Maximum completion tokens |

**Added Defaults (if not present):**
- `stream` defaults to `false`

**Extra Headers Sent:**
- `anthropic-version: 2023-06-01`

**`extract_tokens` Return:**
```python
{
  "input_tokens": 3140,
  "output_tokens": 512,
  "cache_read_input_tokens": 1200,
  "cache_creation_input_tokens": 800
}
```

---

### OpenAIAdapter

Routes requests to `/v1/chat/completions` on the proxy.

```python
from tokenpak.adapters import OpenAIAdapter

adapter = OpenAIAdapter(
    base_url="http://127.0.0.1:8766",
    api_key="sk-...",
)

response = adapter.call({
    "model": "gpt-4o",
    "messages": [
        {"role": "user", "content": "Hello, world!"}
    ],
})
```

**Proxy Path:** `POST /v1/chat/completions`

**Required Request Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | OpenAI model ID |
| `messages` | list | Non-empty list of `{role, content}` dicts |

---

### LangChainAdapter

Drop-in adapter for LangChain integrations.

```python
from tokenpak.adapters import LangChainAdapter

adapter = LangChainAdapter(
    base_url="http://127.0.0.1:8766",
    api_key="sk-ant-...",
)
```

**Constructor Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `base_url` | str | Yes | — | Proxy URL |
| `api_key` | str | Yes | — | Provider API key |
| `timeout_s` | float | No | `120.0` | Request timeout |

---

### LiteLLMAdapter

Drop-in adapter for LiteLLM integrations.

```python
from tokenpak.adapters import LiteLLMAdapter

adapter = LiteLLMAdapter(
    base_url="http://127.0.0.1:8766",
    api_key="sk-ant-...",
)
```

**Constructor Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `base_url` | str | Yes | — | Proxy URL |
| `api_key` | str | Yes | — | Provider API key |
| `timeout_s` | float | No | `120.0` | Request timeout |

---

### Exception Hierarchy

All adapters raise canonical exceptions — never raw `requests` exceptions.

```
TokenPakAdapterError (base)
├── TokenPakTimeoutError      — proxy did not respond within timeout_s
├── TokenPakConfigError       — missing required fields / bad config
└── TokenPakAuthError         — 401 or 403 from proxy
```

**Usage:**
```python
from tokenpak.adapters.base import (
    TokenPakAdapterError,
    TokenPakTimeoutError,
    TokenPakAuthError,
    TokenPakConfigError,
)

try:
    response = adapter.call(request)
except TokenPakTimeoutError:
    print("Proxy timed out")
except TokenPakAuthError as e:
    print(f"Auth failed: {e} (HTTP {e.status_code})")
except TokenPakConfigError as e:
    print(f"Config error: {e}")
except TokenPakAdapterError as e:
    print(f"Adapter error: {e} (HTTP {e.status_code})")
```

---

## CLI Commands

All commands are invoked as `tokenpak <command> [options]`.

### Proxy Lifecycle

#### `tokenpak start`

Start the proxy (default: `localhost:8766`).

```bash
tokenpak start                    # Start on default port 8766
tokenpak start --port 9000        # Custom port
tokenpak start --debug            # Verbose logging
tokenpak start --background       # Run as background daemon
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port` | int | `8766` | Port to listen on |
| `--debug` | flag | off | Enable debug logging |
| `--background` | flag | off | Daemonize the process |

---

#### `tokenpak stop`

Stop the running proxy process.

```bash
tokenpak stop
```

---

#### `tokenpak restart`

Restart the proxy (stop + start).

```bash
tokenpak restart
```

---

#### `tokenpak logs`

Show recent proxy log output.

```bash
tokenpak logs                # Last 50 lines
tokenpak logs -n 100         # Last 100 lines
tokenpak logs --follow       # Stream new log lines (tail -f)
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-n`, `--lines` | int | `50` | Number of log lines to show |

---

#### `tokenpak status`

Show system status and recent retry events.

```bash
tokenpak status
```

---

#### `tokenpak version`

Show current versions (proxy, config, CLI).

```bash
tokenpak version
```

---

#### `tokenpak update`

Update TokenPak to latest version from git/PyPI.

```bash
tokenpak update
```

---

### Indexing & Search

#### `tokenpak index [directory]`

Index a directory for vault-based context injection.

```bash
tokenpak index ~/vault           # Index the vault
tokenpak index ~/vault --watch   # Watch and auto-reindex on changes
tokenpak index --status          # Show indexed file count by type
tokenpak index -w 8              # Use 8 parallel workers
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `directory` | path | — | Directory to index (positional) |
| `--status` | flag | off | Show indexed file count by type |
| `--workers`, `-w` | int | `4` | Parallel indexing workers |
| `--watch` | flag | off | Watch for file changes and auto-reindex |
| `--recalibrate` | flag | off | Run worker calibration before indexing |
| `--max-workers` | int | `8` | Worker cap for auto-calibration |

---

#### `tokenpak search <query>`

Search the indexed vault content using BM25.

```bash
tokenpak search "compression budget"
tokenpak search "rate limits" --top 10
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `query` | string | — | Search query (positional) |

---

#### `tokenpak calibrate`

Calibrate the optimal worker count for parallel indexing on this host.

```bash
tokenpak calibrate
```

---

### Monitoring & Stats

#### `tokenpak stats`

Show registry statistics (request counts, token usage, cost breakdown).

```bash
tokenpak stats
tokenpak stats --raw             # JSON output
```

---

#### `tokenpak models`

Show per-model usage and efficiency breakdown.

```bash
tokenpak models                      # Summary table
tokenpak models --detail sonnet      # Detailed view for models matching "sonnet"
tokenpak models --raw                # JSON output
```

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--detail` | string | Show details for a specific model (partial match) |
| `--raw` | flag | Output as JSON |

---

#### `tokenpak savings[?since=<date>]`

Show savings summary — tokens and cost saved by compression.

```bash
tokenpak savings
tokenpak savings --since 2026-03-01
```

---

#### `tokenpak usage`

Show model usage summary.

```bash
tokenpak usage
```

---

#### `tokenpak compare`

Show before/after cost comparison for the last proxied request.

```bash
tokenpak compare
```

---

#### `tokenpak leaderboard`

Show per-model efficiency ranking (savings rate, cost per token).

```bash
tokenpak leaderboard
```

---

#### `tokenpak report`

Generate a daily savings report.

```bash
tokenpak report
```

---

#### `tokenpak requests`

Live request explorer — browse recent proxied requests interactively.

```bash
tokenpak requests
```

---

#### `tokenpak timeline`

View savings trend over the last 7 or 30 days.

```bash
tokenpak timeline
```

---

#### `tokenpak attribution`

View savings broken down by agent, skill, and model.

```bash
tokenpak attribution
```

---

#### `tokenpak aggregate`

Aggregate request ledger data across multiple machines.

```bash
tokenpak aggregate
```

---

#### `tokenpak monitor`

Start the live monitor dashboard on port 8767.

```bash
tokenpak monitor
tokenpak monitor --port 8768     # Custom port
```

---

#### `tokenpak dashboard`

Real-time health dashboard (TUI) or serve the public web dashboard URL.

```bash
tokenpak dashboard               # TUI view
tokenpak dashboard --public      # Open web dashboard in browser
```

---

#### `tokenpak check-alerts`

Evaluate alert rules and report any health violations.

```bash
tokenpak check-alerts
```

---

### Diagnostics

#### `tokenpak doctor`

Run comprehensive system diagnostics.

```bash
tokenpak doctor
```

Checks:
- Proxy connectivity (port 8766)
- Upstream provider reachability
- API key validity
- Vault index health
- Config file validity

---

#### `tokenpak preview <file>`

Preview compression dry-run on a file — shows token savings before sending to API.

```bash
tokenpak preview prompt.txt
tokenpak preview --mode aggressive prompt.txt
```

---

#### `tokenpak debug on|off|status`

Toggle verbose debug logging or check current debug state.

```bash
tokenpak debug on
tokenpak debug off
tokenpak debug status
```

---

#### `tokenpak learn status`

Show learned compression patterns from telemetry.

```bash
tokenpak learn status
```

#### `tokenpak learn reset`

Clear all learned data and reset to baseline.

```bash
tokenpak learn reset
```

---

#### `tokenpak replay`

List, inspect, and re-run captured sessions (zero API cost).

```bash
tokenpak replay list             # List recent captured sessions
tokenpak replay show <id>        # Show full details
tokenpak replay run <id>         # Re-run with different settings
tokenpak replay clear            # Remove all entries
```

---

#### `tokenpak validate <file>`

Validate a TokenPak JSON file against the v1.0 schema.

```bash
tokenpak validate my-config.json
```

---

#### `tokenpak diff`

Show context changes (removed/compressed/retained blocks) for a request.

```bash
tokenpak diff
```

---

#### `tokenpak vault-health`

Vault index health diagnostic and repair.

```bash
tokenpak vault-health            # Check index health
tokenpak vault-health repair     # Rebuild stale vault index
```

---

### Config Management

#### `tokenpak setup`

Interactive first-time configuration wizard.

```bash
tokenpak setup
```

---

#### `tokenpak config`

Config management subcommands.

```bash
tokenpak config show             # Show merged config (file + env overrides)
tokenpak config sync             # Sync config from canonical source
tokenpak config pull             # Pull config from git or URL
tokenpak config validate         # Validate config against schema
tokenpak config init             # Create default config.yaml
tokenpak config path             # Print config file path
```

---

#### `tokenpak route`

Manage manual model routing rules.

```bash
tokenpak route list              # List routing rules
tokenpak route add               # Add a rule
tokenpak route remove <id>       # Remove a rule
```

---

### Advanced Commands

#### `tokenpak serve`

Start monitoring proxy or telemetry ingest server.

```bash
tokenpak serve                   # Standard proxy
tokenpak serve --telemetry       # Telemetry ingest server
tokenpak serve --ingest          # Phase 5A ingest API server
tokenpak serve --workers 2       # Multiple uvicorn workers
```

---

#### `tokenpak benchmark`

Benchmark compression performance.

```bash
tokenpak benchmark               # Built-in sample data
tokenpak benchmark --file prompt.txt
tokenpak benchmark --latency ~/vault   # Latency/indexing benchmark
tokenpak benchmark --json        # JSON output
```

---

#### `tokenpak macro`

Manage and run compression macros.

```bash
tokenpak macro list              # List all macros
tokenpak macro run <name>        # Run a macro
tokenpak macro create            # Create a user-defined YAML macro
tokenpak macro show <name>       # Show macro definition
tokenpak macro delete <name>     # Delete a user-defined macro
```

---

#### `tokenpak recipe`

Manage compression recipes (YAML workflow definitions).

```bash
tokenpak recipe create           # Scaffold a new recipe YAML
tokenpak recipe validate <file>  # Validate recipe against schema
tokenpak recipe test <file>      # Test recipe against sample input
tokenpak recipe benchmark <file> # Benchmark recipe performance
```

---

#### `tokenpak fleet`

Manage and query a multi-machine proxy fleet.

```bash
tokenpak fleet init              # Configure fleet interactively
tokenpak fleet status            # Show fleet health
tokenpak fleet list              # List fleet members
```

---

#### `tokenpak template`

Manage local user prompt templates.

```bash
tokenpak template list
tokenpak template add <name>     # Add or update a template
tokenpak template show <name>    # Display a template
tokenpak template remove <name>  # Delete a template
tokenpak template use <name>     # Expand a template with variables
```

---

#### `tokenpak audit`

Enterprise audit log management.

```bash
tokenpak audit list              # List audit log entries
tokenpak audit export            # Export to file
tokenpak audit verify            # Verify hash chain integrity
tokenpak audit prune             # Remove old entries
tokenpak audit summary           # Show audit stats
```

---

## Configuration Reference

### Environment Variables

The proxy reads configuration from `~/.tokenpak/config.yaml` with environment variable overrides. Environment variables always take precedence.

#### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_MODE` | `hybrid` | Compression mode: `strict`, `hybrid`, `aggressive` |
| `TOKENPAK_COMPACT` | `1` | Master on/off switch (0 = disable all compression) |
| `TOKENPAK_DB` | `.tokenpak/monitor.db` | SQLite database path |

#### Compression Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_COMPACT_MAX_CHARS` | `120` | Maximum chars for compressed text chunks |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | Skip compression below this token count |
| `TOKENPAK_COMPACT_CACHE_SIZE` | `2000` | Compression result cache entries |
| `TOKENPAK_MAX_COMPRESSION_TIME_MS` | `5000` | Max compression time before skipping (0 = no cap) |

#### Vault Context Injection

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_VAULT_INDEX` | `~/vault/.tokenpak` | Path to vault index directory |
| `TOKENPAK_INJECT_BUDGET` | `4000` | Max tokens to inject from vault per request |
| `TOKENPAK_INJECT_TOP_K` | `5` | Max vault blocks to inject per request |
| `TOKENPAK_INJECT_MIN_SCORE` | `2.0` | Minimum BM25 score to include a block |
| `TOKENPAK_RETRIEVAL_BACKEND` | `json_blocks` | Vault backend: `json_blocks` or `sqlite` |

#### Key Management

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Primary Anthropic API key |
| `ANTHROPIC_OAUTH_TOKEN` | — | Rotation key 2 |
| `ANTHROPIC_OAUTH_TOKEN2` | — | Rotation key 3 |
| `TOKENPAK_KEY_ROTATION` | `failover` | Key rotation mode: `failover` or `roundrobin` |
| `TOKENPAK_KEY_COOLDOWN_429` | `60` | Rate-limit cooldown seconds |
| `TOKENPAK_KEY_COOLDOWN_401` | `300` | Invalid-key cooldown seconds |

#### Advanced Features

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_CAPSULE_BUILDER` | `0` | Enable capsule builder stage (`0` or `1`) |
| `TOKENPAK_CAPSULE_MIN_CHARS` | `400` | Min chars for a block to be capsulised |
| `TOKENPAK_ROUTER_ENABLED` | `true` | Enable smart model router |
| `TOKENPAK_HTTP100_KEEPALIVE` | `0` | Send HTTP 100 Continue before compression |

---

### config.yaml

Default location: `~/.tokenpak/config.yaml`

```yaml
# TokenPak configuration
# All settings can also be overridden via environment variables

compression:
  enabled: true
  mode: hybrid               # strict | hybrid | aggressive
  max_chars: 120             # Max chars per compressed chunk
  threshold_tokens: 4500     # Skip compression below this token count

cache:
  enabled: true
  type: memory               # memory | disk
  ttl_seconds: 3600
  max_size_mb: 256

logging:
  enabled: true
  level: info                # debug | info | warning | error
  destination: file          # file | stdout
  retention_days: 30
  include_request_body: false
  include_response_body: false

metrics:
  enabled: true
  collection_window_seconds: 60
  retention_days: 7

security:
  require_api_key: false
  api_key: null              # X-TokenPak-Key for non-localhost clients
  allowed_origins:
    - "*"
  rate_limit_per_minute: 1000

advanced:
  vault_index_path: "~/.tokenpak/.index"
  enable_trace_logs: false
  proxy_timeout_seconds: 30
  max_request_size_bytes: 10485760    # 10 MB
```

---

*This reference covers TokenPak v0.5.0. For the full changelog, see [CHANGELOG.md](https://github.com/tokenpak/tokenpak/blob/main/CHANGELOG.md).*
