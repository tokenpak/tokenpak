# Claude Code Gateway

TokenPak can act as a drop-in HTTP proxy for Claude Code, intercepting all
traffic on `ANTHROPIC_BASE_URL` and applying cost-reduction features
(compression, vault injection, semantic caching) transparently.

## Quick Start

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766
claude --print "Hello" --model claude-sonnet-4-6
```

All Claude Code requests flow through the proxy.  No SDK changes are needed.

---

## Semantic Cache — Wire-Format Awareness (CCG-15)

TokenPak's semantic cache stores and serves LLM responses for near-duplicate
queries.  As of CCG-15, the cache is **wire-format-aware**: JSON and SSE
(Server-Sent Events / streaming) responses are stored and served separately.

### How It Works

| Client request              | Cache key dimension | Served as              |
|-----------------------------|---------------------|------------------------|
| `"stream": false` (JSON)    | `wire_format=json`  | `application/json`     |
| `"stream": true`  (SSE)     | `wire_format=sse`   | `text/event-stream`    |

A JSON-format cache entry is **never** served to a streaming client, and vice
versa.  Cross-format lookups always return a cache miss.

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
SSE chunks in a bounded in-memory tee buffer (default: **256 KB**).  If a
streaming response exceeds the cap, the response is forwarded to the client
as normal but **not stored** in the cache.  No error is raised.

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
# Claude Code requests:     phase_semantic_cache: skipped:streaming-or-agent
# Non-CC streaming:         phase_semantic_cache: miss  (then hit on repeat)
# JSON SDK requests:        phase_semantic_cache: miss  (then hit on repeat)
```

### Why Claude Code Is Bypassed

A cached SSE response carries the **original `message_id` and `tool_use` IDs**
from the upstream Anthropic API.  When replayed into a fresh Claude Code agent
loop, these stale IDs can desynchronize the agent's tool-call tracking, causing
unpredictable failures.

Non-Claude-Code streaming clients (e.g., raw OpenAI SDK with `stream=True`)
do **not** have this constraint and benefit from SSE caching after CCG-15.

### Deferred — Tier 3 (Agent-Aware Cache)

The next tier of caching for Claude Code traffic will synthesize fresh
`message_id` and `tool_use` IDs on cache hits, making replayed SSE responses
safe for agent loops.  This is tracked as a separate task (no packet yet).

Until Tier 3 ships, the bypass guard remains active for all Claude Code
requests.  The constant `"skipped:streaming-or-agent"` in the journal is the
canonical indicator that the CCG-14/CCG-15 guard fired.

---

## Configuration

| Environment variable         | Default | Description                                      |
|------------------------------|---------|--------------------------------------------------|
| `TOKENPAK_SEMANTIC_CACHE`    | `0`     | Enable semantic cache (`1` to enable)            |
| `TOKENPAK_UPSTREAM_TIMEOUT`  | `90`    | Upstream request timeout in seconds              |
| `ANTHROPIC_BASE_URL`         | —       | Override for Claude Code clients                 |

---

## Related Tasks

| Task   | Description                                                         |
|--------|---------------------------------------------------------------------|
| CCG-14 | Tier 1 hotfix — bypass cache for streaming + Claude Code requests  |
| CCG-15 | Tier 2 — wire-format-aware cache stores and serves SSE responses   |
| CCG-16 | Regression tests for the CCG-14/15 guard composition               |
| Tier 3 | Agent-aware cache — synthesize fresh IDs on hit (not yet scoped)   |
