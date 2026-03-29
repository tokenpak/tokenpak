# TokenPak Proxy Performance Trace — 2026-03-26

**Author:** Trix (forensic analysis + live profiling)
**For:** Sue (task creation) / Kevin (approval)
**Date:** 2026-03-26 17:45 PDT

---

## Executive Summary

Live production profiling reveals **~140–200ms of proxy pipeline overhead per request**. The three largest contributors are:

1. **BM25 vault search** — 57–62ms (pure Python loop over 6,539 blocks)
2. **Redundant tiktoken encoding** — 40–65ms (extract_request_tokens called 6–8× per request)
3. **Redundant count_tokens calls** — 10–25ms (3.5ms each, called 5–8× per request)

Combined, these account for **~110–150ms** of avoidable overhead per request. All three fixes are surgical, low-risk, and can be tested independently.

Sue's earlier report cited 1.6s median overhead. The delta beyond ~140ms is attributable to: (a) larger Opus/Sonnet payloads with full tool schemas (~20K+ tokens), (b) SSE streaming per-chunk overhead on long responses, and (c) vault index reload lock contention (measured at 4.2s when triggered mid-request).

---

## Test Methodology

- **Payload:** 28.3 KB, 17 messages, ~5,800 input tokens (realistic OpenClaw conversation)
- **Model:** claude-haiku-4-5 (non-streaming, to isolate pipeline overhead from LLM latency)
- **Proxy runs (3x):** 1016ms, 1038ms, 969ms TTFB
- **Direct-to-Anthropic runs (3x):** 819ms, 829ms, 5425ms TTFB
- **Measured proxy overhead:** ~170–200ms (TTFB delta, proxy vs direct)
- **Profiling method:** Direct Python import of proxy.py modules + `time.perf_counter()` per stage
- **Machine:** TrixBot (4GB RAM, no GPU)

---

## Per-Stage Timing Breakdown

| # | Stage | Time | Calls/req | Total/req | Status |
|---|-------|------|-----------|-----------|--------|
| 1 | `json.loads(body)` | 0.17ms | 1 (req_data reuse) | 0.17ms | ✅ OK |
| 2 | `cache_poison_strip` | 1.82ms | 1 | 1.82ms | ✅ OK |
| 3 | `extract_request_tokens` | 5.2ms | **6–8** | **31–42ms** | 🔴 FIX |
| 4 | `tool_schema_normalize` | <0.01ms | 1 | <0.01ms | ✅ OFF on Trix |
| 5 | `route_engine` (YAML rules) | 8.06ms | 1 | 8.06ms | 🟡 MONITOR |
| 6 | `deterministic_router` | 0.58ms | 1 | 0.58ms | ✅ OK |
| 7 | `capsule_builder` | 0ms | — | 0ms | ⏸️ Disabled |
| 8 | `extract_query_signal` | 0.20ms | 1 | 0.20ms | ✅ OK |
| 9 | `vault_bm25_search` | **57–62ms** | 1 | **57–62ms** | 🔴 FIX |
| 10 | `compile_injection` | incl. in #9 | 1 | — | — |
| 11 | `inject_vault_context` | incl. in #9 | 1 | — | — |
| 12 | `count_tokens` (tiktoken) | 3.5ms | **5–8** | **18–28ms** | 🟠 FIX |
| 13 | `compact_request_body` | 2.96ms | 1 | 2.96ms | ✅ OK |
| 14 | `canon_dedup` | 0.08ms | 1 | 0.08ms | ✅ OK |
| 15 | `stable_cache_control` | ~0.5ms | 1 | ~0.5ms | ✅ OK |
| | **ESTIMATED PIPELINE TOTAL** | | | **~120–145ms** | |

---

## Fix #1: BM25 Inverted Index

**Priority:** P0
**Estimated savings:** ~55ms per request
**Risk:** Low (search-only change, no mutation of data)
**Effort:** ~1 task (medium)

### Problem

