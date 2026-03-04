# TokenPak

> **Zero-token operations. Maximum context efficiency.**

TokenPak is an open-source LLM proxy agent that compresses context, routes requests intelligently, and tracks costs — all without touching your prompts or credentials.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## What It Does

- **Compresses context** before it hits the API — fewer tokens, lower cost
- **Routes requests** to the right model (fast/cheap vs. powerful/expensive)
- **Tracks costs** locally — per model, per session, per agent
- **Indexes your vault** for instant semantic search without an LLM call
- **80%+ of operations cost zero tokens** — CLI-first, deterministic

## Core Principles

| Principle | What it means |
|-----------|---------------|
| **Zero Data** | We never see your prompts, code, or responses |
| **Zero Credentials** | Pure passthrough proxy — no API keys stored |
| **Zero Lock-in** | Downgrade anytime; keep all your data |
| **Zero Tokens for Ops** | Status, search, cost reports — all free |

---

## Quick Start

### Install

```bash
pip install tokenpak
# or from source:
git clone https://github.com/tokenpak/tokenpak && cd tokenpak
pip install -e .
```

### Configure your LLM client

Point your existing tool (Claude Code, OpenAI client, etc.) at the TokenPak proxy:

```bash
# Start the proxy
tokenpak serve --port 8766

# In your LLM client, set base URL to:
# http://localhost:8766
```

Your credentials pass through unchanged. TokenPak never stores them.

### Index your vault (optional, zero tokens)

```bash
tokenpak index ~/vault
tokenpak vault search "compression benchmark"
```

### Hybrid auto-calibration (recommended)

```bash
# one-time static calibration for this host
tokenpak calibrate ~/vault --max-workers 8 --rounds 2

# normal indexing with dynamic adjustment around calibrated baseline
tokenpak index ~/vault --auto-workers --max-workers 8
```

### Check costs

```bash
tokenpak cost --week
tokenpak cost --by-model
```

---

## CLI Reference

### Status & Health

```bash
tokenpak status [--full]               # proxy health
tokenpak health                        # full system health
tokenpak logs [--errors] [--today]     # proxy logs
tokenpak doctor                        # comprehensive diagnostics
```

### Cost & Telemetry

```bash
tokenpak cost [--week|--month|--by-model|--by-agent|--export csv]
tokenpak budget set --monthly 50       # set $50/month budget
tokenpak budget alert --at 80%         # alert at 80% usage
tokenpak savings [--lifetime]
```

### Compression

```bash
tokenpak demo [--verbose]              # see pipeline on real data
tokenpak compress <file> [--diff]      # dry-run compression
tokenpak trace [--id <id>]             # trace a pipeline run
```

### Vault & Indexing

```bash
tokenpak index [<path>]                # index a directory
tokenpak index --watch                 # auto re-index on changes
tokenpak index --status                # check index health
tokenpak vault search "query"          # semantic search
tokenpak vault blocks [--stale]        # inspect content blocks
```

### Benchmarking & Calibration

```bash
tokenpak benchmark ~/vault --iterations 3
tokenpak benchmark ~/vault --compare   # baseline vs optimized
tokenpak calibrate ~/vault --max-workers 8 --rounds 2
```

### Model Routing

```bash
tokenpak route set ".*test.*" gpt-4o-mini   # route test queries to cheaper model
tokenpak route test "write unit tests"       # preview routing decision
tokenpak route history                        # recent routing decisions
```

### Agent Management

```bash
tokenpak agent list                    # list registered agents
tokenpak agent register <name>         # register an agent
tokenpak agent tasks --queue           # pending tasks
tokenpak agent lock <file>             # acquire a file lock
```

### Event Triggers

```bash
tokenpak trigger list
tokenpak trigger add file-change "*.py" "bash lint.sh"
tokenpak trigger add cost-alert 80% "notify"
tokenpak trigger log
```

### A/B Testing

```bash
tokenpak ab create my-test --variant-a "compress aggressive" --variant-b "compress minimal"
tokenpak ab status my-test
tokenpak ab apply my-test
tokenpak ab presets
```

