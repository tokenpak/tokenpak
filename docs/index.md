# TokenPak

> **The open logistics layer for AI context.**

TokenPak is a local proxy for coding agents that records each request and shows how far the session can go: measured usage, estimated cost, burn and runway.

**Know how far your agent can go.** Today, the open-source core ships the session trip computer (on by default in the Claude Code footer, the Codex pane and `tokenpak status`) and Spend Guard limits. Forecasts are estimates shown as ranges, not guarantees, and appear only for a model and effort combination with enough local history; the rest show `learning`. Calibrated forecasts for more model and effort combinations, and Pro reroute recommendations once calibration evidence exists, are planned.

**Who it's for:** Developers and tech leads running long coding-agent sessions in Claude Code or Codex who want to see what a session has used, what finishing will likely cost, and how far it can go.

The default proxy preserves conversation turns; a forwarded request can correctly report zero tokens saved. Explicit compression operations can reduce eligible content. Provider-bound requests still go to your chosen provider, with no TokenPak cloud relay.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## Why TokenPak?

LLM APIs charge per token. Install TokenPak, launch your agent through it, and get a measured receipt on the first request, then see your session's usage and runway as you work, without changing your code.

| Metric | Value |
|--------|-------|
| Average token reduction | **measure on your workload with `tokenpak savings`** |
| Local operations | **status, search, cost, and route checks read local state** |
| Cold start overhead | **< 100ms** |
| Indexing throughput | **2,700+ files/sec** |

---

## Core Principles

=== "Local Data"
 Prompts are handled by your local proxy, then forwarded only to your configured provider.

=== "Zero Credentials"
 Pure passthrough proxy — your API keys go directly to providers and are not stored by TokenPak.

=== "Zero Lock-in"
 Downgrade anytime. Keep all your data. No vendor dependencies.

=== "Zero Tokens for Ops"
 Status, search, and cost reports read local state instead of calling the LLM.

---

## Quick Start

```bash
pip install tokenpak
tokenpak serve --port 8766
```

Then point your LLM client at `http://localhost:8766` (OpenAI-compatible clients: `http://localhost:8766/v1`). That's it. See [Getting Started](getting-started.md) for the full walkthrough.

---

## What's Inside

<div class="grid cards" markdown>

- :material-fast-forward: **[Getting Started](getting-started.md)**

 Install TokenPak and record your first measured request in 5 minutes.

- :material-console: **[CLI Reference](cli-reference.md)**

 Every command, every flag, with examples.

- :material-lan: **[Proxy Setup](guides/proxy-setup.md)**

 Connect Claude Code, Codex, or the OpenAI SDK, Anthropic SDK and LiteLLM adapters. Cursor, Cline, Continue.dev and Aider are compatibility targets, not yet independently verified.

- :material-chef-hat: **[Recipe Development](guides/recipes.md)**

 Build custom compression recipes for your domain.

- :material-chart-bar: **[Telemetry & Dashboard](guides/telemetry.md)**

 Track costs, view savings, export reports.

- :material-server: **[Team Server](guides/team-server.md)**

 Planned architecture for a shared proxy, not a shipped feature.

</div>

---

## Documentation Reference

### Quick Links

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | 5-minute setup guide |
| [CLI Reference](cli-reference.md) | All commands and flags |
| [REST API Reference](REST_API.md) | REST endpoints |
| [Embedding API Reference](embedding-api-reference.md) | Embedding proxy: providers, failover, env vars |
| [Python SDK Reference](API_REFERENCE.md) | Python API for programmatic use |
| [Runnable Examples](https://github.com/tokenpak/tokenpak/blob/main/examples/README.md) | Clone/download path for examples used with a package install |
| [FAQ](faq.md) | Common questions and troubleshooting |

---

### Architecture & Design

| Document | What It Covers |
|----------|----------------|
| [ARCHITECTURE.md](architecture.md) | System design, compression pipeline, block registry |
| [Compression Deep Dive](compression.md) | How compression works, modes, recipes |
| [Cache System](cache.md) | LRU cache, vault registry, change detection |

### Operations & Deployment

| Document | What It Covers |
|----------|----------------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production deployment, systemd, Docker, scaling |
| [Troubleshooting](troubleshooting.md) | Symptom-first problem solving with copy-paste commands |
| [Error Codes](errors.md) | Full error code reference (TP-Exxx) |
| [Telemetry](telemetry.md) | Cost tracking, privacy model, data retention |
| [FAQ](faq.md) | Common questions, budget, log reading, vault |

### Guides

| Guide | What You'll Learn |
|-------|-------------------|
| [Proxy Setup](guides/proxy-setup.md) | Multi-provider routing, SSL, authentication |
| [Recipe Development](guides/recipes.md) | Custom compression recipes |
| [Telemetry Dashboard](guides/telemetry.md) | Cost reports, export, alerts |
| [Team Server](guides/team-server.md) | Planned shared-proxy architecture (not shipped) |

---

### CLI Reference

```bash
# Core
tokenpak serve # Start proxy
tokenpak status # Health check
tokenpak cost # Cost report
tokenpak savings # Token savings

# Compression
tokenpak compress # Dry-run compression
tokenpak demo # Offline fixture demo
tokenpak debug list # View recent request traces

# Vault
tokenpak index # Index directory
tokenpak vault search # Semantic search
tokenpak calibrate # Auto-tune performance

# Routing
tokenpak route add # Add routing rule
tokenpak route list # List rules
```

See [CLI Reference](cli-reference.md) for complete documentation.

### Python SDK

```python
from tokenpak import (
 TelemetryCollector, # Usage tracking
 CacheManager, # Token cache
 CompressionEngine, # Compression base class
 HeuristicEngine, # Rule-based compression
 Budgeter, # Token budget allocation
 BlockRegistry, # Content-addressed storage
)
```

See [Python SDK Reference](API_REFERENCE.md) for full class documentation.

---

### Contributing

| Document | What It Covers |
|----------|----------------|
| [CONTRIBUTING.md](https://github.com/tokenpak/tokenpak/blob/main/CONTRIBUTING.md) | Development setup, testing, PR process |
| [Recipe SDK](guides/recipes.md) | Building custom compression recipes |

Quick dev setup:

```bash
git clone https://github.com/tokenpak/tokenpak
cd tokenpak
pip install -e ".[dev]"
pytest
```

---

### Performance

| Metric | Value |
|--------|-------|
| Average token reduction | **measure on your workload with `tokenpak savings`** |
| Local operations | **status, search, cost, and route checks read local state** |
| Indexing throughput | **2,700+ files/sec** |
| Search latency | **~23ms** |
| Cold start overhead | **< 100ms** |

See [ARCHITECTURE.md](architecture.md) for benchmarks.

---

### External Links

- **GitHub:** [github.com/tokenpak/tokenpak](https://github.com/tokenpak/tokenpak)
- **PyPI:** [pypi.org/project/tokenpak](https://pypi.org/project/tokenpak/)
- **Issues:** [GitHub Issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [GitHub Discussions](https://github.com/tokenpak/tokenpak/discussions)
