# TokenPak Cache Architecture

> **Sprint:** Cache Efficiency Sprint (P0 wiring + P1 poison removal + P1 deterministic retrieval + P2 telemetry)
> **Last updated:** 2026-03-09

---

## 1. Overview

TokenPak uses a two-tier prompt caching strategy designed to maximize Anthropic prompt cache hit rates. The core idea: split every LLM request into a **stable prefix** (content that never changes between requests) and a **volatile tail** (content that changes per request or per session).

**Why this matters:**

Anthropic prompt caching caches everything up to the last `cache_control: ephemeral` block. If dynamic content (timestamps, retrieved vault chunks, user messages) appears before that marker, the cache key changes every request → 0% cache hits.

With the stable/volatile split properly enforced, the stable prefix stays **byte-for-byte identical** across consecutive requests, enabling consistent cache reuse.

**Target cache hit rate:** 60%+ (from ~10% with cache poison, ~50% after poison removal, 60%+ with full deterministic retrieval)

---

## 2. Stable Prefix Design

### What Goes in the Stable Prefix

The stable prefix contains content that is fixed at startup or configuration load time:

- **System prompt** — static instructions (SOUL.md, project context, policies)
- **Frozen tool schemas** — all tool definitions, normalized once at first use (see §5)
- **Static policy blocks** — routing rules, safety instructions, static context files

The last stable block receives `cache_control: {"type": "ephemeral"}`, which tells Anthropic to cache everything up to and including that block.

### What's Banned from the Stable Prefix (Poison Patterns)

The proxy detects and excludes blocks containing any of the following:

| Pattern | Example | Why It's Poison |
|---------|---------|-----------------|
| ISO timestamps | `2026-03-09T14:25:00` | Changes every request |
| Relative time references | `"today is"`, `"current time"`, `"current date"` | Changes every request |
| Retrieved context tags | `<retrieved_context>`, `<vault_context>`, `[vault injection]` | Per-query content |
| Vault block headers | `--- [path/to/file.md] (relevance: 0.8) ---` | Dynamic search results |

Detection is implemented in `tokenpak/agent/proxy/prompt_builder.py` via `_VOLATILE_PATTERNS` (regex list) and `_is_volatile_block()`.

### Token Budget

The stable prefix has no hard-coded byte limit in the current implementation. In practice it is bounded by the system prompt size — typically the static files loaded at startup. The goal is to keep the stable prefix as large as possible (to maximize cacheable tokens) while keeping it 100% static.

---

## 3. Volatile Tail

### What Goes in the Volatile Tail

Content that changes per request or per session is placed **after** the `cache_control` marker:

- **BM25 vault injection** — retrieved context blocks (see §4)
- **Per-session context** — session-specific dynamic data
- **User messages** — always volatile by definition (`messages` array)

Volatile tail blocks do **not** receive `cache_control`. Anthropic does not cache content after the last cache marker.

### Why Retrieval Goes Here

BM25 search results vary by query. Even with deterministic ordering (see §4), the content itself changes when the query changes. Placing retrieval in the volatile tail preserves cache hits on the stable prefix while still injecting fresh context per request.

### Token Budget

Vault injection is hard-capped at **4,000 tokens** (see `DEFAULT_MAX_TOKENS` in `tokenpak/agent/vault/retrieval.py`). Content is truncated at block boundaries to fit within this budget.

---

## 4. Deterministic Retrieval

To ensure that repeated requests with semantically identical queries produce byte-identical prompt injections, retrieval results are sorted by a fixed key before injection.

### Sort Key

```python
key=lambda item: (
 -item[1], # BM25 score descending (primary)
 item[0].get("source_path", ""), # file path ascending (tie-break A)
 item[0].get("block_id", ""), # chunk ID ascending (tie-break B)
)
```

Implementation: `tokenpak/agent/vault/retrieval.py` → `sort_retrieval_results()`

### Token Cap

Hard cap: **4,000 tokens** (enforced in `inject_retrieved_context()`). Blocks are added greedily in sorted order until fewer than 50 tokens remain in the budget.

### Fixed Section Header

Every injection begins with the exact string:

```
## Retrieved Context
```

This is a constant (`RETRIEVED_CONTEXT_HEADER`) — never changes, preserving byte-identity for the header portion.

### Fixed Block Format

Each result is emitted as:

```
--- [source/path.md] (relevance: 0.8) ---
<content text>
```

