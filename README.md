# TokenPak

**Universal content compiler for LLM context optimization.**

Point TokenPak at any directory. Get optimized LLM context from every file type.

## Features

### Core (v0.1.0)
- Directory indexing with file-type detection
- SQLite-backed block registry with versioning + change detection
- Processors for text, code, and structured data
- Quadratic budget allocation (importance-weighted)
- TOKPAK wire format output with provenance
- CLI for indexing/search/stats/benchmark

### Compaction Engines
- **Heuristic** (default): Fast rule-based, no ML dependencies
- **LLMLingua**: ML-powered (requires `pip install llmlingua`)

### Platform Connectors
- **Free**: Local filesystem, Obsidian vaults
- **Pro** (planned): Google Drive, Notion, GitHub
- **Enterprise** (planned): OneDrive, SharePoint, Confluence, Slack

## Install

```bash
pip install -e .
```

## Usage

### Index a directory

```bash
tokenpak index ~/vault
```

### Search indexed content and emit wire format

```bash
tokenpak search "token compression benchmark" --budget 8000 --top-k 8
```

### Show stats

```bash
tokenpak stats
```

### Run latency benchmark

```bash
tokenpak benchmark ~/vault --iterations 3
```

### Serve (proxy passthrough to existing OpenClaw .ocp proxy)

```bash
tokenpak serve --port 8766
```

## Performance

### Latency Optimizations (v0.1.1)

| Optimization | Component | Improvement |
|---|---|---|
| LRU token cache | `tokens.py` | **25x** faster repeated counting |
| Lazy tiktoken loading | `tokens.py` | ~100ms saved on cold start |
| Batch SQLite transactions | `registry.py` | **60%** faster indexing |
| Connection pooling + WAL | `registry.py` | Reduced I/O overhead |
| Pre-compiled regex | `processors/*.py` | **30%** faster processing |

### Benchmark Results (572-file vault)

```
Token cache speedup: 26.6x
Indexing throughput: 2,738 files/sec
Indexing speedup vs baseline: 55.27x (98.2% faster)
Search latency: 22.7ms/query
Processing: 0.09-0.19ms/file (code/text)
```

### Parallel Indexing

```bash
tokenpak index ~/vault --workers 4
```

### Token Savings (QMD + TokenPak)

| Configuration | Avg tokens/req | Reduction |
|---|---:|---:|
| Baseline (no optimization) | 20,801 | — |
| QMD only | 6,136 | 70% |
| QMD + TokenPak | 3,265 | **84%** |

Consistent **~43% additional savings** on top of QMD across writing, coding, legal, and ops tasks.

## Architecture

```
tokenpak/
├── cli.py          # CLI commands (index, search, stats, benchmark)
├── registry.py     # SQLite registry with connection pooling + batch writes
├── tokens.py       # Token counting with LRU cache + lazy loading
├── walker.py       # Directory traversal + file type detection
├── budget.py       # Quadratic budget allocation
├── wire.py         # TOKPAK wire format packing
├── benchmark.py    # Latency benchmarking suite
├── processors/
│   ├── code.py     # Python/JS structure extraction
│   ├── text.py     # Markdown/HTML compression
│   └── data.py     # JSON/YAML/CSV handling
├── engines/
│   ├── heuristic.py  # Rule-based compaction
│   └── llmlingua.py  # ML-powered compaction (optional)
└── connectors/
    ├── local.py      # Local filesystem
    └── obsidian.py   # Obsidian vault awareness
```

## Notes

- Registry DB default: `.tokenpak/registry.db`
- Uses stdlib only by default.
- Optional: install `tiktoken` for accurate token counting.
- Optional: install `llmlingua` for ML-powered compression.