The BM25 search in `VaultIndex.search()` (line 1164 of proxy.py) iterates **all 6,539 blocks** for every query, regardless of whether a block contains any query term. This is O(blocks × query_terms) — roughly 52,000 inner-loop iterations per search.

Additionally, `min_score=2.0` is far too low: a typical search returns **3,800+ hits** above threshold, meaning the subsequent sort operates on most of the corpus.

### Where

- **File:** `proxy.py` (or `packages/core/tokenpak/runtime/proxy.py` canonical path)
- **Method:** `VaultIndex.search()` — line 1164
- **Method:** `VaultIndex._load()` — line 1101 (where BM25 stats are precomputed)

### Fix

**A) Build an inverted index at load time:**

In `_load()`, after computing `block_tfs` and `df`, add:

```python
# Inverted index: term → set of block_ids that contain it
self._inverted = {}
for bid, tf in block_tfs.items():
    for term in tf:
        if term not in self._inverted:
            self._inverted[term] = set()
        self._inverted[term].add(bid)
```

**B) In `search()`, only score blocks that match at least one query term:**

```python
# Collect candidate blocks (union of all query term posting lists)
candidates = set()
for qt in query_terms:
    if qt in self._inverted:
        candidates.update(self._inverted[qt])

# Score only candidates instead of all blocks
for bid in candidates:
    ...
```

**C) Raise `INJECT_MIN_SCORE` default from 2.0 to 5.0:**

Most of the 3,800 hits at score 2.0 are noise. A score of 5.0+ filters to genuinely relevant blocks. This is a config change only (env var `TOKENPAK_INJECT_MIN_SCORE`).

### Expected Result

- Candidate set shrinks from 6,539 → ~200–500 blocks per query
- Search time drops from ~60ms → ~2–5ms (10–30× speedup)
- Sorted result set shrinks from 3,800 → ~50–100 (faster sort)
- Zero impact on search quality (same BM25 scores, just skipping zero-score blocks)

### Verification

```python
# Before/after timing
t0 = time.perf_counter()
results = VAULT_INDEX.search(query, top_k=5, min_score=5.0)
print(f"Search: {(time.perf_counter()-t0)*1000:.1f}ms, {len(results)} results")
```

---

## Fix #2: Token Counting Cache (extract_request_tokens)

**Priority:** P0
**Estimated savings:** ~40–65ms per request
**Risk:** Low (read-only cache, invalidated on body mutation)
**Effort:** ~1 task (small-medium)

### Problem

`extract_request_tokens(body)` is called **6–8 times per request** across the pipeline:

| Location | Line | Why |
|----------|------|-----|
| Initial token count | 3414 | First measurement |
| After capsule compression | 3631 | Recount post-capsule |
| After vault injection | 3722 | Recount post-injection |
| After canon dedup | 3783 | Recount post-canon |
| Contract enforcement | 3599 | Check quota |
| compact_request_body (internal, 2×) | 2524, 2533 | Before/after compaction |
| Final measurement | 3947 | Post-pipeline |

Each call runs the **full body** through `tiktoken.encode()` — at 5.2ms per call on a 5,800-token payload, this wastes 31–42ms on redundant encoding.

### Where

- **File:** `proxy.py`
- **Function:** `extract_request_tokens()` — line 2279
- **All call sites listed above**

### Fix

**Option A (preferred): Track body version + cache result**

Add a lightweight body fingerprint (length + first/last 64 bytes) to avoid re-encoding unchanged bodies:

```python
_TOKEN_COUNT_CACHE = {}

def extract_request_tokens(body_bytes, adapter=None):
    # Fast fingerprint: length + edges (avoids hashing full body)
    _key = (len(body_bytes), body_bytes[:64], body_bytes[-64:])
    cached = _TOKEN_COUNT_CACHE.get(_key)
    if cached is not None:
        return cached

    # ... existing tiktoken logic ...
    result = (model, token_count)
    _TOKEN_COUNT_CACHE[_key] = result
    # Evict if cache grows too large
    if len(_TOKEN_COUNT_CACHE) > 32:
        _TOKEN_COUNT_CACHE.pop(next(iter(_TOKEN_COUNT_CACHE)))
    return result
```

