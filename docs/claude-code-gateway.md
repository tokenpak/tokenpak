# Claude Code Gateway

TokenPak can act as a drop-in HTTP proxy for Claude Code, intercepting all
traffic on `ANTHROPIC_BASE_URL` and applying cost-reduction features
(compression, vault injection, semantic caching) transparently.

## Quick Start

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766
claude --print "Hello" --model claude-sonnet-4-6
```

All Claude Code requests flow through the proxy. No SDK changes are needed.

---

## Semantic Cache — Wire-Format Awareness

TokenPak's semantic cache stores and serves LLM responses for near-duplicate
queries. As of CCG-15, the cache is **wire-format-aware**: JSON and SSE
(Server-Sent Events / streaming) responses are stored and served separately.

### How It Works

| Client request | Cache key dimension | Served as |
|-----------------------------|---------------------|------------------------|
| `"stream": false` (JSON) | `wire_format=json` | `application/json` |
| `"stream": true` (SSE) | `wire_format=sse` | `text/event-stream` |

A JSON-format cache entry is **never** served to a streaming client, and vice
versa. Cross-format lookups always return a cache miss.

### Cache Hit Behaviour

On a cache hit the proxy responds immediately with:

```
HTTP/1.1 200 OK
Content-Type: <original upstream Content-Type>
Content-Length: <bytes>

<raw upstream response bytes>
```

The response is byte-identical to what the upstream Anthropic API returned
when the entry was first populated.

### SSE Buffer Cap

To prevent memory blowups on large streaming responses, the proxy accumulates
SSE chunks in a bounded in-memory tee buffer (default: **256 KB**). If a
streaming response exceeds the cap, the response is forwarded to the client
as normal but **not stored** in the cache. No error is raised.

The buffer cap is configurable via the `_SC_TEE_CAP` constant in `proxy.py`
(default `256 * 1024` bytes).

---

## Claude Code Bypass — Tier 2 (CCG-14 + CCG-15)

### Current Behaviour (Tier 2)

Requests carrying an `X-Claude-Code-Session-Id` header or a `claude-code`
substring in the `User-Agent` are **bypassed** by the semantic cache — both
on the lookup path and the store path.

```
journalctl --user -u tokenpak-proxy | grep phase_semantic_cache
# Claude Code requests: phase_semantic_cache: skipped:streaming-or-agent
# Non-CC streaming: phase_semantic_cache: miss (then hit on repeat)
# JSON SDK requests: phase_semantic_cache: miss (then hit on repeat)
```

### Why Claude Code Is Bypassed

A cached SSE response carries the **original `message_id` and `tool_use` IDs**
from the upstream Anthropic API. When replayed into a fresh Claude Code agent
loop, these stale IDs can desynchronize the agent's tool-call tracking, causing
unpredictable failures.

Non-Claude-Code streaming clients (e.g., raw OpenAI SDK with `stream=True`)
do **not** have this constraint and benefit from SSE caching after CCG-15.

### Deferred — Tier 3 (Agent-Aware Cache)

The next tier of caching for Claude Code traffic will synthesize fresh
`message_id` and `tool_use` IDs on cache hits, making replayed SSE responses
safe for agent loops. This is tracked as a separate task (no packet yet).

Until Tier 3 ships, the bypass guard remains active for all Claude Code
requests. The constant `"skipped:streaming-or-agent"` in the journal is the
canonical indicator that the CCG-14/CCG-15 guard fired.

---

---

## Client-Auth Pass-Through (2026-04-13)

When Claude Code sends its own OAuth credentials (`Authorization: Bearer`
with `anthropic-beta: oauth-2025-04-20`), the proxy operates in
**pass-through mode** — preserving the exact request byte structure while
still applying response-side features.

### Why Byte Preservation Matters

Anthropic's billing system uses the request byte signature to route between
"normal usage" and "extra usage" quota pools. Python's `json.dumps` produces
different bytes than Node.js's `JSON.stringify` (different whitespace, key
ordering). Re-serialized requests get routed to the wrong quota pool, causing
`YOU_RE_OUT_OF_EXTRA_USAGE` errors even when the account has remaining quota.

**Discovery method:** A pure relay proxy (zero processing) succeeded while
the tokenpak proxy failed — same token, same model, same moment. The only
difference was JSON re-serialization in the pipeline.

### Request Path

```
Claude Code CLI
 │ Authorization: Bearer + all native headers
 │ Body: ~110KB JSON (system prompt, tools, messages)
 ▼
