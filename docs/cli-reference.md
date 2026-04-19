---
title: "CLI Reference"
created: 2026-04-01
---
# CLI Reference

TokenPak provides a comprehensive command-line interface for managing the proxy, monitoring costs, and debugging compression.

**Last verified:** April 1, 2026

---

## Quick Reference

| Command | Description | Status |
|---------|-------------|--------|
| `start` | Start the proxy server | ✅ Stable |
| `stop` | Stop the running proxy | ✅ Stable |
| `status` | Check proxy health and stats | ✅ Stable |
| `cost` | View API spend breakdown | ✅ Stable |
| `savings` | View compression savings | ✅ Stable |
| `doctor` | Run diagnostics and auto-fix | ✅ Stable |
| `dashboard` | Real-time health dashboard | ✅ Stable |
| `compress` | Compress text/JSON/code directly | ✅ Stable |
| `preview` | Dry-run compression (see savings) | ✅ Stable |
| `demo` | See compression in action | ✅ Stable |
| `index` | Index a directory for vault injection | ✅ Stable |
| `vault` | Vault index health and repair | ✅ Stable |
| `config` | Config management (sync, validate) | ✅ Stable |
| `template` | Manage prompt templates | ✅ Stable |
| `route` | Manage provider routing rules | ✅ Stable |
| `version` | Show version info | ✅ Stable |
| `diff` | Show context changes | ⚠️ Experimental |
| `fingerprint` | Fingerprint sync and cache | ⚠️ Experimental |
| `optimize` | Optimize prompts for compression | ⚠️ Experimental |
| `last` | Show details of last request | ✅ Stable |
| `replay` | Replay recorded requests | ⚠️ Experimental |
| `fleet` | Fleet management | ⚠️ Experimental |

---

## Proxy Management

### `tokenpak start`

Start the TokenPak proxy server.

```bash
tokenpak start [--port PORT] [--workers N] [--log-level LEVEL]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8766` | Port to listen on |
| `--workers` | `2` | Number of worker processes |
| `--log-level` | `info` | Logging level (`debug`, `info`, `warning`, `error`) |

```bash
# Default start
tokenpak start

# Custom port with debug logging
tokenpak start --port 9000 --log-level debug

# Background mode
nohup tokenpak start > ~/.tokenpak/proxy.log 2>&1 &
```

!!! note "`serve` is an alias for `start`"
    Both commands start the proxy. Use `start` in new setups.

### `tokenpak stop`

Stop the running proxy process.

```bash
tokenpak stop
```

### `tokenpak status`

Check proxy health, uptime, and recent request stats.

```bash
tokenpak status [--limit N]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | `5` | Max retry events to show |

```bash
tokenpak status
```

Example output:
```
TOKENPAK v1.0.3  |  Status
────────────────────────────────────────
● Proxy: running (port 8766)
  Uptime:          2h 14m
  Requests:        247
  Tokens saved:    12,841 (38.4%)
  Cost:            $0.018
```

### `tokenpak version`

Show the installed TokenPak version and proxy version.

```bash
tokenpak version
```

---

## Cost & Savings

### `tokenpak cost`

View API spend breakdown by model, provider, or time period.

```bash
tokenpak cost [--week] [--month] [--by-model] [--export-csv]
tokenpak cost show-budget
```

| Flag | Description |
|------|-------------|
| `--week` | Show last 7 days |
| `--month` | Show last 30 days |
| `--by-model` | Group costs by model |
| `--export-csv` | Export as CSV |

```bash
# Daily spend
tokenpak cost

# Weekly breakdown by model
tokenpak cost --week --by-model

# View budget status
tokenpak cost show-budget
```

### `tokenpak savings`

View compression and caching savings over a rolling window.

```bash
tokenpak savings [--days N]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days` | `1` | Rolling window in days |

```bash
# Today's savings
tokenpak savings

# Last 7 days
tokenpak savings --days 7
```

---

## Diagnostics

### `tokenpak doctor`

Run diagnostics and optionally auto-fix common issues.

```bash
tokenpak doctor [--fix] [--json] [--fleet] [--deploy] [--verbose]
```

| Flag | Description |
|------|-------------|
| `--fix` | Auto-fix issues where possible |
| `--json` | Output as JSON |
| `--fleet` | Check all fleet nodes |
| `--deploy` | Run deployment checks |
| `--verbose` | Show detailed output |

```bash
# Quick health check
tokenpak doctor

# Auto-fix and show details
tokenpak doctor --fix --verbose
```

### `tokenpak dashboard`

Launch a real-time health dashboard.

```bash
tokenpak dashboard [--fleet] [--json] [--public] [--show-token] [--new-token]
```

| Flag | Description |
|------|-------------|
| `--fleet` | Show fleet-wide metrics |
| `--json` | Output as JSON |
| `--public` | Allow external access |
| `--show-token` | Display auth token |
| `--new-token` | Generate new auth token |

Also available in-browser at `http://localhost:8766/dashboard`.

---

## Compression Tools

### `tokenpak compress`

Compress text, JSON, or code directly to see token savings.

```bash
tokenpak compress [--file FILE] [--verbose] [--json]
```