### Replay & Debug

```bash
tokenpak replay list
tokenpak replay <id> --no-compress
tokenpak replay <id> --model gpt-4o-mini
tokenpak replay <id> --diff
tokenpak debug on [--requests 50]
tokenpak debug off
```

### Templates

```bash
tokenpak template list
tokenpak template create my-tpl
tokenpak template use my-tpl
tokenpak template export my-tpl
```

### Configuration & Maintenance

```bash
tokenpak config set compression.enabled true
tokenpak config get compression.level
tokenpak config export
tokenpak prune --older-than 30d
```

---

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

### Token Savings (QMD + TokenPak)

| Configuration | Avg tokens/req | Reduction |
|---|---:|---:|
| Baseline (no optimization) | 20,801 | — |
| QMD only | 6,136 | 70% |
| QMD + TokenPak | 3,265 | **84%** |

Consistent **~43% additional savings** on top of QMD across writing, coding, legal, and ops tasks.

---

## How Compression Works

TokenPak intercepts requests before they reach the LLM and applies a multi-stage pipeline:

1. **Segmentize** — split content into semantic blocks
2. **Fingerprint** — identify block type (code, docs, config…)
3. **Apply recipe** — use declarative rules to compress that block type
4. **Budget** — allocate tokens using a quadratic priority algorithm
5. **Assemble** — reconstruct the compressed prompt

Result: same semantic content, 20–60% fewer tokens.

---

## Directory Structure

```
tokenpak/
├── agent/
│   ├── agentic/          # multi-agent coordination (locks, retry)
│   ├── cli/              # entry point + command modules
│   ├── compression/      # pipeline, segmentizer, recipes, directives
│   ├── proxy/            # request routing + streaming
│   ├── telemetry/        # cost tracking, storage, demo
│   └── vault/            # indexer, ast_parser, symbols, blocks
├── recipes/
│   └── oss/              # built-in compression recipes (YAML)
├── processors/
│   ├── code.py           # Python/JS structure extraction
│   ├── text.py           # Markdown/HTML compression
│   └── data.py           # JSON/YAML/CSV handling
├── engines/
│   ├── heuristic.py      # Rule-based compaction
│   └── llmlingua.py      # ML-powered compaction (optional)
├── connectors/
│   ├── local.py          # Local filesystem
│   └── obsidian.py       # Obsidian vault awareness
├── tests/
└── pyproject.toml
```

---

## Configuration

Default config: `~/.tokenpak/config.json`

```json
{
  "proxy": {
    "port": 8766,
    "passthrough_url": "https://api.openai.com"
  },
  "compression": {
    "enabled": true,
    "level": "balanced"
  },
  "budget": {
    "monthly_usd": null,
    "alert_at_pct": 80
  },
  "vault": {
    "db_path": ".tokenpak/registry.db",
    "watch": false
  }
}
```

- Registry DB default: `.tokenpak/registry.db`
- Calibration profile path: `~/.tokenpak/calibration.json`

---

## Requirements

- Python 3.11+
- No external dependencies for core functionality
- Optional: `tiktoken` for accurate token counting
- Optional: `llmlingua` for ML-powered compression

---

## Contributing / Dev Workflow

### Pushing code (dual-remote setup)

TokenPak has two remotes: `origin` (GitHub) and `shared` (SueBot QA repo).
**Always use the verification script** to ensure both land:

```bash
bash scripts/push-verified.sh [branch]
```

This will:
1. Push to `origin` and verify the commit hash landed
2. Push to `shared` (SueBot's QA repo) and SSH-verify the hash matches
3. Exit non-zero if either push fails — safe to use in CI or pre-push hooks

> ⚠️ Do NOT push with bare `git push origin` — the shared remote will be skipped and Sue's QA will fail.

Issues and PRs welcome. See [ARCHITECTURE.md](ARCHITECTURE.md) for system design.

---

## License

MIT — see [LICENSE](LICENSE)