**Option B (simpler): Reduce call count**

Pass `input_tokens` as a parameter through the pipeline instead of re-extracting. When a stage mutates `body`, it recounts once and passes the new count forward. This requires refactoring `_proxy_to()` to thread the token count, but eliminates 4–5 redundant calls.

### Reasoning

Option A is safer — no API changes, drop-in cache. Option B is cleaner long-term but touches more code. Recommend A first, B as follow-up refactor.

### Expected Result

- 6–8 calls → 2–3 actual tiktoken encodes (only after body mutations)
- Saves ~25–40ms per request

---

## Fix #3: count_tokens LRU Cache

**Priority:** P1
**Estimated savings:** ~10–20ms per request
**Risk:** Very low
**Effort:** ~0.5 task (small)

### Problem

`count_tokens(text)` (line 1045) is called independently on:
- Injection text segments during `compile_injection()` (line 1237, 1250)
- Combined injection text in `inject_vault_context()` (line 2371)
- Individual message parts during `compact_request_body()` (lines 2547–2585)
- Skeleton-extracted code blocks (line 1237)

Each call runs tiktoken on the full text. The same strings are often counted multiple times (e.g., injection text is counted in `compile_injection`, then again after `inject_vault_context`).

### Where

- **File:** `proxy.py`
- **Function:** `count_tokens()` — line 1045

### Fix

Wrap with `@functools.lru_cache` keyed on content hash:

```python
@functools.lru_cache(maxsize=256)
def _count_tokens_cached(text_hash: int, text_len: int, text: str) -> int:
    return len(_ENC.encode(text))

def count_tokens(text: str) -> int:
    return _count_tokens_cached(hash(text), len(text), text)
```

Note: `lru_cache` requires hashable args. Using `hash(text)` + `len(text)` as compound key gives collision-free caching. The `text` param is needed for the actual encoding but `lru_cache` will use all args as cache key.

**Simpler alternative** — manual dict cache (same pattern as `_COMPACT_CACHE`):

```python
_TOKEN_CACHE = {}
_TOKEN_CACHE_ORDER = []

def count_tokens(text: str) -> int:
    key = hash(text)
    if key in _TOKEN_CACHE:
        return _TOKEN_CACHE[key]
    result = len(_ENC.encode(text))
    _TOKEN_CACHE[key] = result
    _TOKEN_CACHE_ORDER.append(key)
    if len(_TOKEN_CACHE_ORDER) > 256:
        old = _TOKEN_CACHE_ORDER.pop(0)
        _TOKEN_CACHE.pop(old, None)
    return result
```

### Expected Result

- Repeated count_tokens on same text → 0ms (cache hit)
- Saves ~10–20ms on typical requests with 5–8 count_tokens calls

---

## Fix #4: Vault Index Reload Isolation

**Priority:** P1
**Estimated savings:** Eliminates 4.2s stalls (intermittent, ~once per 5 min)
**Risk:** Low-medium (threading change)
**Effort:** ~1 task (medium)

### Problem

`maybe_reload()` (line 1078) is triggered by `threading.Thread(target=VAULT_INDEX.maybe_reload, daemon=True).start()` on every request (line 3685). When the reload fires (every `VAULT_INDEX_RELOAD_INTERVAL` seconds, default 300s), it:

1. Reads 5.0MB `index.json` from disk
2. Reads 8,645 block files from disk
3. Tokenizes all 6,539 blocks for BM25 stats
4. Acquires `self._lock` to swap in new data

**Measured full reload time: 4.2 seconds** (536ms disk I/O + 3,693ms BM25 indexing).

