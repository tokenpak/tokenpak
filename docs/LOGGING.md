# TokenPak Logging & Audit Trail

Structured request logging and audit trails for the TokenPak proxy.

## Overview

The logging system provides:

- **Request Logging**: Timestamp, request ID, latency, compression ratio, status code
- **Audit Trails**: Detailed decision records for what was removed and why
- **Multiple Destinations**: File (daily rotation), stdout, syslog
- **Async I/O**: Non-blocking logging for minimal performance impact
- **Structured Format**: JSON for easy log aggregation, text for human readability

## Quick Start

```python
from tokenpak.middleware import LoggingConfig, init_logger

# Initialize logger
config = LoggingConfig(
    enabled=True,
    level="info",
    destination="file",
    retention_days=30,
)
logger = init_logger(config)

# Log a request
logger.log_request(
    endpoint="/compile",
    method="POST",
    request_size=5000,
    response_size=3000,
    status_code=200,
    latency_ms=45.2,
    compression_ratio=0.6,
    message="Compilation successful",
)
```

## Configuration

### LoggingConfig

```python
@dataclass
class LoggingConfig:
    enabled: bool = True                    # Enable/disable logging
    level: LogLevel = "info"                # debug, info, warn, error
    destination: Destination = "file"       # file, stdout, syslog
    retention_days: int = 30                # Log file retention
    include_request_body: bool = False      # Include full request (privacy)
    include_response_body: bool = False     # Include full response (privacy)
    log_dir: Optional[str] = None           # Default: ~/.tokenpak/logs
    async_buffer_size: int = 1000           # Buffer size before flush
    flush_interval_sec: int = 5             # Async flush interval
```

### Destinations

#### File (default)

Logs written to `~/.tokenpak/logs/proxy-YYYY-MM-DD.log` with daily rotation.

```python
config = LoggingConfig(destination="file", log_dir="/var/log/tokenpak")
```

#### Stdout

Real-time output for containerized deployments.

```python
config = LoggingConfig(destination="stdout")
```

#### Syslog

System logging for enterprise integration.

```python
config = LoggingConfig(destination="syslog")
```

## Log Format

### JSON (File)

```json
{
  "timestamp": "2026-03-10T06:00:00Z",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "level": "info",
  "endpoint": "/compile",
  "client_ip": "192.168.1.1",
  "method": "POST",
  "status_code": 200,
  "request_size": 5000,
  "response_size": 3000,
  "latency_ms": 45.2,
  "compression_ratio": 0.6,
  "message": "Compilation successful",
  "context": {
    "blocks": 10,
    "compression_methods": ["truncation", "dedup"]
  }
}
```

### Text (Stdout)

```
[2026-03-10T06:00:00Z] INFO  550e8400-e29b-41d4-a716-446655440000 POST /compile -> 200 [5000→3000B (ratio: 60.0%)] 45.2ms | Compilation successful
```

## Request Logging

### Basic Request

```python
logger.log_request(
    endpoint="/compile",
    method="POST",
    status_code=200,
    request_size=5000,
    response_size=3000,
    latency_ms=45.2,
    compression_ratio=0.6,
)
```

### With Client IP

```python
logger.log_request(
    endpoint="/compile",
    client_ip="192.168.1.1",
    status_code=200,
    # ... other params
)
```

### With Context

```python
logger.log_request(
    endpoint="/compile",
    status_code=200,
    context={
        "blocks": 10,
        "compression_methods": ["truncation", "dedup"],
        "blocks_removed": 3,
        "tokens_removed": 500,
    },
)
```

### With Custom Request ID

```python
logger.log_request(
    endpoint="/compile",
    status_code=200,
    request_id="custom-id-123",
)
```

## Audit Trails

### Compilation Audit

```python
from tokenpak.middleware import create_compile_audit, BlockType

audit = create_compile_audit(
    request_id="550e8400-e29b-41d4-a716-446655440000",
    input_block_count=20,
    input_blocks_by_type={
        BlockType.INSTRUCTION: 5,
        BlockType.KNOWLEDGE: 10,
        BlockType.EVIDENCE: 5,
    },
    input_total_size=50000,
)

# Populate output
audit.output_block_count = 15
audit.output_blocks_by_type = {
    BlockType.INSTRUCTION: 5,
    BlockType.KNOWLEDGE: 8,
    BlockType.EVIDENCE: 2,
}
audit.output_total_size = 35000
audit.compression_ratio = 0.7
audit.parse_latency_ms = 5.0
audit.compile_latency_ms = 30.0
audit.render_latency_ms = 5.0
audit.total_latency_ms = 40.0

# Log the audit
middleware.log_compile_audit(audit)
```

