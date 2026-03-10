# Observability — Request Logging

TokenPak proxy ships with built-in structured request logging. Every proxied
request produces a JSON log entry suitable for ingestion into any log
aggregator (Datadog, Splunk, Loki, CloudWatch, etc.).

---

## Quick Start

Logging is **enabled by default** and writes to `~/.tokenpak/logs/proxy-YYYY-MM-DD.log`.

```bash
# View today's log in real time
tail -f ~/.tokenpak/logs/proxy-$(date +%Y-%m-%d).log | jq .

# Count requests by status code
cat ~/.tokenpak/logs/proxy-*.log | jq -r '.response_status' | sort | uniq -c

# Find slow requests (> 2s)
cat ~/.tokenpak/logs/proxy-*.log | jq 'select(.latency_ms > 2000)'
```

---

## Configuration

Add a `"logging"` key to `~/.tokenpak/config.json`:

```json
{
  "logging": {
    "enabled": true,
    "level": "info",
    "destination": "file",
    "retention_days": 30,
    "include_request_body": false,
    "include_response_body": false
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Master on/off switch |
| `level` | string | `"info"` | Minimum level: `"debug"`, `"info"`, `"warn"` |
| `destination` | string | `"file"` | `"file"`, `"stdout"`, `"syslog"` |
| `retention_days` | int | `30` | Days to keep log files (file destination only) |
| `include_request_body` | bool | `false` | Include raw request body (privacy risk) |
| `include_response_body` | bool | `false` | Include raw response body (privacy risk) |

### Environment Variable Overrides

```bash
TOKENPAK_LOG_ENABLED=1          # or 0 to disable
TOKENPAK_LOG_LEVEL=debug        # debug | info | warn
TOKENPAK_LOG_DESTINATION=stdout # file | stdout | syslog
TOKENPAK_LOG_RETENTION_DAYS=7
```

---

## Log Schema

Each log entry is a single-line JSON object (JSONL format):

```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-03-10T14:23:01.234567+00:00",
  "level": "info",
  "client_ip": "127.0.0.1",
  "method": "POST",
  "endpoint": "/v1/messages",
  "request_body_size": 8192,
  "response_status": 200,
  "response_body_size": 1024,
  "compression_ratio": 0.7241,
  "latency_ms": 432.15,
  "model": "claude-3-5-sonnet-20241022",
  "provider": "anthropic"
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | UUID v4 unique per request. Propagated via `X-Request-ID` response header. |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `level` | string | `"debug"`, `"info"`, `"warn"` |
| `client_ip` | string | Client IP address |
| `method` | string | HTTP method (`POST`, `GET`, etc.) |
| `endpoint` | string | Request path (e.g. `/v1/messages`, `/v1/chat/completions`) |
| `request_body_size` | int | Request body bytes |
| `response_status` | int | HTTP response status code |
| `response_body_size` | int | Response body bytes |
| `compression_ratio` | float\|null | Tokens sent / tokens in raw request (e.g. 0.72 = 28% saved). Null if no compression. |
| `latency_ms` | float | Total request latency in milliseconds |
| `model` | string | LLM model name (e.g. `claude-3-5-sonnet-20241022`) |
| `provider` | string | Provider name (`anthropic`, `openai`, `google`) |

---

## Audit Trail (Debug Level)

When `level` is set to `"debug"`, the proxy also emits structured audit events
that document *why* tokens were removed or compacted:

### Compile event

```json
{
  "request_id": "a1b2c3d4...",
  "timestamp": "2026-03-10T14:23:01.1Z",
  "level": "debug",
  "event": "compile",
  "input_block_count": 12,
  "output_block_count": 7,
  "blocks_removed_count": 5,
  "blocks_removed": [
    {"id": "knowledge-3", "reason": "low_relevance"},
    {"id": "evidence-7", "reason": "duplicate"}
  ],
  "compression_method": "extractive",
  "stage_timings_ms": {
    "parse": 3.2,
    "compile": 88.5,
    "render": 2.8
  },
  "input_block_types": {"instructions": 2, "knowledge": 5, "evidence": 5},
  "output_block_types": {"instructions": 2, "knowledge": 3, "evidence": 2},
  "tokens_before": 12000,
  "tokens_after": 8400
}
```

