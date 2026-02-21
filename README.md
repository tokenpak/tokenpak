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
- CLI for indexing/search/stats

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

### Serve (proxy passthrough to existing OpenClaw .ocp proxy)

```bash
tokenpak serve --port 8766
```

## Benchmark Results

Tested with QMD retrieval + TokenPak compaction:

| Configuration | Avg tokens/req | Reduction |
|---|---:|---:|
| Baseline (no optimization) | 20,801 | — |
| QMD only | 6,136 | 70% |
| QMD + TokenPak | 3,265 | 84% |

Consistent **~43% additional savings** on top of QMD across writing, coding, legal, and ops tasks.

## Notes

- Registry DB default: `.tokenpak/registry.db`
- Uses stdlib only by default.
- Optional: install `tiktoken` for accurate token counting.
- Optional: install `llmlingua` for ML-powered compression.
