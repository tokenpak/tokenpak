# Embedding API Reference

**POST /v1/embeddings — universal embedding proxy with automatic failover.**

---

## Endpoint

```
POST /v1/embeddings
```

OpenAI-compatible request/response format. Drop in place of `https://api.openai.com/v1/embeddings`.

---

## Request

```json
{
 "input": "text to embed",
 "model": "voyage-3-large",
 "dimensions": 1024
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input` | string or array | Yes | Text(s) to embed |
| `model` | string | No | Provider-specific model name. If omitted, the selected provider uses its default model. |
| `dimensions` | integer | No | Requested output dimensions. See [Dimension negotiation](#dimension-negotiation). |

---

## Response

Standard OpenAI-compatible response, with an additional `_tokenpak` metadata block injected at the top level:

```json
{
 "object": "list",
 "data": [
 {
 "object": "embedding",
 "index": 0,
 "embedding": [0.123, -0.456, "..."]
 }
 ],
 "model": "voyage-3-large",
 "usage": {
 "prompt_tokens": 8,
 "total_tokens": 8
 },
 "_tokenpak": {
 "provider": "voyage",
 "upstream_model": "voyage-3-large",
 "latency_ms": 142.3,
 "cached": false,
 "fallback_used": false
 }
}
```

The `_tokenpak` block is always present on success and is safe to ignore.

---

## Supported Providers

| Provider | Default upstream URL | Cost / 1M tokens |
|----------|----------------------|-------------------|
| `voyage` | `https://api.voyageai.com` | $0.06 |
| `openai` | `https://api.openai.com` | $0.02 |
| `cohere` | `https://api.cohere.com` | $0.10 |

Provider order is tried left-to-right (default: `voyage → openai → cohere`).

---

## Dimension Negotiation

Pass `dimensions` in the request body. The value is forwarded to the upstream provider unchanged. Providers that do not support dynamic dimensions silently ignore the field and return their native embedding size. No server-side truncation or padding is applied.

---

## Failover Chain

Providers are tried in priority order. On failure:

| Upstream status | Behavior |
|-----------------|----------|
| `401` / `403` | Provider enters cooldown for `TOKENPAK_EMBEDDING_KEY_COOLDOWN` seconds (default 300). Next provider tried immediately. |
| `429` | Provider enters cooldown for the duration in the `Retry-After` header, or `TOKENPAK_EMBEDDING_RETRY_429` seconds (default 60) if the header is absent. Next provider tried immediately. |
| `5xx` | One automatic retry against the same provider, then fall through to the next. |
| Network error | Fall through to the next provider immediately. |

When every provider in the chain is in cooldown or has been exhausted, the proxy returns `503`:

```json
{
 "error": {
 "type": "all_providers_in_cooldown",
 "message": "All embedding providers are temporarily unavailable.",
 "providers": {
 "voyage": { "cooldown_until": 1712700000.0, "seconds_remaining": 247.3, "last_error_code": 429 }
 }
 }
}
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_EMBEDDING_PROVIDERS` | `voyage,openai,cohere` | Comma-separated ordered provider list |
| `TOKENPAK_EMBEDDING_KEY_COOLDOWN` | `300` | Seconds to cool down a provider after 401/403 |
| `TOKENPAK_EMBEDDING_RETRY_429` | `60` | Fallback cooldown seconds for 429 when no `Retry-After` header |
| `TOKENPAK_EMBEDDING_VOYAGE_URL` | `https://api.voyageai.com` | Override upstream URL for Voyage |
| `TOKENPAK_EMBEDDING_OPENAI_URL` | `https://api.openai.com` | Override upstream URL for OpenAI |
| `TOKENPAK_EMBEDDING_COHERE_URL` | `https://api.cohere.com` | Override upstream URL for Cohere |

---

## Examples

### Embed a single string

```bash
curl http://localhost:8766/v1/embeddings \
 -H "Content-Type: application/json" \
 -d '{"input": "The quick brown fox", "model": "voyage-3-large"}'
```

### Embed a batch

```bash
curl http://localhost:8766/v1/embeddings \
 -H "Content-Type: application/json" \
 -d '{
 "input": ["First document", "Second document"],
 "model": "text-embedding-3-small"
 }'
```

### Request specific dimensions

```bash
curl http://localhost:8766/v1/embeddings \
 -H "Content-Type: application/json" \
 -d '{"input": "example", "model": "text-embedding-3-large", "dimensions": 256}'
```

### Override provider order at runtime

```bash
TOKENPAK_EMBEDDING_PROVIDERS=openai,voyage tokenpak start
```