The `(relevance: X.X)` value uses `.1f` formatting (one decimal place), always stable for the same score.

---

## 5. Cache Poison Removal

Cache poison is any dynamic content that causes the stable prefix bytes to differ between requests, preventing cache hits.

### Banned: Timestamps in Prompts

`datetime.now()`, `time.time()`, `date.today()` must not appear in prompt-building code. They belong in log statements only.

```python
# ❌ Poison
system_prompt = f"Current time: {datetime.now().isoformat()}"

# ✅ Safe
logger.info(f"Request at {datetime.now()}")
system_prompt = "You are an AI assistant." # static
```

Detection: `prompt_builder.py` flags blocks matching `\bcurrent time\b`, `\bcurrent date\b`, ISO timestamp patterns.

### Banned: UUIDs and Request IDs in Prompt Text

Request IDs and UUIDs belong in HTTP headers and log output — not in the prompt body.

```python
# ❌ Poison
context = f"Request ID: {uuid.uuid4()}\nProcessing..."

# ✅ Safe
request_id = str(uuid.uuid4())
headers["X-Request-ID"] = request_id
logger.info(f"request_id={request_id}")
context = "Processing..."
```

### Frozen Tool Schemas

Tool schemas sent with each LLM request are large (10–20 KB) and stable. The `ToolSchemaRegistry` (`tokenpak/agent/proxy/tool_schema_registry.py`) solves schema drift by:

1. **Normalizing** the tools array deterministically (sorted by name, all dict keys sorted recursively via `_normalize_schema()`)
2. **Freezing** the result at first use — stores the normalized bytes as `_frozen_tools`
3. **Detecting actual changes** via SHA-256 hash comparison — updates the frozen copy only when schemas genuinely change (e.g., new tool added)
4. **Returning identical bytes** every request as long as schemas haven't changed

The registry is a module-level singleton accessed via `get_registry()`.

### Auditing for New Poisons

To check for new poison sources:

```bash
# Timestamps in proxy
grep -r "datetime\|time\.time\|date\." ~/Projects/tokenpak/tokenpak/proxy/ \
 --include="*.py" | grep -v "logger\|#"

# UUIDs in proxy
grep -r "uuid\|uuid4" ~/Projects/tokenpak/tokenpak/proxy/ \
 --include="*.py" | grep -v "logger\|#\|header"

# Dynamic tool schema rendering
grep -r "render_tool\|get_tool_schema" ~/Projects/tokenpak/tokenpak/proxy/ \
 --include="*.py"
```

---

## 6. In-Process Cache Layer

Beyond prompt caching, TokenPak maintains an in-process cache layer (`tokenpak/cache/`) for operational data.

### StableCache (`tokenpak/cache/stable_cache.py`)

LRU cache for long-lived content (compiled pack schemas, tool definitions, static vault index snapshots).

| Property | Value |
|----------|-------|
| Default TTL | 24 hours |
| Default max size | 500 entries |
| Eviction | LRU |
| Thread-safe | Yes |

### VolatileCache (`tokenpak/cache/volatile_cache.py`)

Short-lived TTL cache for per-session or per-request data (BM25 search results, vault injection text).

| Property | Value |
|----------|-------|
| Default TTL | 270 seconds (4.5 min) |
| Default max size | 1,000 entries |
| Eviction | Oldest-first when at capacity |
| Thread-safe | Yes |

### CacheRegistry (`tokenpak/cache/registry.py`)

Central registry for named cache instances. Pre-registered names:

| Name | Type | Purpose |
|------|------|---------|
| `"default"` | VolatileCache (TTL=270s) | General per-session data |
| `"stable"` | StableCache (TTL=24h) | Pack schemas, static content |
| `"injection"` | VolatileCache (TTL=270s) | Proxy vault injection alias |

Custom caches can be registered via `CacheRegistry.register(name, cache)`.

### Telemetry Query Cache (`tokenpak/telemetry/cache.py`)

A separate `CacheStore` wraps dashboard query results to reduce SQLite load.

| Query type | TTL |
|-----------|-----|
| Rollup / KPI summary | 5 minutes |
| Filter options | 10 minutes |
| Insights | 5 minutes |
| Pricing | 1 hour |
| Trace search | No cache (real-time) |

---

## 7. Telemetry

### `/v1/cache/stats` Endpoint

Returns hit/miss statistics for the telemetry query cache.

**Route:** `GET /v1/cache/stats` (implemented in `tokenpak/telemetry/server.py`)

