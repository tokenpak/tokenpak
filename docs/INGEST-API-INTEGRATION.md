# TokenPak Ingest API Integration into tokenpak.proxy.py

## Overview

The ingest API has been successfully migrated from the legacy standalone modules into `tokenpak.proxy.py`. This provides a unified proxy server that handles:

- LLM request forwarding and optimization (v3/v1beta endpoints)
- Ollama proxy (ollama-proxy routes)
- Agent usage data ingestion (/ingest, /ingest/batch)

## Endpoints

### POST /ingest

Ingest a single usage entry.

**Request:**
```json
{
  "model": "claude-3-opus-20240229",
  "tokens": 5000,
  "cost": 0.15,
  "timestamp": "2026-03-10T15:37:00Z",
  "agent": "my-agent",
  "provider": "anthropic",
  "session_id": "sess-abc123",
  "extra": {
    "custom_field": "value"
  }
}
```

**Fields:**
- `model` (required, string): Model name
- `tokens` (required, int ≥ 0): Total tokens used
- `cost` (required, float ≥ 0): Cost in USD
- `timestamp` (optional, ISO 8601): Event timestamp (defaults to current UTC time)
- `agent` (optional, string): Agent name
- `provider` (optional, string): LLM provider
- `session_id` (optional, string): Session identifier
- `extra` (optional, object): Additional metadata
- Any additional fields are accepted via model_config

**Response (200 OK):**
```json
{
  "status": "ok",
  "ids": ["550e8400-e29b-41d4-a716-446655440000"]
}
```

**Error Responses:**
- `400 Bad Request` — malformed JSON, missing/invalid fields
- `413 Payload Too Large` — request body > 1MB
- `422 Unprocessable Entity` — validation failure (non-integer tokens, negative cost, etc.)
- `500 Internal Server Error` — storage write failure

---

### POST /ingest/batch

Ingest multiple entries atomically.

**Request:**
```json
{
  "events": [
    {
      "model": "claude-3-opus-20240229",
      "tokens": 5000,
      "cost": 0.15
    },
    {
      "model": "gpt-4-turbo",
      "tokens": 3000,
      "cost": 0.12
    }
  ]
}
```

**Fields:**
- `events` (required, array): List of entry objects (same schema as /ingest)
  - Max 1000 entries per batch
  - Min 1 entry required

**Response (200 OK):**
```json
{
  "status": "ok",
  "ids": ["id1", "id2"],
  "errors": null
}
```

If some entries fail but others succeed, successful entries are written and errors are reported:
```json
{
  "status": "ok",
  "ids": ["id1"],
  "errors": [
    "event[1]: tokens must be non-negative int"
  ]
}
```

**Error Responses:**
- `400 Bad Request` — missing events field, empty list, invalid format
- `413 Payload Too Large` — request body > 1MB
- `422 Unprocessable Entity` — all entries failed validation
- `500 Internal Server Error` — storage write failure

---

## Storage

Ingest entries are stored in append-only JSONL files:

```
~/vault/.tokenpak/entries/YYYY-MM-DD.jsonl
```

Each line is a valid JSON object with all submitted fields plus:
- `id` — UUID assigned at write time (or provided in request)

Example file content:
```jsonl
{"id":"550e8400-e29b-41d4-a716-446655440000","model":"claude-3-opus","tokens":5000,"cost":0.15,"timestamp":"2026-03-10T15:37:00+00:00"}
{"id":"6ba7b810-9dad-11d1-80b4-00c04fd430c8","model":"gpt-4","tokens":3000,"cost":0.12,"timestamp":"2026-03-10T15:37:01+00:00"}
```

---

## Request Validation

### Single Entry (/ingest)

1. **Structure:** Must be a JSON object
2. **Required fields:** model, tokens, cost
3. **Type checks:**
   - `model` — non-empty string
   - `tokens` — non-negative integer
   - `cost` — non-negative number (int or float)
   - `timestamp` — ISO 8601 string (if provided)
4. **Timestamp format:** Must be valid ISO 8601 (e.g., `2026-03-10T15:37:00Z` or `2026-03-10T15:37:00+00:00`)

### Batch Entry (/ingest/batch)

1. **Structure:** Must be a JSON object with `events` key
2. **Events array:**
   - Required, non-empty list
   - Max 1000 entries
   - Each entry validated as above
