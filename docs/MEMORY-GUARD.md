# Adaptive MemoryGuard

TokenPak includes a built-in memory pressure manager that prevents OOM kills without sacrificing cache performance.

---

## How It Works

The MemoryGuard runs as a background thread inside the proxy process. Every 30 seconds it:

1. **Reads its own RSS** from `/proc/self/status`
2. **Reads system-available memory** from `/proc/meminfo`
3. **Compares against adaptive thresholds** calculated from total system RAM
4. **Takes graduated action** — keeping hot cache entries, evicting only the coldest

### Threshold Calculation

At startup, the guard auto-calculates thresholds based on the host machine:

```
budget  = min(total_ram × proxy_share, 2048MB)
target  = budget × 0.75    ← start mild eviction
ceiling = budget × 0.95    ← aggressive eviction
sys_low = total_ram × 0.08 ← system-available floor (min 200MB)
```

| Machine | RAM | Target | Ceiling | sys_low |
|---------|-----|--------|---------|---------|
| 2GB VPS | 2048MB | 537MB | 680MB | 200MB |
| 4GB host | 3896MB | 998MB | 1264MB | 304MB |
| 8GB host | 7800MB | 1536MB | 1945MB | 624MB |
| 16GB laptop | 16384MB | 1536MB | 1945MB | 1310MB |

No configuration needed — it adapts automatically.

---

## Eviction Strategy

The guard **never flushes everything**. It selectively evicts the coldest entries while preserving hot/recent data:

### GREEN (RSS < target, sys_avail > sys_low)
No action. Caches operate normally.

### YELLOW (RSS ≥ target OR sys_avail < sys_low)
1. `gc.collect()` — free Python reference cycles
2. `malloc_trim(0)` — return freed glibc arenas to the OS
3. Evict coldest **25%** of compact cache (preserves 75% of recent compressions)
4. Evict coldest **25%** of token count cache (cheap to recompute on miss)
5. Sweep expired semantic cache entries

### RED (RSS ≥ ceiling)
1. Evict coldest **50%** of compact cache
2. Evict coldest **75%** of token count cache
3. Sweep expired semantic cache entries
4. Double `gc.collect()` + `malloc_trim()` pass

### What's NEVER evicted
- **Vault index** — block metadata + BM25 inverted index (critical for injection quality)
- **Session counters** — cumulative stats
- **Provider circuit breakers** — routing health
- **Hot cache entries** — the most recently used 50–75% of each cache

---

## Monitoring

### `/memory` endpoint

```bash
curl http://localhost:8766/memory
```

```json
{
  "system": {
    "total_ram_mb": 3805,
    "available_mb": 1452
  },
  "proxy": {
    "rss_mb": 826,
    "compact_cache_size": 57,
    "compact_cache_max": 2000,
    "token_cache_size": 136,
    "token_cache_max": 1024,
    "semantic_cache_size": 1
  },
  "guard": {
    "checks": 42,
    "yellow_triggers": 3,
    "red_triggers": 0,
    "sys_low_triggers": 0,
    "compact_evictions": 57,
    "token_evictions": 135,
    "peak_rss_mb": 1100,
    "last_rss_mb": 826,
    "last_sys_avail_mb": 1452,
    "last_level": "GREEN",
    "total_reclaimed_mb": 274,
    "config": {
      "total_ram_mb": 3805,
      "target_mb": 998,
      "ceiling_mb": 1264,
      "sys_low_mb": 304,
      "check_interval_secs": 30
    }
  }
}
```

### Proxy logs

The guard logs to the proxy's stdout (captured by systemd journal):

```
🛡️  MemoryGuard active: system=3805MB target=998MB ceiling=1264MB sys_low=304MB
🟡 MemoryGuard YELLOW: RSS=1005MB >= 998MB
  Compact cache: evicted 14 coldest entries (25%)
  Token cache: evicted 34 entries (25%)
🟡 YELLOW done: 1005MB → 831MB (freed 174MB, sys_avail=1481MB)
```

---

## Configuration

All settings are optional. The guard auto-configures from system RAM by default.

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `TOKENPAK_MEMORY_GUARD` | `1` | Set to `0` to disable entirely |
| `TOKENPAK_MEMORY_TARGET_MB` | auto | Override the soft eviction threshold |
| `TOKENPAK_MEMORY_CEILING_MB` | auto | Override the aggressive eviction threshold |
| `TOKENPAK_MEMORY_CHECK_SECS` | `30` | Interval between checks (seconds) |
| `TOKENPAK_MEMORY_PROXY_SHARE` | `0.35` | Fraction of system RAM allocated to proxy |
| `TOKENPAK_MEMORY_BUDGET_MAX` | `2048` | Maximum budget cap in MB (regardless of RAM) |
| `TOKENPAK_MEMORY_SYS_LOW_MB` | auto | System-available floor for forced eviction |

### glibc malloc tuning

For best results, also set these in the proxy's systemd environment:

```ini
Environment=MALLOC_TRIM_THRESHOLD_=65536
Environment=MALLOC_MMAP_THRESHOLD_=65536
```

These tell glibc to return freed memory pages to the OS sooner instead of keeping them in arena pools.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  TokenPak Proxy Process                      │
│                                              │
│  ┌─────────────┐  ┌──────────────────────┐   │
│  │ Request      │  │ MemoryGuard Thread   │   │
│  │ Handler      │  │                      │   │
│  │  ↕ caches    │  │ Every 30s:           │   │
│  │  - compact   │←─│  1. Read RSS         │   │
│  │  - token     │  │  2. Read sys_avail   │   │
│  │  - semantic  │  │  3. Compare vs auto  │   │
│  │              │  │     thresholds       │   │
│  └─────────────┘  │  4. Evict coldest N%  │   │
│                    │     if over target    │   │
│  ┌─────────────┐  │  5. gc + malloc_trim  │   │
│  │ /memory     │──│                      │   │
│  │ endpoint    │  └──────────────────────┘   │
│  └─────────────┘                             │
└──────────────────────────────────────────────┘
```

The guard thread holds no locks on its own. Cache eviction callbacks operate on the same data structures as the request handler, using the existing insertion-order eviction (pop from front = coldest).