tokenpak proxy
 ├─ Headers: forwarded verbatim (no allowlist filtering)
 ├─ Auth: passed through unchanged
 ├─ Body: original bytes preserved
 │ └─ Byte-level vault injection (optional, budget-limited)
 │ Splices text block directly into "system" array bytes
 │ without json.loads/json.dumps round-trip
 ▼
api.anthropic.com ← sees identical request to direct Claude Code
```

### What Works in Pass-Through Mode

| Feature | How | Notes |
|---------|-----|-------|
| **Vault injection** | Byte-level splice into system array | Budget-limited, relevance-gated |
| **Cost tracking** | Response-side `usage` field extraction | Per-model pricing to SQLite |
| **Request logging** | Structured JSON logs | Model, tokens, latency, status |
| **Budget enforcement** | Pre-send token estimate check | Returns 429 if exceeded |
| **DB logging** | SQLite `monitor.db` | Full audit trail |
| **Multi-provider failover** | Response-side 5xx detection | failover chain (planned) |

### What Does NOT Work in Pass-Through Mode

| Feature | Why |
|---------|-----|
| **Compaction** | Requires JSON re-serialization (breaks billing) |
| **Stable cache control** | Client manages its own cache_control TTL ordering |
| **Cache cap / TTL hotfix** | Would modify client's cache_control blocks |
| **Semantic cache** | Claude Code bypassed (stale message_id issue, CCG-14) |

### Vault Injection — Byte-Level Splice

Instead of `json.loads → modify → json.dumps`, the proxy:

1. Finds the closing `]` of the `"system"` array via byte scanning
 (tracks string state + nesting depth, ~microseconds for 110KB)
2. JSON-escapes the injection text with `json.dumps(text)`
3. Splices `{"type": "text", "text": <escaped>}` at the bracket offset
4. All other bytes remain untouched

**Relevance gate:** Injection is skipped when:
- User prompt is too short (< `TOKENPAK_CC_INJECT_MIN_QUERY` chars, default 50)
- No vault blocks score above threshold (`INJECT_MIN_SCORE`, default 2.0)
- Budget is zero (`TOKENPAK_CC_INJECT_MAX_CHARS=0`)

### How Claude Code Authenticates

Claude Code sends (discovered via request interception 2026-04-13):

```
Authorization: Bearer sk-ant-oat01-...
anthropic-beta: claude-code-20250219,oauth-2025-04-20,...
User-Agent: claude-cli/2.1.104 (external, cli)
x-app: cli
X-Claude-Code-Session-Id: <uuid>
X-Stainless-*: <sdk metadata>
```

Key finding: `x-api-key` is NOT used by Claude Code CLI. The
`oauth-2025-04-20` beta flag enables `Authorization: Bearer` for OAuth
tokens. Without this flag, `Bearer` returns "OAuth authentication is
currently not supported."

---

## Configuration

| Environment variable | Default | Description |
|-----------------------------------|---------|--------------------------------------------------------|
| `ANTHROPIC_BASE_URL` | — | Set to `http://127.0.0.1:8766` for Claude Code clients |
| `TOKENPAK_CC_INJECT_MAX_CHARS` | `2000` | Max vault injection chars for Claude Code (~500 tokens) |
| `TOKENPAK_CC_INJECT_MIN_QUERY` | `50` | Min user prompt length to trigger vault injection |
| `TOKENPAK_INJECT_MIN_SCORE` | `2.0` | Min BM25 relevance score for vault blocks |
| `TOKENPAK_INJECT_BUDGET` | `4000` | Vault search budget (tokens) — results trimmed by CC max |
| `TOKENPAK_SEMANTIC_CACHE` | `0` | Enable semantic cache (`1` to enable) |
| `TOKENPAK_UPSTREAM_TIMEOUT` | `90` | Upstream request timeout in seconds |

---

## Related Tasks

| Task | Description |
|--------|---------------------------------------------------------------------|
| CCG-14 | Tier 1 hotfix — bypass cache for streaming + Claude Code requests |
| CCG-15 | Tier 2 — wire-format-aware cache stores and serves SSE responses |
| CCG-16 | Regression tests for the CCG-14/15 guard composition |
| Tier 3 | Agent-aware cache — synthesize fresh IDs on hit (not yet scoped) |
