---
title: "Observability"
status: active
created: 2026-03-23
---
# Error Telemetry & Observability

TokenPak includes comprehensive error tracking and telemetry for production deployments.

## Error Logging

### Quick Start

The error logger automatically captures exceptions with context for post-mortem analysis.

```python
from tokenpak.telemetry import get_error_logger

logger = get_error_logger()

try:
    result = call_llm(model="gpt-4")
except Exception as e:
    logger.log_error(
        request_id="req-123",
        error=e,
        context={
            "model": "gpt-4",
            "provider": "openai",
            "input_size": 1024,
            "cost_estimate": 0.045,
            "duration_ms": 2350
        }
    )
```

### Using the Decorator

For automatic exception logging, use the `@log_exception` decorator:

```python
from tokenpak.telemetry import log_exception

@log_exception(
    request_id="req-456",
    context={"model": "gpt-4", "provider": "openai"}
)
def call_model():
    return openai.ChatCompletion.create(...)
```

Any exception raised in the decorated function is automatically logged and re-raised.

### Log Storage

Errors are stored in append-only JSON Lines format:

```
~/.tokenpak/logs/errors-2026-03-24.jsonl
~/.tokenpak/logs/errors-2026-03-23.jsonl
...
```

Each line is a JSON object containing:
- `timestamp` — ISO 8601 UTC timestamp
- `request_id` — Unique request identifier
- `error_type` — Exception class name
- `message` — Exception message
- `stack_trace` — Full Python traceback
- `context` — Dict with optional metadata (model, provider, cost, timing, etc.)

Example log entry:
```json
{
  "timestamp": "2026-03-24T17:35:22.123456Z",
  "request_id": "req-123",
  "error_type": "ValueError",
  "message": "Invalid model parameter",
  "stack_trace": "Traceback (most recent call last):\n  ...",
  "context": {
    "model": "invalid-model",
    "provider": "openai",
    "input_size": 1024
  }
}
```

## Log Rotation

Log files are automatically rotated daily. Logs older than 7 days are automatically:
1. **Compressed** with gzip
2. **Moved** to `~/.tokenpak/logs/archive/`

This keeps active logs lean while preserving historical data for analysis.

## CLI Commands

### Generate Error Report

```bash
# Report for last 1 day (default)
tokenpak telemetry error-report

# Report for last 7 days
tokenpak telemetry error-report --days 7

# Filter by error type
tokenpak telemetry error-report --type ValueError

# JSON format for scripting
tokenpak telemetry error-report --format json
```

Output example:
```
============================================================
Error Report — Last 1 day(s)
Generated: 2026-03-24T17:35:22Z
============================================================

Total Errors: 42

By Error Type:
  ValueError: 18
  TimeoutError: 12
  AuthenticationError: 8
  KeyError: 4

By Provider:
  openai: 22
  anthropic: 15
  azure: 5

============================================================
```

### List Log Files

```bash
# List recent logs
tokenpak telemetry logs

# Show more files
tokenpak telemetry logs --limit 20
```

### Export for Analysis

```bash
# Export all logs from last 7 days
tokenpak telemetry export logs.json

# Export last 30 days
tokenpak telemetry export logs.json --days 30
```

Exported format is a JSON array of log entries, suitable for:
- External analysis tools
- Visualization dashboards
- Integration with error tracking services (Sentry, DataDog, etc.)

## Prometheus Metrics

Error counts by type are tracked for Prometheus integration:

```python
from tokenpak.telemetry import get_error_logger

logger = get_error_logger()
metrics = logger.get_metrics()

# Output:
# {
#   'ValueError': 18,
#   'TimeoutError': 12,
#   'AuthenticationError': 8
# }
```

Include in your metrics endpoint:

```python
from prometheus_client import Gauge

error_gauge = Gauge('tokenpak_errors_total', 'Total errors by type', ['error_type'])

metrics = logger.get_metrics()
for error_type, count in metrics.items():
    error_gauge.labels(error_type=error_type).set(count)
```

## Thread Safety

The error logger is fully thread-safe. Multiple threads can log errors concurrently without contention:

```python
import threading
from tokenpak.telemetry import get_error_logger

logger = get_error_logger()

def worker():
    try:
        # Do work
        pass
    except Exception as e:
        logger.log_error(f"req-{threading.current_thread().name}", e)

threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
```

## Error Reporting Best Practices

1. **Always include a request ID** — Use it to correlate errors across distributed logs
2. **Add context fields** — Include model, provider, timing, and cost data for debugging
3. **Don't log personally identifiable information** — Filter PII before logging
4. **Review logs regularly** — Use daily error reports to catch new failure patterns
5. **Integrate with alerting** — Set up alerts for error spikes or new error types

## Troubleshooting

### "Failed to write error log"

Check that `~/.tokenpak/logs/` is writable:

```bash
ls -la ~/.tokenpak/logs/
chmod 755 ~/.tokenpak/logs/
```

### "Malformed log line"

Log files can be partially corrupt if the process crashes. This is non-fatal — the logger skips malformed lines and continues. Use the `export` command to clean and extract valid entries:

```bash
tokenpak telemetry export clean-logs.json --days 7
```

### Large log files

If log files grow too quickly, consider:
1. Enabling sampling (log only a percentage of errors)
2. Reducing context verbosity
3. Exporting and archiving old logs manually

```bash
# Manually archive logs older than 30 days
find ~/.tokenpak/logs -name "errors-*.jsonl" -mtime +30 -exec gzip {} \; -exec mv {}.gz archive/ \;
```

## Next Steps

- **Set up monitoring**: Integrate error reports into your observability stack (DataDog, NewRelic, etc.)
- **Create dashboards**: Visualize error trends by type, provider, and time
- **Automate alerts**: Trigger notifications on error spikes or critical error types
