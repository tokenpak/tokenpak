# TokenPak Observability

TokenPak provides comprehensive observability tools for monitoring proxy health, performance, and errors.

## Error Telemetry Logging

The error logger captures exceptions with full context for post-mortem analysis and troubleshooting.

### Basic Usage

```python
from tokenpak.telemetry.error_logger import log_error

try:
 result = call_provider(model="gpt-4", input=user_prompt)
except Exception as e:
 log_error(
 e,
 request_id="req-123",
 model="gpt-4",
 provider="OpenAI",
 input_size=2048,
 cost_estimate=0.015
 )
```

### Log Files

Logs are stored in `~/.tokenpak/logs/` as JSON Lines:

```
errors-2026-03-23.jsonl # Today's errors
errors-2026-03-22.jsonl # Yesterday's errors
archive/ # Logs >7 days old (gzipped)
```

Each line is a valid JSON object:

```json
{
 "timestamp": "2026-03-23T15:30:00.123456",
 "error_type": "TimeoutError",
 "message": "Request timeout after 30s",
 "stack_trace": "Traceback (most recent call last):\n ...",
 "context": {
 "request_id": "req-123",
 "model": "gpt-4",
 "provider": "OpenAI",
 "input_size": 2048,
 "cost_estimate": 0.015
 }
}
```

### CLI Commands

View error summary:
```bash
tokenpak logs error-report
```

List errors for a specific date:
```bash
tokenpak logs list --date 2026-03-23
```

### Automatic Rotation

Logs older than 7 days are automatically archived (gzipped) and moved to `archive/`. This is triggered on the first log write after midnight daily.

### Prometheus Metrics

Error counts are tracked in-memory and can be exported via:

```python
from tokenpak.telemetry.error_logger import get_error_summary

summary = get_error_summary()
# {"TimeoutError": 5, "RateLimitError": 2, "AuthenticationError": 1}
```

## Integration with Proxy

Exceptions raised during proxy operation are automatically logged:

```python
# In proxy request handler
try:
 response = await proxy.handle_request(request)
except Exception as e:
 log_error(
 e,
 request_id=request.id,
 model=request.model,
 provider=request.provider,
 input_size=len(request.input)
 )
 raise
```

## Thread Safety

The error logger is thread-safe for concurrent requests. Log writes use file-level locking; metrics are protected by a threading.Lock.

## Best Practices

1. **Always include request_id** — Enables tracing across components
2. **Log context early** — Before the exception propagates
3. **Use meaningful messages** — "Provider timeout after 30s" > "Error"
4. **Estimate cost** — Helps identify expensive failure patterns
5. **Archive manually** — `rm errors-*.jsonl.gz` to free disk space if needed