3. **Partial failure handling:**
   - Individual entry errors don't stop the batch
   - All valid entries are written to JSONL
   - Errors are returned alongside successful IDs

---

## Error Shapes

All errors follow a consistent JSON structure:

**Single entry error:**
```json
{
  "error": "human-readable error message"
}
```

**Batch error (all failed):**
```json
{
  "error": "all events failed: event[0]: ...; event[1]: ..."
}
```

---

## Integration with tokenpak.proxy.py

### Routing

The proxy detects and routes ingest requests early in `do_POST()`:

```python
if self.path == "/ingest" or self.path == "/ingest/batch":
    self._ingest(self.path)
```

### Session Tracking

Ingest entries are tracked in the global SESSION dict:
```python
SESSION["ingest_entries"]  # Count of successfully written entries
```

This is included in health/stats responses for monitoring.

---

## Testing

Run the full test suite:

```bash
cd /home/trix/Projects/tokenpak
python3 -m pytest tests/test_ingest_tokenpak.proxy.py -v
```

Tests cover:
- ✅ Single entry write and persistence
- ✅ Batch writes with multiple entries
- ✅ Timestamp-based JSONL file routing
- ✅ Input validation (required fields, types, ranges)
- ✅ JSONL format correctness
- ✅ Error handling and reporting
- ✅ Optional field acceptance
- ✅ Entry ID generation and preservation

---

## Usage Examples

### Using curl

**Single entry:**
```bash
curl -X POST http://localhost:8766/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-opus",
    "tokens": 1000,
    "cost": 0.03,
    "agent": "my-agent"
  }'
```

**Batch entries:**
```bash
curl -X POST http://localhost:8766/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {"model": "claude-3-opus", "tokens": 1000, "cost": 0.03},
      {"model": "gpt-4", "tokens": 500, "cost": 0.02}
    ]
  }'
```

### Using Python

```python
import requests

# Single entry
resp = requests.post(
    "http://localhost:8766/ingest",
    json={
        "model": "claude-3-opus",
        "tokens": 1000,
        "cost": 0.03,
        "agent": "my-agent"
    }
)
print(resp.json())  # {"status": "ok", "ids": [...]}

# Batch
resp = requests.post(
    "http://localhost:8766/ingest/batch",
    json={
        "events": [
            {"model": "claude-3-opus", "tokens": 1000, "cost": 0.03},
            {"model": "gpt-4", "tokens": 500, "cost": 0.02}
        ]
    }
)
print(resp.json())  # {"status": "ok", "ids": [...]}
```

---

## Implementation Details

### File Structure in tokenpak.proxy.py

1. **Handler methods** (class ForwardProxyHandler):
   - `_ingest(path)` — Router for /ingest endpoints
   - `_ingest_single(payload)` — Handle single entry requests
   - `_ingest_batch(payload)` — Handle batch requests

2. **Storage function** (module-level):
   - `_ingest_write_entry(entry)` — Write entry to JSONL, return ID

3. **Configuration**:
   - `INGEST_ENTRIES_DIR` — ~/vault/.tokenpak/entries
   - Entries grouped by date (timestamp.split('T')[0])

---

## Migration Notes

### From Legacy API

The legacy API used FastAPI and ran as a standalone service. The tokenpak.proxy integration:

- ✅ Preserves all endpoint behavior
- ✅ Reuses the same storage format (JSONL)
- ✅ Maintains validation logic
- ✅ Simplifies deployment (one process instead of two)
- ✅ Adds session tracking/metrics

### Compatibility

- Existing clients using `/ingest` endpoints continue to work unchanged
- Storage location and format are identical
- No breaking changes to request/response schemas

---

## Monitoring

Check ingest activity via `/health` or `/stats`:

```bash
curl http://localhost:8766/health
```

Look for:
```json
{
  "stats": {
    "ingest_entries": 42,
    ...
  }
}
```

---

## Future Enhancements

Potential improvements:
- [ ] Rate limiting per agent/provider
- [ ] Cost aggregation/reporting endpoints
- [ ] Data export/backup tools
- [ ] Real-time ingest stream (Server-Sent Events)
- [ ] Batch acknowledgment with retry tokens
