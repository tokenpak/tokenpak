# Telemetry & Dashboard Guide

Track costs, view savings, and understand your LLM usage — all locally.

---

## Overview

TokenPak records every request locally in a SQLite database. No data leaves your machine. The telemetry system provides:

- **Cost tracking** — per-model, per-session, per-agent breakdowns
- **Token savings** — what compression saved you (tokens and $)
- **Session history** — replay any past request
- **Export** — CSV/JSON for your own analysis

---

## Cost Tracking

### Quick View

```bash
tokenpak cost --today
# Today: $0.042 | 23 requests | 18,341 tokens | saved 37%

tokenpak cost --week
tokenpak cost --month
```

### Breakdowns

```bash
# By model
tokenpak cost --by-model
# anthropic/claude-3-5-sonnet   $0.031   12 requests
# openai/gpt-4o-mini             $0.011   11 requests

# By agent (if agents are registered)
tokenpak cost --by-agent
# cali        $0.024   14 requests
# trix        $0.018    9 requests
```

### Export

```bash
tokenpak cost --month --export csv > costs-march.csv
tokenpak cost --month --export json > costs-march.json
```

---

## Savings Reports

```bash
tokenpak savings
# This month: saved 142,000 tokens (~$0.71) via compression

tokenpak savings --lifetime
# Lifetime: saved 2.1M tokens (~$10.50) across 847 requests
```

---

## Budget Management

### Set Limits

```bash
tokenpak budget set --monthly 50   # $50/month hard limit
tokenpak budget alert --at 80%     # warn at $40 spent
```

When you hit 80%, TokenPak logs a warning. At 100%, it can either block requests or switch to a cheaper model (configurable):

```json
{
  "budget": {
    "monthly_usd": 50,
    "alert_at_pct": 80,
    "on_exceeded": "warn"   // "warn" | "block" | "route-cheap"
  }
}
```

### Check Status

```bash
tokenpak budget status
# Monthly: $50.00 limit | $23.40 spent (46.8%) | $26.60 remaining
# Alert at: $40.00 | On exceeded: warn
```

---

## Dashboard (Web UI)

Start the local dashboard:

```bash
tokenpak serve --dashboard
# Dashboard at: http://localhost:8766/dashboard
```

Or access it while the proxy is running:

```
http://localhost:8766/dashboard
```

### Dashboard Views

**Overview** — Realtime request stream, current session cost, compression rate

**Cost Breakdown** — Charts by model, agent, day; table with totals

**Session Explorer** — Browse past sessions, filter by model/agent/date/cost

**Compression Stats** — Token savings histogram, recipe hit rates, pipeline timing

**Export** — Download any view as CSV or JSON

---

## Session Filtering

The dashboard and export API support rich filtering:

```bash
# Via CLI
tokenpak cost --since 2026-01-01 --model claude-3-5-sonnet --agent cali

# Via dashboard URL
http://localhost:8766/dashboard?model=gpt-4o&agent=trix&since=2026-03-01
```

Filter parameters:

| Parameter | Example | Description |
|-----------|---------|-------------|
| `since` | `2026-01-01` | Start date |
| `until` | `2026-03-31` | End date |
| `model` | `gpt-4o-mini` | Filter by model |
| `agent` | `cali` | Filter by agent name |
| `min_cost` | `0.01` | Minimum request cost |
| `compressed_only` | `true` | Only show compressed requests |

---

## Export API

The proxy exposes a REST API for programmatic access:

```bash
# All requests this week
curl http://localhost:8766/v1/telemetry/sessions?since=2026-02-28

# Export as CSV
curl http://localhost:8766/v1/telemetry/export?format=csv > sessions.csv

# Summary stats
curl http://localhost:8766/v1/telemetry/summary
```

See [API Reference](../api-reference.md) for full endpoint docs.

---

## Replay

Replay any past request — useful for testing recipe changes:

```bash
# List recent requests
tokenpak replay list --last 20

# Replay without compression (get baseline)
tokenpak replay abc123 --no-compress

# Replay with a different model
tokenpak replay abc123 --model gpt-4o-mini

# Compare compressed vs uncompressed
tokenpak replay abc123 --diff
```

---

## Debug Mode

Capture detailed traces for a set of requests:

```bash
tokenpak debug on --requests 5
# Make 5 requests through your LLM client...
tokenpak debug off

# Inspect a trace
tokenpak trace --last
```

A trace shows:
- Original prompt (token count)
- Compressed prompt (token count, reduction %)
- Which recipe(s) fired
- Which pipeline stages ran
- Routing decision
- Response tokens and cost

---

## Database

All telemetry is stored in a local SQLite database (default: `~/.tokenpak/monitor.db`).

```bash
# Custom location
tokenpak serve --db ~/my-stats.db

# Or in config
tokenpak config set db.path ~/.tokenpak/stats.db
```

### Pruning Old Data

```bash
tokenpak prune --older-than 30d    # remove data older than 30 days
tokenpak prune --older-than 3m     # 3 months
tokenpak prune --dry-run           # preview without deleting
```

---

## Zero-Token Principle

All telemetry operations — viewing costs, searching sessions, generating reports — are free. They query the local SQLite database directly without making any LLM API calls.

This is intentional. Understanding your spending should never cost you money.