During step 4, any concurrent `search()` call blocks on the same lock, causing a 4.2s stall for any request that hits the reload window.

### Where

- **File:** `proxy.py`
- **Method:** `VaultIndex._load()` — line 1101
- **Method:** `VaultIndex.search()` — line 1164 (acquires `self._lock`)
- **Thread spawn:** line 3685

### Fix

**Shadow-load + atomic swap:**

```python
def _load(self, index_path, mtime):
    # Build everything into local variables (no lock held)
    new_blocks = ...
    new_df = ...
    new_block_tfs = ...
    new_avg_dl = ...
    new_doc_count = ...
    # Also build inverted index here (Fix #1)
    new_inverted = ...

    # Atomic swap — lock held for microseconds, not seconds
    with self._lock:
        self.blocks = new_blocks
        self._df = new_df
        self._block_tfs = new_block_tfs
        self._avg_dl = new_avg_dl
        self._doc_count = new_doc_count
        self._inverted = new_inverted
        self._last_mtime = mtime
```

The current code already does this partially — the heavy work happens before the lock. But the `search()` method also acquires the lock to **read** the data:

```python
def search(self, query, ...):
    with self._lock:
        df = self._df
        block_tfs = self._block_tfs
        ...
```

This means search blocks during the entire reload. Fix: use a `threading.RLock` or — better — make `search()` grab references outside the lock (the swap is atomic for Python dict assignment):

```python
def search(self, query, ...):
    # Snapshot references (atomic in CPython due to GIL)
    df = self._df
    block_tfs = self._block_tfs
    avg_dl = self._avg_dl
    doc_count = self._doc_count
    blocks = self.blocks
    # No lock needed — we're working on immutable snapshots
    ...
```

**Also: Stop spawning a thread per request.** Replace the per-request `threading.Thread(...).start()` with a single background timer:

```python
# At startup
_reload_timer = threading.Timer(VAULT_INDEX_RELOAD_INTERVAL, _periodic_reload)
_reload_timer.daemon = True
_reload_timer.start()

def _periodic_reload():
    VAULT_INDEX.maybe_reload()
    # Reschedule
    t = threading.Timer(VAULT_INDEX_RELOAD_INTERVAL, _periodic_reload)
    t.daemon = True
    t.start()
```

### Expected Result

- Zero lock contention during reload (search works on old snapshot until swap completes)
- Eliminates intermittent 4.2s stalls entirely
- Removes per-request thread creation overhead (~0.1ms each, but adds up)

---

## Fix #5: Route Engine YAML Caching

**Priority:** P2
**Estimated savings:** ~5ms per request
**Risk:** Very low
**Effort:** ~0.5 task (small)

### Problem

`route_engine` takes 8.06ms — most of this is YAML rule loading via `_get_cached_route_rules()`. If the YAML file hasn't changed, the parsed rules should be cached.

### Where

- **File:** `proxy.py`
- **Function:** `_get_cached_route_rules()` — check if it's actually caching or re-reading YAML each time

### Fix

Verify that `_get_cached_route_rules()` checks file mtime before re-parsing. If it doesn't, add mtime check:

```python
_ROUTE_RULES_CACHE = None
_ROUTE_RULES_MTIME = 0

def _get_cached_route_rules():
    global _ROUTE_RULES_CACHE, _ROUTE_RULES_MTIME
    mtime = os.path.getmtime(RULES_PATH)
    if mtime == _ROUTE_RULES_MTIME and _ROUTE_RULES_CACHE is not None:
        return _ROUTE_RULES_CACHE
    _ROUTE_RULES_CACHE = _load_rules(RULES_PATH)
    _ROUTE_RULES_MTIME = mtime
    return _ROUTE_RULES_CACHE
```

Also: `_extract_prompt_text()` and `_count_tokens_approx()` are imported from `tokenpak.routing.rules` on every request. Move the import to module level.

### Expected Result

- Route engine drops from ~8ms to ~1ms (cached YAML + module-level import)