### Cache Audit

```python
from tokenpak.middleware import create_cache_audit

audit = create_cache_audit(
    request_id="550e8400-e29b-41d4-a716-446655440000",
    operation="get",
    block_id="block-abc123",
)
audit.cache_hit = True
audit.cached_value_size = 1000

middleware.log_cache_audit(audit)
```

### Metrics Audit

```python
from tokenpak.middleware import create_metrics_audit

audit = create_metrics_audit(
    request_id="550e8400-e29b-41d4-a716-446655440000",
    aggregation_window="24h",
)
audit.data_points_returned = 1440
audit.metrics_included = ["compression_ratio", "latency", "blocks_removed"]

middleware.log_metrics_audit(audit)
```

## Middleware Integration

### Flask Integration

```python
from flask import Flask, request
from tokenpak.middleware import LoggingConfig, RequestLogger, LoggingMiddleware

app = Flask(__name__)

config = LoggingConfig(destination="file")
logger = RequestLogger(config)
middleware = LoggingMiddleware(logger)

@app.route("/compile", methods=["POST"])
@middleware.wrap_request("/compile", "POST")
def compile_blocks():
    body = request.get_json()
    result = compress(body)
    return {"result": result}, 200
```

### Starlette/FastAPI Integration

```python
from fastapi import FastAPI, Request
from tokenpak.middleware import LoggingConfig, RequestLogger, LoggingMiddleware

app = FastAPI()

config = LoggingConfig(destination="file")
logger = RequestLogger(config)
middleware = LoggingMiddleware(logger)

@app.post("/compile")
@middleware.wrap_request("/compile", "POST")
async def compile_blocks(request: Request):
    body = await request.json()
    result = compress(body)
    return {"result": result}
```

## Log Analysis

### Search by Request ID

```bash
grep "550e8400-e29b-41d4-a716-446655440000" ~/.tokenpak/logs/proxy-*.log
```

### Filter by Status Code

```bash
grep '"status_code": 500' ~/.tokenpak/logs/proxy-*.log
```

### Find Slow Requests (>100ms)

```bash
jq 'select(.latency_ms > 100)' ~/.tokenpak/logs/proxy-*.log
```

### Compression Ratio Analysis

```bash
jq '.compression_ratio' ~/.tokenpak/logs/proxy-*.log | sort -n | tail -20
```

### Group by Endpoint

```bash
jq -s 'group_by(.endpoint) | map({endpoint: .[0].endpoint, count: length})' ~/.tokenpak/logs/proxy-*.log
```

## Performance

- **Latency Overhead**: <5ms per request (target met)
- **Log Size**: ~500 bytes per request
- **Async I/O**: Non-blocking with 1000-record buffer
- **Throughput**: Handles 1000+ requests/sec

## Troubleshooting

### Logs not appearing

- Check `enabled: true` in config
- Verify log directory is writable: `touch ~/.tokenpak/logs/test.txt`
- Check log level: `level: "debug"` includes all messages

### Slow logging

- Increase `async_buffer_size` (default: 1000)
- Switch to `destination: "stdout"` for faster I/O
- Reduce `flush_interval_sec` if buffer fills quickly

### Missing request ID

- Request IDs are auto-generated (UUID v4) if not provided
- All log lines for one request share the same request_id
- Return X-Request-ID header to clients for correlation

### Large log files

- Logs rotate daily by default
- Adjust `retention_days` (default: 30) to change cleanup
- Filter old logs: `find ~/.tokenpak/logs -name "*.log" -mtime +30 -delete`

## Best Practices

1. **Privacy**: Never log request/response bodies (set to false)
2. **Retention**: Keep 30 days of logs for incident investigation
3. **Aggregation**: Use JSON format for cloud logging integration
4. **Request IDs**: Always propagate X-Request-ID for correlation
5. **Performance**: Use async logging to avoid blocking requests

## Future Enhancements

- Cloud logging integration (Datadog, Splunk, Elasticsearch)
- Real-time alerting on error rates
- Distributed tracing (OpenTelemetry)
- Metrics export (Prometheus format)
