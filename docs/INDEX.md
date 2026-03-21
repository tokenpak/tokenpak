# TokenPak Documentation Index

> Complete documentation for TokenPak — the open-source LLM proxy for context compression, cost tracking, and intelligent routing.

---

## 📚 Quick Links

| Document | Description |
|----------|-------------|
| [README](../README.md) | Project overview, installation, quick start |
| [Getting Started](getting-started.md) | 5-minute setup guide |
| [CLI Reference](cli-reference.md) | All commands and flags |
| [API Reference](API.md) | Python API for programmatic use |

---

## 🚀 Getting Started

1. **[Installation](getting-started.md#install)** — pip, source, Docker
2. **[Start the Proxy](getting-started.md#start-the-proxy)** — `tokenpak serve`
3. **[Connect Your Client](getting-started.md#connect-your-llm-client)** — Claude Code, OpenAI, etc.
4. **[Verify It Works](getting-started.md#verify-its-working)** — `tokenpak status`

---

## 📖 Core Documentation

### Architecture & Design

| Document | What It Covers |
|----------|----------------|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | System design, compression pipeline, block registry |
| [Compression Deep Dive](compression.md) | How compression works, modes, recipes |
| [Cache System](cache.md) | LRU cache, vault registry, change detection |

### Operations & Deployment

| Document | What It Covers |
|----------|----------------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production deployment, systemd, Docker, scaling |
| [Telemetry](telemetry.md) | Cost tracking, privacy model, data retention |
| [FAQ](faq.md) | Common questions and troubleshooting |

### Guides

| Guide | What You'll Learn |
|-------|-------------------|
| [Proxy Setup](guides/proxy-setup.md) | Multi-provider routing, SSL, authentication |
| [Recipe Development](guides/recipes.md) | Custom compression recipes |
| [Telemetry Dashboard](guides/telemetry.md) | Cost reports, export, alerts |
| [Team Server](guides/team-server.md) | Shared instance for teams |

---

## 🔧 Reference

### CLI Commands

```bash
# Core
tokenpak serve           # Start proxy
tokenpak status          # Health check
tokenpak cost            # Cost report
tokenpak savings         # Token savings

# Compression
tokenpak compress        # Dry-run compression
tokenpak demo            # Live demo
tokenpak trace           # Debug pipeline

# Vault
tokenpak index           # Index directory
tokenpak vault search    # Semantic search
tokenpak calibrate       # Auto-tune performance

# Routing
tokenpak route add       # Add routing rule
tokenpak route list      # List rules
```

See [CLI Reference](cli-reference.md) for complete documentation.

### Python API

```python
from tokenpak import (
    TelemetryCollector,    # Usage tracking
    CacheManager,          # Token cache
    CompressionEngine,     # Compression base class
    HeuristicEngine,       # Rule-based compression
    Budgeter,              # Token budget allocation
    BlockRegistry,         # Content-addressed storage
)
```

See [API Reference](API.md) for full class documentation.

---

## 🤝 Contributing

| Document | What It Covers |
|----------|----------------|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Development setup, testing, PR process |
| [Recipe SDK](recipe-sdk.md) | Building custom compression recipes |

### Quick Dev Setup

```bash
git clone https://github.com/tokenpak/tokenpak
cd tokenpak
pip install -e ".[dev]"
pytest
```

---

## 📊 Performance

| Metric | Value |
|--------|-------|
| Token reduction | 43–84% |
| Indexing throughput | 2,700+ files/sec |
| Search latency | ~23ms |
| Cold start | < 100ms |

See [ARCHITECTURE.md](../ARCHITECTURE.md) for benchmarks.

---

## 🔗 External Links

- **GitHub:** [github.com/tokenpak/tokenpak](https://github.com/tokenpak/tokenpak)
- **PyPI:** [pypi.org/project/tokenpak](https://pypi.org/project/tokenpak/)
- **Issues:** [GitHub Issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [GitHub Discussions](https://github.com/tokenpak/tokenpak/discussions)

---

## 📄 License

TokenPak is released under the [MIT License](../LICENSE).
