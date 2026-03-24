# TokenPak Deployment Guide

This guide covers everything from a local dev install to a production systemd service.

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Python | 3.10+ | 3.11+ |
| RAM | 256 MB | 512 MB+ (for vault indexing) |
| Disk | 100 MB | 1 GB+ (for telemetry DB + vault index) |
| OS | Linux / macOS / Windows | Linux (for systemd) |
| Network | Localhost only | — |

Optional dependencies:

```bash
pip install tokenpak[tiktoken]   # accurate token counting (recommended)
pip install tokenpak[ml]         # ML-powered compression via LLMLingua
pip install tokenpak[dev]        # development tools (pytest, ruff, etc.)
```

---

## Installation

### pip (recommended)

```bash
pip install tokenpak
```

### From source

```bash
git clone https://github.com/tokenpak/tokenpak
cd tokenpak
pip install -e .
```

### Verify install

```bash
tokenpak --version
tokenpak doctor        # checks Python version, deps, config
```

---

## Quick Start

```bash
tokenpak serve --port 8766
```

That's it. No config required. Point your LLM client at `http://localhost:8766`.

---

## Configuration

### Config file

Default location: `~/.tokenpak/config.json`

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
    "db_path": "~/.tokenpak/registry.db",
    "watch": false
  },
  "stats_footer": false,
  "debug": false
}
```

### Environment variables

All env vars override config file values. **Env vars take priority.**

| Variable | Default | Description |
|---|---|---|
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_MODE` | `hybrid` | Compression mode: `strict`, `hybrid`, `aggressive` |
| `TOKENPAK_COMPACT` | `1` | Master compression switch (`0` to disable) |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | Min tokens before compression activates |
| `TOKENPAK_DB` | `.ocp/monitor.db` | SQLite telemetry database path |
| `TOKENPAK_STATS_FOOTER` | `0` | Append savings summary to each response (`1` to enable) |
| `TOKENPAK_DEBUG` | `0` | Enable debug logging (`1` to enable) |
| `TOKENPAK_METRICS_ENABLED` | `0` | Opt-in anonymous usage metrics (`1` to enable) |
| `TOKENPAK_CAPSULE_BUILDER` | `0` | Enable capsule builder feature (`1` to enable) |

### Compression modes

| Mode | Behavior | Best for |
|---|---|---|
| `hybrid` | Applies compression when tokens > threshold | General use (default) |
| `strict` | Aggressive compression on all requests | Cost-sensitive workloads |
| `aggressive` | Maximum compression, accepts minor quality loss | Batch/automation |

---

## Running as a systemd Service

### Proxy daemon

Create `~/.config/systemd/user/tokenpak.service`:

```ini
[Unit]
Description=TokenPak LLM Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/env tokenpak serve --port 8766 --workers 4
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenpak

# Environment
Environment=PYTHONUNBUFFERED=1
Environment=TOKENPAK_MODE=hybrid

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable tokenpak
systemctl --user start tokenpak

# Verify
systemctl --user status tokenpak
journalctl --user -u tokenpak -f
```

### Vault file watcher (optional)

To automatically re-index your vault when files change:

```bash
# Use the bundled installer
bash tokenpak/agent/systemd/install-service.sh ~/vault
```

Or manually enable the templated service:

```bash
# Escape the path for systemd instance name
systemctl --user enable tokenpak-watcher@$(systemd-escape ~/vault)
systemctl --user start  tokenpak-watcher@$(systemd-escape ~/vault)
```

---

## Connecting LLM Clients

### Claude Code / Anthropic SDK

```bash
export ANTHROPIC_BASE_URL=http://localhost:8766
```

Or in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8766"
  }
}
```

### OpenAI SDK / OpenAI clients

```bash
export OPENAI_BASE_URL=http://localhost:8766/v1
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8766/v1", api_key="your-key")
```

### Google Gemini

```bash
export GOOGLE_AI_BASE_URL=http://localhost:8766
```

### Multi-provider

All three providers can run through the same proxy simultaneously. TokenPak auto-detects the provider from the `Authorization` header and routes accordingly — no separate ports needed.

---

## Monitoring & Logging

### Check proxy status

```bash
tokenpak status          # basic health check
tokenpak status --full   # include session stats
```

### View costs

```bash
tokenpak cost --today
tokenpak cost --week
tokenpak cost --month
tokenpak savings --lifetime
```

### View logs

```bash
# systemd (if running as service)
journalctl --user -u tokenpak -f