### Cache event

```json
{
  "request_id": "a1b2c3d4...",
  "timestamp": "2026-03-10T14:23:01.0Z",
  "level": "debug",
  "event": "cache",
  "operation": "get",
  "block_id": "knowledge-3",
  "cache_hit": true,
  "cached_size": 2048
}
```

---

## Example Log Output

### Successful compile request

```json
{"request_id":"a1b2c3d4-e5f6-7890-abcd-ef1234567890","timestamp":"2026-03-10T14:23:01.234567+00:00","level":"info","client_ip":"127.0.0.1","method":"POST","endpoint":"/v1/messages","request_body_size":8192,"response_status":200,"response_body_size":1024,"compression_ratio":0.7241,"latency_ms":432.15,"model":"claude-3-5-sonnet-20241022","provider":"anthropic"}
```

### Error response (401 Unauthorized)

```json
{"request_id":"b2c3d4e5-f6a7-8901-bcde-f12345678901","timestamp":"2026-03-10T14:23:05.001234+00:00","level":"warn","client_ip":"192.168.1.50","method":"POST","endpoint":"/v1/messages","request_body_size":1024,"response_status":401,"response_body_size":128,"latency_ms":12.3,"model":"unknown","provider":"anthropic"}
```

### Cache hit

```json
{"request_id":"c3d4e5f6-a7b8-9012-cdef-123456789012","timestamp":"2026-03-10T14:23:10.567890+00:00","level":"debug","event":"cache","operation":"get","block_id":"kb-system-prompt","cache_hit":true,"cached_size":4096}
```

---

## Request ID Correlation

Every request gets a UUID assigned in `X-Request-ID` response header.
Client libraries can log this ID to correlate their trace with proxy logs:

```python
import httpx

resp = httpx.post("http://localhost:8766/v1/messages", ...)
request_id = resp.headers.get("X-Request-ID")
print(f"TokenPak request ID: {request_id}")
```

To supply your own ID (e.g., from a distributed trace):

```python
resp = httpx.post(
    "http://localhost:8766/v1/messages",
    headers={"X-Request-ID": "my-trace-id-abc123"},
    ...
)
```

---

## Troubleshooting Using Logs

### "Why was my request slow?"

```bash
# Show requests slower than 1s, sorted by latency
cat ~/.tokenpak/logs/proxy-*.log \
  | jq 'select(.latency_ms > 1000)' \
  | jq -s 'sort_by(.latency_ms) | reverse | .[0:10]'
```

### "Why did my request fail?"

```bash
# Find all errors with request IDs
cat ~/.tokenpak/logs/proxy-*.log \
  | jq 'select(.response_status >= 400) | {request_id, timestamp, response_status, error}'
```

### "How much is TokenPak compressing?"

```bash
# Average compression ratio across all requests
cat ~/.tokenpak/logs/proxy-*.log \
  | jq 'select(.compression_ratio != null) | .compression_ratio' \
  | awk '{sum+=$1; n++} END {printf "Avg compression ratio: %.2f (%.0f%% tokens sent)\n", sum/n, sum/n*100}'
```

### "Why was block X removed?"

Enable `level: "debug"` and look for audit events:

```bash
cat ~/.tokenpak/logs/proxy-*.log \
  | jq 'select(.event == "compile" and .blocks_removed != null) | .blocks_removed[] | select(.id == "your-block-id")'
```

---

## Log Destinations

### File (default)

Daily rotating files at `~/.tokenpak/logs/proxy-YYYY-MM-DD.log`.
Files older than `retention_days` are automatically pruned.

### Stdout

Suitable for containerised deployments. Configure in `config.json`:

```json
{"logging": {"destination": "stdout"}}
```

Or via env: `TOKENPAK_LOG_DESTINATION=stdout`

Stdout logs are consumed by Docker/Kubernetes log drivers and forwarded to
your configured log aggregator.

### Syslog

Linux/macOS only. Logs appear in system journal with the identifier
`tokenpak-proxy`:

```bash
journalctl -t tokenpak-proxy -f
```

### Future: Cloud (Datadog / Splunk)

Cloud log destinations are planned for a future release. In the interim,
use the file destination with a log shipping agent (Filebeat, Fluentd, etc.).
