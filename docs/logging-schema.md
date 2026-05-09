# TokenPak Logging Schema

Complete reference for logging data structures and JSON fields.

## LogRecord

Standard request log entry.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO 8601 | Request timestamp (UTC) |
| `request_id` | UUID | Unique request identifier |
| `level` | string | Log level: debug, info, warn, error |
| `endpoint` | string | API endpoint (e.g., /compile, /cache/get) |
| `client_ip` | string | Client IP address |
| `method` | string | HTTP method (GET, POST, etc.) |
| `status_code` | integer | Response HTTP status code |
| `request_size` | integer | Request body size (bytes) |
| `response_size` | integer | Response body size (bytes) |
| `latency_ms` | float | Total request latency (milliseconds) |
| `compression_ratio` | float | Response size / request size (0-1) |
| `message` | string | Human-readable message |
| `context` | object | Additional structured context |

### Example

```json
{
 "timestamp": "2026-03-10T06:00:00.123456Z",
 "request_id": "550e8400-e29b-41d4-a716-446655440000",
 "level": "info",
 "endpoint": "/compile",
 "client_ip": "192.168.1.100",
 "method": "POST",
 "status_code": 200,
 "request_size": 5000,
 "response_size": 3000,
 "latency_ms": 45.234,
 "compression_ratio": 0.6,
 "message": "Compilation successful",
 "context": {
 "input_blocks": 20,
 "output_blocks": 15,
 "blocks_removed": 5,
 "compression_methods": ["truncation", "deduplication"]
 }
}
```

## CompileAudit

Detailed audit trail for `/compile` requests.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | UUID | Links to LogRecord |
| `timestamp` | ISO 8601 | Audit timestamp |
| `input_block_count` | integer | Number of input blocks |
| `input_blocks_by_type` | object | Block counts by type |
| `input_total_size` | integer | Total input size (bytes) |
| `output_block_count` | integer | Number of output blocks |
| `output_blocks_by_type` | object | Output counts by type |
| `output_total_size` | integer | Total output size (bytes) |
| `blocks_audited` | array | Detailed per-block decisions |
| `compression_methods_used` | object | Count of each method used |
| `parse_latency_ms` | float | Parse phase latency |
| `compile_latency_ms` | float | Compilation latency |
| `render_latency_ms` | float | Rendering latency |
| `total_latency_ms` | float | Total compilation latency |
| `compression_ratio` | float | Output / input size |
| `tokens_removed` | integer | Total tokens removed |
| `errors` | array | Any errors encountered |

### BlockType Values

- `instruction` — System instructions
- `knowledge` — Knowledge base content
- `evidence` — Supporting evidence
- `example` — Examples
- `custom` — Custom blocks

### CompressionMethod Values

- `extractive` — Extract key sentences
- `llm` — LLM-based summarization
- `truncation` — Truncate to token limit
- `deduplication` — Remove duplicates
- `semantic` — Semantic similarity filtering

### BlockAudit

Per-block decision record within CompileAudit.

| Field | Type | Description |
|-------|------|-------------|
| `block_id` | string | Block identifier |
| `block_type` | string | Type of block |
| `original_size` | integer | Original size (bytes) |
| `final_size` | integer | Final size (bytes) |
| `action` | string | kept, removed, compacted, deduplicated |
| `compression_method` | string | Method used (if compressed) |
| `reason` | string | Human-readable reason |
| `similarity_to_kept` | float | Similarity score (for dedup) |

### Example

```json
{
 "request_id": "550e8400-e29b-41d4-a716-446655440000",
 "timestamp": "2026-03-10T06:00:00.123456Z",
 "input_block_count": 20,
 "input_blocks_by_type": {
 "instruction": 5,
 "knowledge": 10,
 "evidence": 5
 },
 "input_total_size": 50000,
 "output_block_count": 15,
 "output_blocks_by_type": {
 "instruction": 5,
 "knowledge": 8,
 "evidence": 2
 },
 "output_total_size": 35000,
 "blocks_audited": [
 {
 "block_id": "block-k-1",
 "block_type": "knowledge",
 "original_size": 1000,
 "final_size": 0,
 "action": "removed",
 "compression_method": null,
 "reason": "Duplicate of block-k-2"
 },
 {
 "block_id": "block-e-1",
 "block_type": "evidence",
 "original_size": 2000,
 "final_size": 1000,
 "action": "compacted",
 "compression_method": "truncation",
 "reason": "Truncated to 500 tokens"
 }
 ],
 "compression_methods_used": {
 "truncation": 3,
 "deduplication": 2
 },
 "parse_latency_ms": 5.0,
 "compile_latency_ms": 30.0,
 "render_latency_ms": 5.0,
 "total_latency_ms": 40.0,
 "compression_ratio": 0.7,
 "tokens_removed": 5000,
 "errors": []
}
```

## CacheAudit

Audit trail for `/cache/*` requests.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | UUID | Links to LogRecord |
| `timestamp` | ISO 8601 | Audit timestamp |
| `operation` | string | get, set, invalidate, clear |
| `block_id` | string | Block ID (null for clear) |
| `cache_hit` | boolean | Cache hit (for get) |
| `cached_value_size` | integer | Size of cached value |
| `ttl_seconds` | integer | Time-to-live (for set) |
| `message` | string | Operation details |

### Example

```json
{
 "request_id": "550e8400-e29b-41d4-a716-446655440000",
 "timestamp": "2026-03-10T06:00:00.123456Z",
 "operation": "get",
 "block_id": "block-k-1",
 "cache_hit": true,
 "cached_value_size": 1000,
 "ttl_seconds": null,
 "message": "Cache hit for block-k-1"
}
```

## MetricsAudit

Audit trail for `/metrics` requests.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | UUID | Links to LogRecord |
| `timestamp` | ISO 8601 | Audit timestamp |
| `aggregation_window` | string | Time window (1h, 24h, etc.) |
| `data_points_returned` | integer | Number of data points |
| `metrics_included` | array | List of metric names |

### Example

```json
{
 "request_id": "550e8400-e29b-41d4-a716-446655440000",
 "timestamp": "2026-03-10T06:00:00.123456Z",
 "aggregation_window": "24h",
 "data_points_returned": 1440,
 "metrics_included": [
 "compression_ratio",
 "latency_p50",
 "latency_p95",
 "latency_p99",
 "blocks_removed",
 "requests_per_second",
 "cache_hit_rate"
 ]
}
```

## Type Reference

### LogLevel

- `debug` — Detailed tracing information
- `info` — General informational messages
- `warn` — Warning messages (unusual but not error)
- `error` — Error messages (request failed)

### Destination

- `file` — Write to `~/.tokenpak/logs/proxy-YYYY-MM-DD.log`
- `stdout` — Write to standard output
- `syslog` — Write to system syslog

### CacheOperation

- `get` — Retrieve from cache
- `set` — Store in cache
- `invalidate` — Invalidate specific block
- `clear` — Clear all cache
