# TokenPak API Documentation

## Endpoints

### HTTP REST API

#### `POST /v1/messages`
**Backward compatible Anthropic Messages API endpoint**

Forward requests to upstream Anthropic API with optional compression and vault context injection.

Request:
```json
{
  "model": "claude-3-opus-20250219",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "max_tokens": 1024
}
```

Response:
```json
{
  "id": "msg_123",
  "type": "message",
  "role": "assistant",
  "content": [...],
  "usage": {
    "input_tokens": 10,
    "output_tokens": 50
  }
}
```

**Features:**
- Automatic context injection from vault index (if available)
- Token compression via compaction pipeline
- Optional cache poisoning removal (timestamps, UUIDs)
- Protected content detection and preservation
- Cost estimation and metrics collection

**Headers:**
- `X-OpenClaw-Session`: (optional) Session ID for context caching
- `Content-Type`: application/json

**Status Codes:**
- `200` Success
- `400` Invalid request JSON or missing required fields
- `413` Request body exceeds 10MB limit
- `429` Rate limited (60 req/min per IP)
- `502` Upstream unavailable
- `503` Provider circuit breaker open (too many failures)

---

### WebSocket API

#### `GET /ws` (WebSocket upgrade)
**Streaming endpoint with compression support**

Establish a WebSocket connection for streaming message responses with optional gzip compression applied in-flight.

**Connection:**
```
ws://localhost:8766/ws
```

**Protocol:**
1. Client initiates WebSocket upgrade
2. Client sends initial JSON message with `model`, `messages`, and optional parameters
3. Server forwards to upstream with optional compaction
4. Server streams compressed chunks back to client (binary frames)
5. Client receives decompressed events
6. Connection closes cleanly with appropriate WebSocket close code

**Client Request Format:**
```json
{
  "model": "claude-3-opus-20250219",
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"},
    {"role": "user", "content": "How are you?"}
  ],
  "max_tokens": 1024,
  "stream": true
}
```

Note: `stream` is always forced to `true` by the WebSocket handler; clients do not need to set it.

**Server Response Format:**
Streamed as compressed JSON objects, one per line:

```json
{"type":"message_start","message":{"id":"msg_123","role":"assistant"}}
{"type":"content_block_start","index":0,"content_block":{"type":"text"}}
{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}
{"type":"message_delta","delta":{"stop_reason":"end_turn"}}
{"type":"message_stop"}
{"type":"stats","usage":{"input_tokens":20,"output_tokens":50,"cache_read_tokens":0,"cache_creation_tokens":0}}
```

**Compression Details:**
- Each event is individually compressed with gzip before sending
- Client receives binary frames (compressed)
- Client must decompress with gzip before parsing JSON
- Typical compression ratio: 20-50% for streaming SSE events

**Error Handling:**
On error, server sends a single JSON error message before closing:

```json
{"error":{"type":"validation_error","message":"Missing required fields: model, messages"}}
```

**Connection Limits:**
- Maximum 50 concurrent WebSocket connections (configurable)
- Excess connections receive close code 1008 with reason "Connection limit exceeded"

**Close Codes:**
- `1000` Normal closure (completion or client disconnect)
- `1003` Invalid message format (invalid JSON)
- `1008` Connection limit exceeded
- `1011` Internal server error or upstream error

**Error Responses:**
- `validation_error`: Missing or invalid request fields
- `invalid_json`: Request body is not valid JSON
- `upstream_error`: Error forwarding to upstream API
- `streaming_error`: Error during streaming response
- `authentication_error`: Invalid API key or credentials

**Python Client Example:**
```python
import asyncio
import websockets
import json
import gzip

async def chat_with_ws():
    uri = "ws://localhost:8766/ws"
    async with websockets.connect(uri) as websocket:
        # Send request
        request = {
            "model": "claude-3-opus-20250219",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100
        }
        await websocket.send(json.dumps(request))
        
        # Receive streamed response
        while True:
            compressed = await websocket.recv()
            if isinstance(compressed, bytes):
                # Decompress
                json_str = gzip.decompress(compressed).decode("utf-8")
                event = json.loads(json_str)
                print(f"Event: {event['type']}")
                
                if event.get("type") == "message_stop":
                    break

asyncio.run(chat_with_ws())
```