**Response:**
```json
{
 "status": "ok",
 "cache": {
 "size": 42,
 "hits": 1280,
 "misses": 320,
 "hit_rate": 80.0,
 "max_size": 1000
 }
}
```

### `CacheStore.stats` Fields

| Field | Type | Description |
|-------|------|-------------|
| `size` | int | Current number of entries in cache |
| `hits` | int | Cumulative cache hits since startup |
| `misses` | int | Cumulative cache misses since startup |
| `hit_rate` | float | `hits / (hits + misses) * 100`, rounded to 1 decimal |
| `max_size` | int | Configured maximum entries |

### Additional Cache Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/cache/stats` | GET | Hit/miss statistics |
| `/v1/cache/clear` | POST | Clear all entries (or by `?prefix=`) |
| `/v1/cache/evict` | POST | Remove expired entries |

### Hit Rate Target

**Target: 60%+ cache hit rate** for prompt caching across all models and sizes.

- With cache poison (pre-sprint): ~10%
- After poison removal alone: ~50%
- After deterministic retrieval + full pipeline: 60%+

---

## 8. Request Flow Diagrams

### Prompt Cache Boundary

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ANTHROPIC MESSAGES API REQUEST │
│ │
│ "system": [ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ STABLE PREFIX (byte-identical across requests) │ │
│ │ │ │
│ │ { type: "text", text: "<SOUL.md + project context>" }, │ │
│ │ { type: "text", text: "<static policy blocks>" }, │ │
│ │ { type: "text", text: "<last stable block>", │ │
│ │ cache_control: { type: "ephemeral" } } ◄── MARKER │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ VOLATILE TAIL (changes per request — NOT cached) │ │
│ │ │ │
│ │ { type: "text", text: "<BM25 vault injection>" }, │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ] │
│ │
│ "tools": [ <frozen by ToolSchemaRegistry — byte-identical> ] │
│ │
│ "messages": [ │
│ { role: "user", content: "<user message>" } ◄── always volatile │
│ ] │
└─────────────────────────────────────────────────────────────────────────┘
```

### Request Processing Pipeline

```
Incoming Request
 │
 ▼
┌─────────────────┐
│ ToolSchemaReg. │ normalize_request()
│ (freeze tools) │ → deterministic JSON, byte-identical
└────────┬────────┘
 │
 ▼
┌─────────────────┐
│ PromptBuilder │ apply_stable_cache_control()
│ (classify │ → classify system blocks
│ stable/volat.) │ → add cache_control to last stable block
└────────┬────────┘
 │
 ▼
┌─────────────────┐
│ Vault Retrieval │ inject_retrieved_context()
│ (BM25 + sort) │ → sort (-score, path, chunk_id)
│ │ → cap at 4,000 tokens
│ │ → append after cache boundary
└────────┬────────┘
 │
 ▼
┌─────────────────┐
│ Anthropic API │ Cache hit if stable prefix is byte-identical
└─────────────────┘
 │
 ▼
┌─────────────────┐
│ Telemetry │ Record cache_read_input_tokens
│ (cache stats) │ /v1/cache/stats available
└─────────────────┘
```

### Stable vs. Volatile Content Classification

```
System Block Classification (prompt_builder.py)

Block text
 │
 ├─ Contains ISO timestamp? → VOLATILE
 ├─ Contains "current time/date"? → VOLATILE
 ├─ Contains <retrieved_context>? → VOLATILE
 ├─ Contains vault block header? → VOLATILE
 │ (--- [path] (relevance: X) ---)
 │
 └─ None of the above → STABLE
 │
 Last STABLE block gets
 cache_control: ephemeral
```

---

## Implementation File Reference

| Component | File |
|-----------|------|
| PromptBuilder / cache_control placement | `tokenpak/agent/proxy/prompt_builder.py` |
| ToolSchemaRegistry (frozen schemas) | `tokenpak/agent/proxy/tool_schema_registry.py` |
| Deterministic retrieval sort + injection | `tokenpak/agent/vault/retrieval.py` |
| StableCache | `tokenpak/cache/stable_cache.py` |
| VolatileCache | `tokenpak/cache/volatile_cache.py` |
| CacheRegistry | `tokenpak/cache/registry.py` |
| Telemetry query cache (CacheStore) | `tokenpak/telemetry/cache.py` |
| Telemetry endpoints incl. /v1/cache/stats | `tokenpak/telemetry/server.py` |
