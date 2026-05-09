# TokenPak Live Monitor Dashboard

A real-time single-page dashboard for monitoring TokenPak proxy health, request throughput, error rates, and cost.

## Quick Start

```bash
# Start the dashboard (default port 8767)
tokenpak monitor

# Custom port
tokenpak monitor --port 9000
```

Then open: **http://localhost:8767/**

The proxy must be running on port 8766 (`tokenpak serve`) for live data.

## Features

| Feature | Description |
|---------|-------------|
| **Request counter** | Total session requests + error count |
| **Cache hit rate** | Live cache hits/misses/rate |
| **Token savings** | Compressed tokens saved this session |
| **Cost ticker** | Running cost + projection /hour, /day, /month |
| **Latency percentiles** | p50, p75, p95, p99 from recent 100 requests |
| **Model chart** | Pie chart of requests per model |
| **Error log** | Filterable error viewer with export |
| **Dark/light mode** | Persisted in localStorage |
| **Auto-refresh** | Configurable 3s/5s/10s/30s or paused |

## Architecture

```
tokenpak monitor (port 8767)
├── GET / → dashboard.html (self-contained)
├── GET /api/stats → proxied from localhost:8766/stats
└── GET /api/errors → reads ~/.tokenpak/logs/errors-*.jsonl
```

- No external JS dependencies — pure vanilla JS
- Self-contained HTML file (can be opened directly from disk)
- Graceful fallback when proxy is offline (shows "Proxy Offline")

## Backend Data Sources

### `/api/stats`
Forwards to the TokenPak proxy stats endpoint (`http://127.0.0.1:8766/stats`).

Returns session metrics:
- `requests`, `errors`, `cost`, `cost_saved`, `saved_tokens`
- `cache_hits`, `cache_misses`
- `start_time`, `recent_requests[]` (with `latency_ms`, `model`)

### `/api/errors`
Reads from `~/.tokenpak/logs/errors-*.jsonl` (last 3 days).

Query params:
- `limit=N` — max entries (default 100)
- `model=name` — filter by model name

## Customization

### Change refresh interval
Use the dropdown in the header (3s default, or pause).

### Proxy URL
Set `TOKENPAK_PROXY_URL` env var to override proxy address:
```bash
TOKENPAK_PROXY_URL=http://192.168.1.100:8766 tokenpak monitor
```

### Logs directory
Error logs are read from `~/.tokenpak/logs/errors-*.jsonl`.
This is populated by the `p2-tokenpak-error-telemetry-logger` system.

## Exporting Errors

Use the **Export JSON** or **Export CSV** buttons to download filtered error logs.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Proxy Offline" | Start proxy: `tokenpak serve` |
| No error data | Check `~/.tokenpak/logs/` has `errors-*.jsonl` files |
| Port already in use | Use `--port 8768` |
| Dashboard blank | Check browser console for CORS/network errors |

## Running as Background Service

```bash
# Start in background
nohup tokenpak monitor --port 8767 > /tmp/tokenpak-monitor.log 2>&1 &

# Check it's running
curl -s http://localhost:8767/ | head -5
```