**JavaScript/TypeScript Client Example:**
```typescript
async function chatWithWS() {
  const ws = new WebSocket("ws://localhost:8766/ws");
  
  ws.onopen = () => {
    const request = {
      model: "claude-3-opus-20250219",
      messages: [{role: "user", content: "Hello"}],
      max_tokens: 100
    };
    ws.send(JSON.stringify(request));
  };
  
  ws.onmessage = async (event) => {
    // event.data is a Blob (binary compressed frame)
    const compressed = await event.data.arrayBuffer();
    
    // Decompress using pako (gzip library)
    const json_str = pako.ungzip(new Uint8Array(compressed), {to: 'string'});
    const chunk = JSON.parse(json_str);
    console.log("Event:", chunk.type);
  };
  
  ws.onerror = (error) => console.error("WebSocket error:", error);
  ws.onclose = () => console.log("Connection closed");
}
```

---

### Monitoring & Debug Endpoints

#### `GET /health`
**Health check endpoint**

Returns proxy status, feature availability, and basic metrics.

Response:
```json
{
  "status": "ok",
  "compilation_mode": "hybrid",
  "vault_index": {
    "available": true,
    "blocks": 150,
    "path": "/home/user/vault/.tokenpak"
  },
  "router": {
    "enabled": true,
    "components": {
      "slot_filler": true,
      "recipe_engine": true,
      "validation_gate": false
    }
  },
  "stats": {
    "requests": 1250,
    "errors": 3,
    "saved_tokens": 45000,
    "cache_hits": 120
  }
}
```

#### `GET /stats`
**Detailed session statistics**

Returns comprehensive metrics including compression, vault injection, and model breakdown.

Response:
```json
{
  "session": {
    "requests": 1250,
    "input_tokens": 500000,
    "saved_tokens": 125000,
    "output_tokens": 250000,
    "cost": 12.50,
    "injected_tokens": 50000,
    "cache_read_tokens": 20000
  },
  "today": {
    "requests": 1250,
    "input_tokens": 500000,
    "total_cost": 12.50,
    "cache_hits": 120
  },
  "by_model": {
    "claude-opus": {...},
    "gpt-4o": {...}
  }
}
```

#### `GET /stats/last`
**Last request statistics**

Returns metrics for the most recent request only.

#### `GET /stats/session`
**Session aggregates**

Returns overall session statistics including uptime and average savings.

#### `GET /vault`
**Vault index debug endpoint**

Returns detailed vault index state:
```json
{
  "available": true,
  "blocks": 150,
  "total_tokens": 245000,
  "path": "/home/user/vault/.tokenpak",
  "block_list": [
    {
      "block_id": "agents.md#core-truths",
      "source_path": "vault/03_AGENT_PACKS/_shared/SOUL.md",
      "risk_class": "protected",
      "raw_tokens": 450
    }
  ]
}
```

#### `GET /metrics`
**Prometheus metrics export**

Returns metrics in Prometheus text format:
```
# HELP tokenpak_requests_total Total requests processed
# TYPE tokenpak_requests_total counter
tokenpak_requests_total 1250

# HELP tokenpak_tokens_saved_total Total tokens saved by compression
# TYPE tokenpak_tokens_saved_total counter
tokenpak_tokens_saved_total 125000
```

#### `GET /metrics/dashboard`
**Comprehensive dashboard metrics**

Returns 8 key metrics for monitoring:
1. Request count + throughput
2. Latency percentiles (p50, p95, p99)
3. Model provider distribution
4. Smart routing hit rate
5. Cache hit ratio
6. Error rate + top failures
7. Streaming request count
8. 24-hour rolling window stats

#### `GET /trace/last`
**Last pipeline trace**

Returns detailed trace of the last request through the processing pipeline:
```json
{
  "request_id": "1234-5678",
  "timestamp": "14:23:45",
  "model": "claude-opus",
  "input_tokens": 500,
  "output_tokens": 200,
  "stages": [
    {
      "name": "vault_injection",
      "enabled": true,
      "input_tokens": 500,
      "output_tokens": 650,
      "tokens_delta": 150,
      "duration_ms": 45,
      "details": {
        "blocks_matched": 3,
        "tokens_injected": 150
      }
    },
    {
      "name": "compaction",
      "enabled": true,
      "input_tokens": 650,
      "output_tokens": 480,
      "tokens_delta": -170,
      "duration_ms": 120
    }
  ]
}
```

#### `GET /trace/{request_id}`
**Request-specific pipeline trace**

Returns detailed trace for a specific request ID.

#### `GET /traces`
**All recent traces**

Returns list of the last 10 pipeline traces.

---

## Configuration

### Environment Variables

