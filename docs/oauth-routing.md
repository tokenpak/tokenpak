# OAuth Routing in TokenPak Proxy

TokenPak supports two authentication flavors for LLM providers: **API keys** and **OAuth Bearer tokens** (subscription-based). This document covers how the proxy detects and routes each type, with focus on Codex (subscription OAuth) and Claude Code (OAuth session).

---

## Auth Type Detection

The proxy inspects the `Authorization` header to determine auth type before routing:

| Header Pattern | Auth Type | Examples |
|---|---|---|
| `x-api-key: <value>` | `apikey` | Anthropic API key |
| `Authorization: Bearer sk-...` | `apikey` | OpenAI / Anthropic API key |
| `Authorization: Bearer <non-sk>` | `oauth` | Codex subscription, Claude Code |
| _(none)_ | `none` | Unauthenticated (rejected) |

**Rule of thumb:** If your Bearer token starts with `sk-`, it's an API key. All other Bearer tokens are treated as OAuth.

---

## Provider Routing

### Codex (OpenAI Subscription OAuth)

**Endpoint:** `https://api.openai.com/v1/responses` (Responses API)

Codex subscription models (`gpt-5.1-codex-mini`, `gpt-5.2-codex`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`) use OAuth Bearer tokens — not API keys. The proxy detects Codex via:

1. **Path:** requests to `/v1/responses` → routed to `openai-codex`
2. **Model name:** body contains `"model": "gpt-5.x-codex..."` → identified as Codex

**Example: Codex via proxy**
```bash
# Point your Codex client at the TokenPak proxy:
export OPENAI_BASE_URL=http://localhost:8766

# Codex authenticates with OAuth Bearer token (from Codex CLI / subscription)
# The proxy forwards it unchanged to api.openai.com/v1/responses
```

**Using the Codex CLI with TokenPak:**
```bash
# Set proxy as base URL (Codex CLI / SDK)
OPENAI_BASE_URL=http://localhost:8766 codex "explain this code"
```

### Claude Code (Anthropic Subscription OAuth)

**Endpoint:** `https://api.anthropic.com/v1/messages`

Claude Code subscription users authenticate with an OAuth Bearer token instead of an API key. The proxy detects this when:
- Path is `/v1/messages` AND
- `Authorization: Bearer <non-sk-token>` is present

The provider is still `anthropic` — same endpoint, different auth mechanism.

**Example: Claude Code via proxy**
```bash
# Set proxy (Claude Code CLI reads ANTHROPIC_BASE_URL or custom config)
export ANTHROPIC_BASE_URL=http://localhost:8766

# Claude Code's OAuth session token is forwarded unchanged
claude "review this PR"
```

### Standard API Key (Anthropic + OpenAI)

```bash
# Anthropic API key
curl http://localhost:8766/v1/messages \
  -H "x-api-key: sk-ant-..." \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-6","messages":[...]}'

# OpenAI API key  
curl http://localhost:8766/v1/chat/completions \
  -H "Authorization: Bearer sk-openai..." \
  -d '{"model":"gpt-4o","messages":[...]}'
```

---

## Auth Flow Diagrams

### API Key Flow (Static)
```
Client ──► Proxy ──► Provider
           │
           ├─ Validate Bearer sk-... or x-api-key present
           ├─ Detect provider from path/body
           ├─ Forward auth header unchanged (ZERO storage)
           └─ Cache keying: ENABLED (key prefix stable)
```

### OAuth Bearer Flow (Session)
```
Client ──► Proxy ──► Provider
           │
           ├─ Detect Bearer <non-sk> → auth_type=oauth
           ├─ Detect provider:
           │    /v1/responses           → openai-codex
           │    /v1/messages + non-sk   → anthropic (Claude Code OAuth)
           │    /v1/chat/completions    → openai
           ├─ Forward OAuth token unchanged (ZERO storage, ZERO logging)
           └─ Cache keying: DISABLED (OAuth tokens may expire mid-session)
```

---

## OAuth-Specific Behaviors

### Cache Keying Disabled

OAuth tokens may expire during a session. To prevent stale cached responses from being served after token refresh, the proxy sets `skip_cache_keying=True` for all OAuth requests.

This means:
- OAuth requests are still forwarded through compression/cost-tracking
- Responses are NOT shared across OAuth sessions
- Each OAuth session gets fresh upstream responses

### Token Security

The OAuth module upholds the same zero-storage guarantees as the passthrough module:
- **Zero logging** of token values (even at DEBUG level)
- **Zero storage** of tokens on disk or in memory beyond request lifetime
- **Telemetry tags** contain format hints only (`jwt`, `opaque`, `apikey`) — never token values

### Token Expiry

The proxy does **not** attempt OAuth token refresh. If an OAuth token expires mid-session:
1. The upstream provider returns HTTP 401
2. The proxy forwards the 401 to the client unchanged
3. The client must re-authenticate and retry

Token refresh is the responsibility of the Codex CLI / Claude Code CLI — not the proxy.

---

## Limitations

1. **No token refresh** — expired OAuth tokens cause 401s forwarded to client
2. **No Codex-specific compression** — compression pipeline works the same as other providers; Codex response format differences (Responses API vs Chat Completions) don't affect compression output
3. **OAuth rate limits** — Codex subscription may have different rate limits than API-key plans; these are enforced by OpenAI, not the proxy

---

## Provider Quick Reference

| Provider | Endpoint | Auth | Model Prefix |
|---|---|---|---|
| Anthropic (API key) | api.anthropic.com/v1/messages | `x-api-key` or `Bearer sk-ant-...` | `claude-` |
| Anthropic (Claude Code OAuth) | api.anthropic.com/v1/messages | `Bearer <oauth>` | `claude-` |
| OpenAI (API key) | api.openai.com/v1/chat/completions | `Bearer sk-...` | `gpt-`, `o1`, `o3` |
| OpenAI Codex (subscription) | api.openai.com/v1/responses | `Bearer <oauth>` | `gpt-5.x-codex*` |
| Google | generativelanguage.googleapis.com | `Bearer AIza...` | `gemini-` |
