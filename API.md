# TokenPak API Reference

TokenPak exposes two HTTP interfaces: the **Proxy** (OpenAI-compatible) and the optional **Telemetry Server**.

---

## Proxy API (Port 8766)

The proxy is a drop-in replacement for any OpenAI-compatible endpoint.

### Base URL

```
http://localhost:8766
```

### Authentication

Pass your provider API key exactly as you would to the original provider:

```bash
# OpenAI
Authorization: Bearer sk-...

# Anthropic
x-api-key: sk-ant-...
```

TokenPak passes credentials through and never stores them.

---

### POST /v1/chat/completions

OpenAI-compatible chat completions endpoint. Supports streaming.

**Request body:** Identical to the [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)

**Response:** Same format as the upstream provider, with an optional `x-tokenpak-stats` response header:

```
x-tokenpak-stats: compressed=true, original_tokens=4200, compressed_tokens=2100, savings_pct=50.0
```

**Example:**

```bash
curl http://localhost:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**Streaming example:**

```bash
curl -N http://localhost:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-4o",
    "stream": true,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

When `"stream": true` is set:
- Response is `Content-Type: text/event-stream` (SSE)
- Chunks are forwarded immediately with no buffering
- `Cache-Control: no-cache` and `X-Accel-Buffering: no` are enforced
- Output token telemetry is captured from the stream's usage events

---

### POST /v1/messages

Anthropic-compatible messages endpoint. Pass through to Anthropic API.

```bash
curl http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-4-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**Streaming example:**

```bash
curl -N http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-4-5",
    "max_tokens": 1024,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

SSE events are forwarded verbatim from Anthropic: `message_start`, `content_block_delta`, `message_delta` (with output token count), and `message_stop`. Output and cache token telemetry is captured from the stream automatically.

---

### POST /ingest

Ingest custom telemetry entries into the proxy's metrics database.

**Request body (single entry):**

```json
{
  "model": "claude-opus-4-5",
  "tokens": 5000,
  "cost": 0.25,
  "timestamp": "2026-03-10T16:03:00Z"
}
```

**Required fields:**
- `model` (string): Model identifier
- `tokens` (integer): Total tokens used (≥ 0)
- `cost` (number): Estimated cost in USD (≥ 0)

**Optional fields:**
- `timestamp` (ISO 8601 string): Entry time; defaults to current UTC if omitted

**Response:**

```json
{
  "status": "ok",
  "ids": ["entry_20260310_abc123"]
}
```

**Error responses:**
- `400 Bad Request`: Missing required fields, invalid JSON, or invalid field types
- `413 Payload Too Large`: Request body exceeds 1MB
- `422 Unprocessable Entity`: Field validation failed (invalid timestamp format, negative values)
- `500 Internal Server Error`: Storage write failure

---

### POST /ingest/batch

Bulk ingest multiple telemetry entries in a single request.

**Request body:**

```json
{
  "events": [
    {
      "model": "claude-opus-4-5",
      "tokens": 5000,
      "cost": 0.25,
      "timestamp": "2026-03-10T16:03:00Z"
    },
    {
      "model": "gpt-4o",
      "tokens": 3000,
      "cost": 0.15
    }
  ]
}
```

**Constraints:**
- `events` must be a list of 1–1000 entries
- Each entry uses the same validation as `/ingest`
- Batch processing is atomic: on error, the entire batch is rejected

**Response:**

```json
{
  "status": "ok",
  "ids": ["entry_20260310_abc123", "entry_20260310_def456"]
}
```

**Error responses:**
- `400 Bad Request`: Missing events field, empty list, or non-dict entries
- `413 Payload Too Large`: Request body exceeds 1MB
- `422 Unprocessable Entity`: One or more entries fail validation
- `500 Internal Server Error`: Storage write failure

---

### GET /health

Proxy health check.

**Response:**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "uptime_seconds": 3600
}
```

---

### GET /stats

Current session compression statistics.

**Response:**

```json
{
  "session_id": "abc123",
  "requests_total": 42,
  "tokens_original": 180000,
  "tokens_compressed": 95000,
  "savings_pct": 47.2,
  "cost_saved_usd": 1.23
}
```

---

## Telemetry Server API (Port 8767)

Optional. Start with `tokenpak telemetry serve`.

### GET /api/sessions

List recent sessions.

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `limit` | int | Max results (default: 50) |
| `since` | ISO datetime | Filter sessions after this time |
| `model` | string | Filter by model name |

---

### GET /api/sessions/{session_id}

Get a single session's details.

---

### GET /api/cost/summary

Aggregated cost breakdown.

**Response:**

```json
{
  "total_cost_usd": 42.10,
  "total_tokens": 8200000,
  "total_savings_usd": 19.85,
  "savings_pct": 47.1,
  "by_model": {
    "gpt-4o": {"cost_usd": 28.50, "tokens": 5200000},
    "claude-opus-4-5": {"cost_usd": 13.60, "tokens": 3000000}
  }
}
```

---

### GET /api/cost/export

Export cost data as CSV.

**Query params:** Same as `/api/sessions`.

---

## Python SDK

### Basic usage

```python
from tokenpak import compress

result = compress("Your long context here...", budget=4000)
print(result.text)
print(f"Savings: {result.savings_pct:.1f}%")
```

### Recipe SDK

```python
from tokenpak.agent.recipe_sdk import RecipeSDK

sdk = RecipeSDK()
sdk.load_recipe("recipes/oss/py-docstring-to-signature.yaml")
compressed = sdk.apply("def foo():\n    \"\"\"Long docstring...\"\"\"\n    pass")
```

### Routing rules

```python
from tokenpak.routing.rules import RouteRule, RouteEngine

engine = RouteEngine()
engine.add_rule(RouteRule(
    name="small-to-haiku",
    condition=lambda req: req.token_count < 1000,
    target_model="claude-haiku-4-5"
))
```

---

## CLI Reference

See [docs/cli-reference.md](docs/cli-reference.md) for the full CLI command listing.

```bash
tokenpak --help
tokenpak serve --help
tokenpak cost --help
tokenpak route --help
```
