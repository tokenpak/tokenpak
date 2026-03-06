# TokenPak

> **Zero-token operations. Maximum context efficiency.**

TokenPak is an open-source LLM proxy that compresses context, routes requests intelligently, and tracks costs — all without touching your prompts or credentials.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Why TokenPak?

LLM APIs charge per token. Most conversations are bloated with repetitive context, verbose code comments, and redundant structure. TokenPak fixes that at the proxy layer — transparently, locally, without ever seeing your content.

| Metric | Value |
|--------|-------|
| Average token reduction | **43–84%** |
| Zero-token operations | **80%+** |
| Cold start overhead | **< 100ms** |
| Indexing throughput | **2,700+ files/sec** |

---

## Core Principles

=== "Zero Data"
    We never see your prompts, code, or responses. Everything happens locally.

=== "Zero Credentials"
    Pure passthrough proxy — your API keys go directly to providers, never stored by TokenPak.

=== "Zero Lock-in"
    Downgrade anytime. Keep all your data. No vendor dependencies.

=== "Zero Tokens for Ops"
    Status, search, cost reports — all free. CLI-first, deterministic.

---

## Quick Start

```bash
pip install tokenpak
tokenpak serve --port 8766
```

Then point your LLM client at `http://localhost:8766`. That's it. See [Getting Started](getting-started.md) for the full walkthrough.

---

## What's Inside

<div class="grid cards" markdown>

-   :material-fast-forward: **[Getting Started](getting-started.md)**

    Install TokenPak and run your first compressed request in 5 minutes.

-   :material-console: **[CLI Reference](cli-reference.md)**

    Every command, every flag, with examples.

-   :material-lan: **[Proxy Setup](guides/proxy-setup.md)**

    Connect Claude Code, OpenAI clients, or any HTTP-based LLM tool.

-   :material-chef-hat: **[Recipe Development](guides/recipes.md)**

    Build custom compression recipes for your domain.

-   :material-chart-bar: **[Telemetry & Dashboard](guides/telemetry.md)**

    Track costs, view savings, export reports.

-   :material-server: **[Team Server](guides/team-server.md)**

    Deploy a shared TokenPak instance for your whole team.

</div>
