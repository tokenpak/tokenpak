# Telemetry: Data Privacy & Metrics

TokenPak collects operational metrics locally so you can track costs and debug issues. This page explains exactly what is collected, where it's stored, and what leaves your machine (nothing, by default).

---

## Privacy Model

### What TokenPak sees

The proxy sits between your LLM client and your provider. It reads:

- **Request metadata**: model name, token counts, timestamps, HTTP status codes
- **Sizes**: raw token count, compressed token count, cost estimate
- **Routing**: which provider was used, whether failover occurred

### What TokenPak does NOT read or store

- Your prompts or message content
- Your API keys (they're in the `Authorization` header and are forwarded as-is)
- LLM responses
- File contents during vault indexing (only metadata and extracted symbols)

### What leaves your machine

**Nothing, by default.** All telemetry is stored in a local SQLite database (`~/.tokenpak/telemetry.db`). No data is sent to TokenPak servers.

The only outbound connections TokenPak makes:
1. Your LLM request, forwarded to your provider (Anthropic, OpenAI, Google)
2. License validation — sends your license key hash, not usage data

### Optional anonymous metrics

If you want to help improve TokenPak, you can opt in to anonymous usage metrics:

```bash
TOKENPAK_METRICS_ENABLED=1 tokenpak serve
```

What anonymous metrics include:
- Aggregate token counts (not content)
- Compression ratio distribution (not individual requests)
- Model names
- OS/Python version
- TokenPak version
- **Active profile** — the loaded workflow profile name (e.g. `balanced`, `agentic`, `claude-code-cli`).
 This is an anonymous categorical string that tells us which mode users adopt most.
- **Consumption mode** — the auto-detected invocation mode (one of `cli`, `tui`, `tmux`, `sdk`, `ide`, `cron`).
 Detected from environment variables at runtime; no user-identifying information.

What they never include: prompts, responses, file names, API keys, user identifiers.

To opt out (default): don't set `TOKENPAK_METRICS_ENABLED`, or set it to `0`.

---

## Local Telemetry Storage

### Schema

Telemetry is stored in SQLite at `~/.tokenpak/telemetry.db` (and an agent-level DB at `~/.tokenpak/monitor.db`).

**Core tables:**

```sql
-- One row per LLM request
tp_events (
 trace_id TEXT, -- unique per request
 request_id TEXT,
 event_type TEXT, -- e.g. "completion"
 ts REAL, -- unix timestamp
 provider TEXT, -- "anthropic" | "openai" | "google"
 model TEXT, -- e.g. "claude-opus-4-6"
 agent_id TEXT,
 duration_ms REAL,
 status TEXT, -- "ok" | "error"
 error_class TEXT
)

-- Token counts and compression stats
tp_usage (
 trace_id TEXT,
 input_tokens INT, -- tokens sent to provider (after compression)
 output_tokens INT,
 tokens_saved INT, -- how many were compressed away
 savings_pct REAL,
 cache_hit BOOLEAN -- provider-level cache hit if applicable
)

-- Cost estimates
tp_costs (
 trace_id TEXT,
 cost_usd REAL, -- estimated cost based on pricing catalog
 cost_saved_usd REAL
)

-- Per-block segment breakdown
tp_segments (
 trace_id TEXT,
 segment_type TEXT, -- "code" | "markdown" | "text" | etc.
 tokens_raw INT,
 tokens_after INT
)
```

### Pricing catalog

Cost estimates use a local pricing catalog (`tokenpak/telemetry/data/pricing_catalog.json`) that includes per-model rates for all major providers. The catalog ships with each TokenPak release and can be updated independently.

For models not in the catalog, a conservative default is used (`$3.00/$15.00 per MTok input/output`).

---

## What You Can Do With Telemetry

### Cost reports

```bash
tokenpak cost --today
tokenpak cost --week
tokenpak cost --month
tokenpak savings --lifetime
```

### Session stats

```bash
tokenpak status --full
# Sessions: 847 requests
# Tokens saved: 2,847,341 (41.3% avg)
# Cost saved: $8.54
```

### Trace inspection

Every request gets a trace ID. Inspect individual requests:

```bash
tokenpak trace --last
tokenpak trace --id <trace_id>
```

Output:

```
Trace: req-a1b2c3
Model: claude-opus-4-6 (anthropic)
Time: 2026-03-06 08:14:22 PST
Status: ok (1,847ms)

Pipeline:
 dedup: 0 removed
 segmentize: 12 blocks
 directives: applied

Tokens:
 raw: 8,240
 compressed: 4,891
 saved: 3,349 (40.6%)

Cost:
 estimated: $0.074
 saved: $0.050
```

### Export

```bash
tokenpak metrics export --format csv --out report.csv
tokenpak metrics export --format json --out report.json
```

### Dashboard

The web dashboard is served alongside the proxy at `http://localhost:8766/dashboard`. It shows:

- FinOps view: cost trends, top models by spend, savings over time
- Engineering view: latency distribution, error rates, compression efficiency
- Audit log: per-request trace browser with filters

---

## Stats Footer

Optionally append a one-line summary to every LLM response:

```bash
TOKENPAK_STATS_FOOTER=1 tokenpak serve
```

The footer is appended as a trailing comment in the response stream:

```
⚡ TokenPak: -3,349 tokens (40.6%) | $0.050 saved
```

Disable by setting `TOKENPAK_STATS_FOOTER=0` or removing it from config.

---

## Data Retention

Telemetry accumulates indefinitely by default. Prune old records:

```bash
# Delete events older than 30 days
tokenpak maintenance prune --days 30

# Delete all telemetry (keeps config and vault index)
tokenpak maintenance purge --telemetry
```

You can automate pruning with a cron job or systemd timer:

```bash
# crontab -e
0 3 * * 0 tokenpak maintenance prune --days 90
```

---

## Disable Telemetry Entirely

If you don't want any local storage:

```bash
TOKENPAK_DB=/dev/null tokenpak serve
```

This routes the SQLite DB to `/dev/null` — no disk writes. Cost reports and trace inspection won't work, but compression and proxy functions normally.
