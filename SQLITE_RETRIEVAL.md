# SQLite Retrieval Backend — Migration Guide

## Overview

`proxy_v4` now supports two vault retrieval backends:

| Backend | Default | Best For |
|---------|---------|----------|
| `json_blocks` | ✅ Yes | Small-medium indexes (<2k blocks), simplicity |
| `sqlite` | No | Large indexes (5k+ blocks), frequent reloads, incremental updates |

## Enabling SQLite Backend

Set the environment variable before starting `proxy_v4`:

```bash
export TOKENPAK_RETRIEVAL_BACKEND=sqlite
```

Or in your systemd unit / `.env`:

```env
TOKENPAK_RETRIEVAL_BACKEND=sqlite
```

**Default is `json_blocks`** — no change required for existing deployments.

## Benchmark Results (1,857 blocks, 4.4M tokens)

| Metric      | json_blocks | sqlite    | Δ        |
|-------------|-------------|-----------|----------|
| Cold load   | ~1.3 s      | ~5.5 s    | ▲ 317%   |
| Warm reload | ~1.2 s      | **1.1 ms**| ▼ 99.9%  |
| Search p50  | ~8.6 ms     | ~12.9 ms  | ▲ 50%    |
| Search p95  | ~9.8 ms     | ~27.9 ms  | ▲ 186%   |

### Key Takeaways

- **Warm reload** (no index change): SQLite is **~1100× faster**. This is the primary win — `proxy_v4` checks for index updates every 5 minutes. With `json_blocks`, every check re-scans all block files. With SQLite, it's a single mtime comparison.
- **Cold load** (DB not yet built): SQLite is slower on first build. The DB file is persisted at `<vault_index>/retrieval.db`.
- **Search latency**: SQLite is ~1.5–2× slower than in-memory BM25. Acceptable for vault injection (non-critical path).

### When to Use SQLite

- Index > 5,000 blocks
- High reload frequency or many proxy restarts
- Shared vault across multiple processes (WAL mode)
- Memory-constrained environments (content not held in RAM during non-search time)

### When to Keep json_blocks

- Current scale (<3k blocks, fast reload)
- Simplicity requirement — no DB file on disk
- Frequent content changes (SQLite build cost amortizes less well)

## Architecture

```
TOKENPAK_VAULT_INDEX/
├── index.json          ← block metadata (always required)
├── blocks/             ← block content files (always required)
│   └── <block_id>.txt
└── retrieval.db        ← SQLite DB (created by SQLite backend)
    ├── blocks          ← block metadata + content (denormalised)
    ├── block_terms     ← TF per block per term (indexed on term)
    ├── doc_stats       ← doc_count, avg_dl (BM25 corpus stats)
    └── meta            ← index_mtime checkpoint
```

### Incremental Update Strategy

On each `maybe_reload()` call:
1. Compare `index.json` mtime vs `meta.index_mtime` checkpoint.
2. If unchanged → skip (1 ms).
3. If changed → diff new block IDs vs existing DB, upsert changed/new blocks, delete removed blocks, update corpus stats.

Only blocks that changed (new/deleted) are reprocessed — unchanged blocks are left in place.

## Fallback Behaviour

If the SQLite backend fails to import or errors on build, `proxy_v4` automatically falls back to `json_blocks`:

```
⚠️  SQLite retrieval backend unavailable (<error>), falling back to json_blocks
```

No data loss or service interruption occurs.

## Health / Metrics

The `/health` and `/stats` endpoints include `vault_index.blocks` count regardless of backend.

Prometheus metric:
```
tokenpak_vault_blocks <N>
```

## Files Changed

| File | Change |
|------|--------|
| `proxy_v4.py` | Added `TOKENPAK_RETRIEVAL_BACKEND` env var + backend-aware `VAULT_INDEX` init |
| `tokenpak/agent/vault/sqlite_retrieval.py` | New SQLite backend module |
| `benchmark_retrieval.py` | New benchmark script |
