# TokenPak API Reference

> **Source of truth** — matches actual proxy_v4.py endpoints.
> API runs on the proxy port (default **8766**).

Base URL: `http://localhost:8766`

---

## Authentication

Most endpoints require no authentication for local use.

The dashboard can be protected with an admin token (configure `dashboard.require_token` in config or set `TOKENPAK_DASHBOARD_AUTH=true`). Set the token via `X-Admin-Token` header when required.

---

## Core LLM Proxy

### `POST /v1/*`

Route an LLM request through TokenPak. Supports OpenAI-compatible and Anthropic-compatible clients.

```bash
curl -X POST http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -d '{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"Hello"}],"max_tokens":100}'
```

TokenPak auto-detects the provider (Anthropic, OpenAI, Google) based on path, headers, and request body. The response is the standard provider response — no schema changes.

---

## Health & Status

### `GET /health`

Liveness check. Returns system status, module availability, and session stats.

```bash
curl http://localhost:8766/health
```

**Response:**
```json
{
  "status": "ok",
  "compilation_mode": "full",
  "vault_index": { "available": true, "blocks": 42, "path": "/..." },
  "router": { "enabled": true },
  "capsule_available": true,
  "canon": { "enabled": true, "session_hits": 12 },
  "skeleton": { "enabled": false },
  "budget": { "enabled": true, "total_tokens": 200000 },
  "strict_validation": true,
  "upstream_timeout_seconds": 60,
  "circuit_breakers": {
    "anthropic": { "open": false, "failures": 0 }
  },
  "stats": { "requests": 47, "tokens_saved": 12400 }
}
```

---

### `GET /stats`

Session statistics and cost tracking.

```bash
curl http://localhost:8766/stats
```

**Response:**
```json
{
  "session": { "requests": 47, "tokens_saved": 12400, "cost_usd": 0.08 },
  "compilation_mode": "full",
  "vault_index": { "available": true, "blocks": 42 },
  "router": { "enabled": true },
  "today": { "requests": 47, "cost_usd": 0.08 },
  "by_model": { "claude-haiku-4-5": { "requests": 30, "cost": 0.04 } },
  "recent": [ ... ]
}
```

---

### `GET /stats/last`

Per-request stats for the most recent request.

```bash
curl http://localhost:8766/stats/last
```

---

### `GET /stats/session`

Current session-level stats (tokens in, tokens out, savings).

```bash
curl http://localhost:8766/stats/session
```

---

### `GET /cache-stats`

Cache performance metrics (hit rate, miss count, TTL info).

```bash
curl http://localhost:8766/cache-stats
```

---

### `GET /recent`

Last 50 requests with timing, model, token counts, and cost.

```bash
curl http://localhost:8766/recent
```

---

## Data Ingest

### `POST /ingest`

Ingest a single request record into TokenPak's telemetry.

```bash
curl -X POST http://localhost:8766/ingest \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5","tokens":1500,"cost":0.002}'
```

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Model name (e.g. `claude-haiku-4-5`) |
| `tokens` | int ≥ 0 | Token count for this request |
| `cost` | float ≥ 0 | Cost in USD |

**Optional fields:** `timestamp` (ISO 8601), `agent`, `session_id`, `tokens_saved`, `compression_rate`

---

### `POST /ingest/batch`

Ingest multiple records at once.

```bash
curl -X POST http://localhost:8766/ingest/batch \
  -H "Content-Type: application/json" \
  -d '[{"model":"claude-haiku-4-5","tokens":1500,"cost":0.002}, ...]'
```

---

## Dashboard

### `GET /dashboard`

Opens the TokenPak web dashboard (HTML UI). Accessible in your browser at:

```
http://localhost:8766/dashboard
```

Shows token savings, cost trends, model usage, and session history.

---

## Notes

- The proxy passes requests to the upstream LLM provider unchanged (after compression/routing). Response schema is the provider's native format.
- `/v1/*` supports both streaming (SSE) and non-streaming requests.
- `/v1beta/*` is also supported (for Google Gemini compatibility).
- Endpoints not listed here are internal or deprecated — do not rely on them.