# Enable debug logging
TOKENPAK_DEBUG=1 tokenpak serve
```

### Dashboard

The web dashboard runs alongside the proxy:

```bash
tokenpak serve --port 8766
# Dashboard available at http://localhost:8766/dashboard
```

### Export data

```bash
tokenpak metrics export --format csv --out report.csv
tokenpak metrics export --format json --out report.json
```

---

## Budget Enforcement

Protect against runaway API spend:

```bash
tokenpak budget set --monthly 50     # $50/month hard limit
tokenpak budget alert --at 80        # warn at 80% consumed
tokenpak budget status               # current spend vs limit
```

When the budget limit is hit, requests return a `429 Budget Exceeded` error instead of forwarding to the provider.

---

## Vault Indexing

Index a codebase or notes vault for zero-token semantic search:

```bash
tokenpak index ~/vault               # one-time index
tokenpak index ~/vault --watch       # watch for changes
tokenpak calibrate ~/vault           # auto-tune parallelism for this machine
```

Calibration runs a benchmark and saves optimal worker settings to `~/.tokenpak/calibration.json`. Run once after install on new hardware.

---

## Upgrading

```bash
pip install --upgrade tokenpak

# Verify after upgrade
tokenpak doctor
tokenpak status
```

If running as a service:

```bash
pip install --upgrade tokenpak
systemctl --user restart tokenpak
```

---

## Uninstall

```bash
# Stop service (if running)
systemctl --user stop tokenpak
systemctl --user disable tokenpak

# Remove package
pip uninstall tokenpak

# Remove data (optional — this deletes all telemetry and vault indexes)
rm -rf ~/.tokenpak
```

---

## OpenTelemetry Export

TokenPak can export request spans and metrics to any OTLP-compatible backend (Prometheus via OTel Collector, Grafana, Datadog, Jaeger, etc.).

### Install OTel dependencies

```bash
pip install "tokenpak[otel]"
```

### Enable export

Set the `TOKENPAK_OTEL_ENDPOINT` environment variable before starting the proxy:

```bash
# HTTP/JSON endpoint (OTel Collector default)
export TOKENPAK_OTEL_ENDPOINT=http://localhost:4318

# gRPC endpoint (port 4317)
export TOKENPAK_OTEL_ENDPOINT=http://localhost:4317
```

When `TOKENPAK_OTEL_ENDPOINT` is **not set**, OTel is completely disabled — zero imports, zero overhead.

### Spans exported

Each proxied request generates one span (`tokenpak.proxy_request`) with attributes:

| Attribute | Type | Description |
|---|---|---|
| `tokenpak.model` | string | Model name (e.g. `claude-3-haiku`) |
| `tokenpak.input_tokens` | int | Raw input tokens (before compression) |
| `tokenpak.output_tokens` | int | Output tokens |
| `tokenpak.compression_ratio` | float | Sent ÷ raw (1.0 = no compression) |
| `tokenpak.cache_hit` | bool | True if prompt cache was used |
| `http.status_code` | int | Upstream HTTP status |
| `tokenpak.duration_ms` | float | End-to-end latency in ms |

### Metrics exported

| Metric | Type | Description |
|---|---|---|
| `tokenpak.requests.total` | counter | Total requests (label: `model`) |
| `tokenpak.tokens.input` | counter | Raw input tokens (label: `model`) |
| `tokenpak.tokens.output` | counter | Output tokens (label: `model`) |
| `tokenpak.compression.ratio` | histogram | Per-request compression ratio |
| `tokenpak.cache.hit_rate` | counter | Cache hits/misses (label: `result=hit\|miss`) |

### Error handling

- If the OTel endpoint is unreachable, requests continue normally — OTel errors are suppressed
- If `opentelemetry` packages are not installed but the env var is set, a warning is logged and OTel is disabled
