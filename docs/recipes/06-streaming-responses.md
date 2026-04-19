# Recipe: Streaming Responses

**What this solves:** Use TokenPak with streaming-capable clients to receive responses token-by-token instead of waiting for the full response, improving perceived latency and enabling real-time UX.

## Prerequisites
- TokenPak installed
- A streaming-aware client (curl with `--no-buffer`, Python `requests-stream`, Node.js streams)
- Understanding of server-sent events (SSE) or chunked transfer encoding
- API keys for providers

## Config Snippet

```yaml
# config.yaml
streaming:
  enabled: true
  # Support both SSE (server-sent events) and chunked encoding
  formats: [sse, chunked]

  # Buffer size: larger = fewer roundtrips, smaller = lower latency
  buffer_tokens: 5  # Send every 5 tokens or every 500ms, whichever comes first
  flush_interval_ms: 500

  # Streaming-aware batching (optional)
  # If multiple streaming requests come in, batch them for efficiency
  max_concurrent_streams: 100

providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}
    # OpenAI supports streaming natively
    supports_streaming: true

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    supports_streaming: true

models:
  gpt-4: { provider: openai }
  gpt-4-streaming: { provider: openai, stream: true }
  claude-opus: { provider: anthropic }
  claude-opus-streaming: { provider: anthropic, stream: true }
```

## Test & Verify

**Step 1:** Validate config:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Streaming enabled (SSE + chunked)
# ✓ Streaming models available
```

**Step 2:** Start TokenPak proxy:
```bash
tokenpak proxy --config config.yaml
```

**Step 3:** Test streaming with curl (server-sent events):
```bash
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4-streaming",
    "stream": true,
    "messages": [
      {"role": "user", "content": "Write a haiku about API proxies"}
    ]
  }' \
  --no-buffer

# Expected output (streaming):
# data: {"delta":{"type":"content_block_delta","delta":{"type":"text_delta","text":"API"}}}
# data: {"delta":{"type":"content_block_delta","delta":{"type":"text_delta","text":" proxies"}}}
# data: {"delta":{"type":"content_block_delta","delta":{"type":"text_delta","text":" pass"}}}
# ...
# (tokens arrive in real-time, one by one)
```

**Step 4:** Test streaming with Python (chunked response):
```python
import requests

response = requests.post(
    'http://localhost:8000/v1/messages',
    json={
        'model': 'gpt-4-streaming',
        'stream': True,
        'messages': [
            {'role': 'user', 'content': 'Write a 2-sentence story'}
        ]
    },
    stream=True  # Enable streaming
)

# Iterate over streaming chunks
for line in response.iter_lines():
    if line:
        print(f"Received: {line.decode('utf-8')}")

# Expected output:
# Received: data: {"delta":{"text":"Once"}}
# Received: data: {"delta":{"text":" upon"}}
# Received: data: {"delta":{"text":" a"}}
# ...
```

**Step 5:** Test streaming with Node.js:
```javascript
const fetch = require('node-fetch');

async function streamResponse() {
  const response = await fetch('http://localhost:8000/v1/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'gpt-4-streaming',
      stream: true,
      messages: [{ role: 'user', content: 'Count to 5' }]
    })
  });

  // Read streaming response
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value);
    process.stdout.write(chunk);
  }
}

streamResponse();
// Expected output: tokens streaming in real-time
```

**Step 6:** Measure latency improvement:
```bash
# Non-streaming (wait for full response)
time curl -X POST http://localhost:8000/v1/messages \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Write 500 words"}]}' \
  -s > /dev/null
# Result: real 2.45s (full response time)

# Streaming (first token appears sooner)
time curl -X POST http://localhost:8000/v1/messages \
  -d '{"model": "gpt-4-streaming", "stream": true, "messages": [{"role": "user", "content": "Write 500 words"}]}' \
  --no-buffer -s | head -1
# Result: real 0.28s (time to first token)
# User sees response starting in 280ms instead of waiting 2.45s!
```

## What Just Happened

TokenPak streamed the response back to your client in real-time:

1. **Client sends** request with `stream: true` flag
2. **TokenPak proxies** to OpenAI/Anthropic with streaming enabled
3. **As tokens arrive**, TokenPak immediately forwards them to your client
4. **Client receives** deltas (token fragments) and can display them as they arrive
5. **Full response** completes, connection closes cleanly

Your UI can display tokens as they arrive, making the experience feel 5-10x faster than waiting for a full response.

## Common Pitfalls

**Pitfall 1: Buffering too much**
- ❌ Wrong: `buffer_tokens: 1000` (waits for 1000 tokens before sending)
- ✅ Right: `buffer_tokens: 5` - 10 (low latency, reasonable efficiency)

**Pitfall 2: Not handling connection drops**
- ❌ Wrong: Assume streaming connection never breaks
- ✅ Right: Implement reconnect logic: exponential backoff, state tracking

**Pitfall 3: Client doesn't handle streaming format**
- ❌ Wrong: Try to `json.parse()` streaming SSE (it's not valid JSON)
- ✅ Right: Parse SSE line-by-line: `data: {json}`, extract JSON payload

**Pitfall 4: Streaming and non-streaming models mixed**
- ❌ Wrong: Same model name used for both streaming and non-streaming
- ✅ Right: Separate models: `gpt-4` (non-streaming) vs `gpt-4-streaming`

**Pitfall 5: Missing flush on timeout**
- ❌ Wrong: Tokens buffered for 500ms, but response finishes in 200ms (stuck tokens)
- ✅ Right: Flush on timeout OR `buffer_tokens` reached, whichever comes first

**Pitfall 6: Not measuring time-to-first-token**
- ❌ Wrong: Report "response time" as total time (includes buffering)
- ✅ Right: Track TTFT separately from total time for true latency insight