| Flag | Description |
|------|-------------|
| `--file` | Read input from file |
| `--verbose` | Show detailed compression stats |
| `--json` | Output as JSON |

```bash
# Compress from stdin
echo "Your long prompt here" | tokenpak compress

# Compress a file
tokenpak compress --file prompt.md --verbose
```

### `tokenpak preview`

Dry-run compression to see what would change without sending to a provider.

```bash
tokenpak preview [--file FILE] [--raw] [--verbose] [--json] [INPUT]
```

| Flag | Description |
|------|-------------|
| `--file` | Read input from file |
| `--raw` | Show raw compressed output |
| `--verbose` | Show token counts per block |
| `--json` | Output as JSON |

```bash
# Preview compression on a file
tokenpak preview --file system-prompt.md

# Preview with token counts
tokenpak preview --file prompt.txt --verbose
```

### `tokenpak optimize`

Analyze a prompt and suggest optimizations for better compression.

```bash
tokenpak optimize [--file FILE] [--strategy STRATEGY] [--show-diff]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--file` | stdin | Input file |
| `--strategy` | `balanced` | Strategy: `conservative`, `balanced`, `aggressive` |
| `--show-diff` | off | Show before/after diff |

### `tokenpak demo`

See compression in action with built-in example prompts.

```bash
tokenpak demo [--list] [--category CAT] [--recipe NAME] [--file FILE]
```

| Flag | Description |
|------|-------------|
| `--list` | List available demo recipes |
| `--category` | Filter by category |
| `--recipe` | Run a specific recipe |
| `--file` | Use your own file |

```bash
# List demo categories
tokenpak demo --list

# Run a specific demo
tokenpak demo --recipe code-review
```

---

## Vault & Indexing

### `tokenpak index`

Index a directory for vault injection (BM25 + optional vector search).

```bash
tokenpak index [--status] [--budget N] [--workers N] [--watch] [--no-treesitter]
```

| Flag | Description |
|------|-------------|
| `--status` | Show current index stats |
| `--budget` | Max tokens per injection |
| `--workers` | Number of indexing workers |
| `--watch` | Watch for changes and re-index |
| `--no-treesitter` | Skip AST parsing |

```bash
# Index the current directory
tokenpak index

# Watch for changes
tokenpak index --watch

# Check index status
tokenpak index --status
```

### `tokenpak vault`

Check vault index health and repair stale entries.

```bash
tokenpak vault repair
tokenpak vault status
```

---

## Configuration

### `tokenpak config`

Manage TokenPak configuration.

```bash
tokenpak config {sync|pull|validate|show|init|path|migrate}
```

| Subcommand | Description |
|------------|-------------|
| `sync` | Sync config from canonical source |
| `pull` | Pull latest config |
| `validate` | Validate current config |
| `show` | Show active config |
| `init` | Initialize default config |
| `path` | Show config file path |
| `migrate` | Migrate config between versions |

!!! note "Environment variables are canonical"
    All proxy settings can be set via environment variables (see [Configuration](./configuration.md)).
    Config files are optional and env vars always take precedence.

---

## Routing

### `tokenpak route`

Manage provider routing rules.

```bash
tokenpak route {add|list|remove|show}
```

```bash
# List current routes
tokenpak route list

# Add a route
tokenpak route add --model "claude-*" --provider anthropic
```

---

## Templates

### `tokenpak template`

Manage reusable prompt templates.

```bash
tokenpak template {list|add|show|remove|use}
```

```bash
# List templates
tokenpak template list

# Add a template
tokenpak template add --name code-review --file template.md

# Use a template
tokenpak template use code-review
```

---

## Advanced Commands

### `tokenpak diff`

Show context changes between requests. ⚠️ Experimental.

```bash
tokenpak diff [--verbose] [--json] [--since TIMESTAMP]
```

### `tokenpak last`

Show details of the most recent request processed by the proxy.

```bash
tokenpak last [--limit N] [--json] [--verbose]
```

### `tokenpak replay`

Replay previously recorded requests for testing or debugging. ⚠️ Experimental.

```bash
tokenpak replay [--file FILE] [--dry-run]
```

### `tokenpak fingerprint`

Manage fingerprint sync and caching. ⚠️ Experimental.

```bash
tokenpak fingerprint {sync|cache|clear-cache}
```

### `tokenpak fleet`

Fleet management for multi-node deployments. ⚠️ Experimental.

```bash
tokenpak fleet {status|sync|health}
```

---

## HTTP Endpoints

The proxy also exposes these HTTP endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (`{"status": "healthy"}`) |
| `/stats` | GET | Request stats and cache metrics |
| `/savings` | GET | Compression savings data |
| `/metrics` | GET | Prometheus-compatible metrics |
| `/version` | GET | Version and build info |
| `/dashboard` | GET | Web dashboard (browser) |
| `/v1/messages` | POST | Anthropic-compatible proxy endpoint |
| `/v1/chat/completions` | POST | OpenAI-compatible proxy endpoint |

---

## Global Options

```bash
tokenpak --help     # Show all commands
tokenpak --version  # Show version
```

All commands support `--help` for detailed usage:

```bash
tokenpak <command> --help
```