**Core:**
```bash
TOKENPAK_PORT=8766                    # Proxy listen port
TOKENPAK_MODE=hybrid                  # strict|hybrid|aggressive
TOKENPAK_LISTEN_ADDRESS=0.0.0.0       # Listen interface
```

**Compression:**
```bash
TOKENPAK_COMPACT=1                    # Enable/disable compaction
TOKENPAK_COMPACT_MAX_CHARS=120        # Max chars for compacted text
TOKENPAK_COMPACT_THRESHOLD_TOKENS=4500 # Min tokens to trigger compaction
TOKENPAK_COMPACT_CACHE_SIZE=2000      # Compaction cache size
```

**Vault Injection:**
```bash
TOKENPAK_VAULT_INDEX=~/vault/.tokenpak  # Vault index path
TOKENPAK_INJECT_BUDGET=4000           # Max tokens to inject
TOKENPAK_INJECT_TOP_K=5               # Max blocks to inject
TOKENPAK_INJECT_MIN_SCORE=2.0         # Minimum BM25 relevance score
```

**WebSocket:**
```bash
TOKENPAK_WS_MAX_CONNECTIONS=50        # Max concurrent connections
TOKENPAK_WS_ENABLED=1                 # Enable WebSocket server
```

**Rate Limiting:**
```bash
TOKENPAK_RATE_LIMIT_RPM=60            # Requests per minute per IP
```

**Upstream Timeout:**
```bash
TOKENPAK_UPSTREAM_TIMEOUT=300         # Seconds
TOKENPAK_OLLAMA_TIMEOUT=20            # Seconds for Ollama
```

---

## Feature Flags

### Tier 1 (Off by default, safe to enable)
```bash
TOKENPAK_SEMANTIC_CACHE=0             # Duplicate query cache
TOKENPAK_PREFIX_REGISTRY=0            # Stable prefix tracking
TOKENPAK_SKELETON_ENABLED=1           # Code skeleton extraction
TOKENPAK_SHADOW_ENABLED=1             # Coherence validation
```

### Tier 2 (Research/advanced)
```bash
TOKENPAK_ERROR_NORMALIZER=0           # Normalize error responses
TOKENPAK_BUDGET_CONTROLLER=0          # Token budget enforcement
TOKENPAK_SALIENCE_ROUTER=0            # Content-type extraction
TOKENPAK_CACHE_REGISTRY=0             # Unified cache registry
```

### Validation Gate
```bash
TOKENPAK_VALIDATION_GATE=1            # Enable validation gate
TOKENPAK_VALIDATION_GATE_SOFT=1       # Soft mode (warn, don't block)
TOKENPAK_VALIDATION_GATE_BUDGET_CAP=120000  # Max token budget
```

---

## Error Handling

### 4xx Errors (Client)
- `400 Bad Request` - Malformed JSON, missing required fields
- `413 Payload Too Large` - Request body exceeds 10MB
- `429 Too Many Requests` - Rate limit exceeded

### 5xx Errors (Server)
- `502 Bad Gateway` - Upstream unavailable
- `503 Service Unavailable` - Circuit breaker open, provider unreachable

### WebSocket Errors
- `1003` Invalid Message - JSON parse error
- `1008` Policy Violation - Connection limit
- `1011` Server Error - Upstream or internal error

---

## Performance

**Typical Compression:**
- Narrative text: 40-60% reduction
- Protected content: 0% (no compression)
- Code blocks: 20-40% reduction (with skeleton extraction)

**Latency Overhead:**
- Vault injection: 10-50ms (depends on index size)
- Compaction: 50-200ms (depends on message size)
- Total proxy overhead: <500ms for typical messages

**Throughput:**
- HTTP: 100+ req/sec on modern hardware
- WebSocket: 50 concurrent connections @ ~10KB/sec each

**Resource Usage:**
- Memory: ~100-200MB for vault index + in-flight requests
- CPU: Minimal (<5%) when idle, scales linearly with load
- Network: Upstream bandwidth reduced by compression (typically 30-50%)

---

## Backward Compatibility

The `/v1/messages` HTTP endpoint is fully backward compatible with Anthropic Messages API. Existing applications require no changes to use TokenPak proxy:

```bash
# Point your client to the proxy
curl -X POST http://localhost:8766/v1/messages \
  -H "Authorization: Bearer sk-..." \
  -d '{"model":"claude-opus","messages":[...]}'
```

The proxy will:
1. Accept the request as-is
2. Apply optional compaction and vault injection
3. Forward to upstream with proper authentication
4. Return response in standard Anthropic format

No client-side code changes required.