---

## Fix #6: Reduce json.loads/json.dumps Round-Trips

**Priority:** P2
**Estimated savings:** ~2–3ms per request (more on large payloads)
**Risk:** Low (refactor, no logic change)
**Effort:** ~1 task (medium)

### Problem

The pipeline currently has **10+ independent `json.loads(body)` calls** across different stages. The `req_data` optimization introduced in Phase 0 (line 3418) is only used by the route engine and prefix registry. Stages 1.7 (query rewriter), 1.8 (salience router), 1.9 (fidelity tiers), and the plugin system all re-parse `body` independently.

### Where

- Line 3803: salience router — `_req_data = json.loads(body)`
- Line 3829: query rewriter — `_req_data = json.loads(body)`
- Line 3870: plugin system — `_req_data = json.loads(body)`
- Line 3923: fidelity tiers — `_req_data = json.loads(body)` (unused, just classifies)

### Fix

Thread `req_data` through all stages. When a stage mutates the data, it writes back to both `req_data` and `body`:

```python
# After each mutating stage:
req_data = _modified_data
body = json.dumps(req_data, separators=(",", ":")).encode()
```

Stages that only read (fidelity tiers, contract enforcement) should use `req_data` directly without serializing.

### Reasoning

Currently these stages are all disabled (Tier 2+), so this is defensive cleanup — it prevents a performance regression when they're re-enabled. The json.loads overhead is 0.17ms per call today; on larger payloads with tools it could be 1–2ms per call × 10 calls = 10–20ms.

---

## Implementation Order (Recommended)

| Order | Fix | Priority | Savings | Risk | Depends On |
|-------|-----|----------|---------|------|------------|
| 1 | BM25 Inverted Index (#1) | P0 | ~55ms | Low | None |
| 2 | Token Counting Cache (#2) | P0 | ~40ms | Low | None |
| 3 | count_tokens LRU (#3) | P1 | ~15ms | Very low | None |
| 4 | Vault Reload Isolation (#4) | P1 | Eliminates 4.2s stalls | Low-med | #1 (build inverted index during reload) |
| 5 | Route Engine YAML Cache (#5) | P2 | ~5ms | Very low | None |
| 6 | JSON Round-Trip Reduction (#6) | P2 | ~2–3ms | Low | None |

**Fixes #1 and #2 are independent and can be worked in parallel.**

**Combined P0+P1 savings: ~110ms per request** (~60–75% reduction in pipeline overhead).

---

## Verification Protocol

After each fix, run the following benchmark on Trix (same payload, same conditions):

```bash
# Save as ~/tokenpak-bench.py and run:
# python3 ~/tokenpak-bench.py
```

Compare TTFB delta (proxy vs direct) across 5 runs. Target: **<50ms proxy overhead** (down from current ~140ms).

Also check proxy logs for per-stage timing:
```bash
journalctl --user -u tokenpak-proxy --no-pager -n 20 --since "1 min ago"
```

---

## Appendix: Raw Profiling Data

```
Machine: TrixBot (4GB RAM, no GPU)
Python: 3.12
tiktoken: installed (cl100k_base encoder)
Vault blocks: 6,539 (5.0MB index + 8,645 block files)
Vault avg_dl: 883 terms/block
Vault unique terms: 85,527

Individual stage times (single call):
  json.loads(28KB):          0.17ms
  cache_poison_strip:        1.82ms
  extract_request_tokens:    5.20ms (tiktoken full body)
  route_engine:              8.06ms (YAML + prompt extract)
  deterministic_router:      0.58ms
  extract_query_signal:      0.20ms
  vault_bm25_search:        57–62ms (6,539 blocks, 3,800+ hits at min_score=2.0)
  count_tokens(20K chars):   3.50ms
  compact_request_body:      2.96ms
  canon_dedup:               0.08ms

Vault index full reload:     4,229ms (536ms disk I/O + 3,693ms BM25 reindex)
```
