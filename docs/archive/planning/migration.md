# Migrating to TokenPak

This guide walks you through migrating from raw API calls to TokenPak in **5 steps**. Takes ~15 minutes.

## Before You Start

- TokenPak runs as a proxy on `localhost:8766` (or your custom port)
- All your code changes are **backwards compatible** — if you need to, you can revert
- No API key changes required

## Step 1: Install TokenPak

```bash
pip install tokenpak
```

## Step 2: Start the TokenPak Proxy

### Option A: Long-running Proxy (Recommended)
```bash
tokenpak proxy --port 8766
```

The proxy will:
- Listen on `127.0.0.1:8766`
- Forward requests to OpenAI, Anthropic, Google, etc.
- Cache responses
- Compress tokens
- Log all activity

### Option B: Docker
```bash
docker run -p 8766:8766 -v ~/.tokenpak/config.yml:/config.yml tokenpak:latest
```

## Step 3: Update Your API Endpoint

### Before (Raw API)
```python
import anthropic

client = anthropic.Anthropic(api_key="sk-ant-...")

response = client.messages.create(
    model="claude-3-sonnet-20250319",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### After (TokenPak)
```python
import anthropic

client = anthropic.Anthropic(
    api_key="sk-ant-...",
    base_url="http://localhost:8766"  # Point to TokenPak proxy
)

response = client.messages.create(
    model="claude-3-sonnet-20250319",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

**That's it.** Your code works the same way. TokenPak intercepts the request, optimizes it, and forwards it.

## Step 4: Common Patterns

### OpenAI
```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8766"
)
```

### Google Generative AI
```python
import anthropic

# TokenPak proxies Google requests too
client = anthropic.Anthropic(
    api_key="google-api-key",
    base_url="http://localhost:8766"
)
```

### Using Environment Variables
```bash
export OPENAI_API_BASE="http://localhost:8766"
```

Then your code automatically uses the proxy:
```python
from openai import OpenAI
client = OpenAI()  # Uses OPENAI_API_BASE env var
```

## Step 5: Verify It's Working

```bash
# Check TokenPak proxy is running
curl http://localhost:8766/health

# Should return:
# {"status": "healthy", "uptime_seconds": 123}
```

Check proxy logs:
```bash
tokenpak logs --follow
```

You should see requests being processed:
```
2026-03-25 14:30:12 | POST /v1/messages | anthropic | 200 | 1250 tokens | 5ms
2026-03-25 14:30:15 | POST /v1/chat/completions | openai | 200 | 800 tokens | 3ms
```

## Validation Checklist

- [ ] TokenPak proxy is running (`tokenpak status`)
- [ ] Your code points to `http://localhost:8766`
- [ ] First request succeeds (check logs)
- [ ] Response latency is acceptable (<100ms added)
- [ ] Tokens compressed (check `tokenpak metrics`)
- [ ] No errors in TokenPak logs

## Rollback

If something goes wrong, simply remove the `base_url` override:

```python
# Rollback — goes directly to provider
client = anthropic.Anthropic(api_key="sk-ant-...")
```

## Advanced: Configuration

TokenPak config file location: `~/.tokenpak/config.yml`

```yaml
proxy:
  port: 8766
  host: 127.0.0.1

compression:
  enabled: true
  threshold_tokens: 100  # Compress if >100 tokens

cache:
  enabled: true
  ttl_seconds: 3600

routing:
  fallback:
    - openai
    - anthropic

logging:
  level: info
  format: json
```

Reload config without restarting:
```bash
tokenpak config reload
```

## Metrics & Monitoring

After 10+ requests, check metrics:

```bash
tokenpak metrics
```

Output:
```
Requests: 152
Total tokens sent: 8,430
Total tokens received: 4,210
Compression savings: 2,015 tokens (19%)
Cost saved: $0.42
Top provider: anthropic (62%)
```

## Troubleshooting

### "Connection refused" on localhost:8766
- [ ] Is TokenPak running? (`tokenpak status`)
- [ ] Is port 8766 correct? Check with `netstat -tulpn | grep 8766`
- [ ] Try a different port: `tokenpak proxy --port 9000`

### "API key rejected"
- [ ] Verify the API key is correct
- [ ] Check TokenPak logs: `tokenpak logs --tail 50`
- [ ] Ensure provider supports the model you're using

### Slower latency than expected
- [ ] Check network: `ping localhost:8766`
- [ ] Run benchmark: `tokenpak benchmark --duration 10s`
- [ ] Check TokenPak CPU/memory: `tokenpak status`

### Compression not working
- [ ] Check config: `tokenpak config get compression`
- [ ] Monitor compression: `tokenpak metrics --watch`
- [ ] Compression requires identical previous requests (caching)

## Next Steps

- **[Configuration Guide](api-reference.md)** — Advanced settings
- **[Observability](observability.md)** — Set up monitoring
- **[Plugin Guide](plugin-guide.md)** — Extend TokenPak
- **[Production Guide](production-sla.md)** — Deploy to production

## FAQ

**Q: Do I need to change my API keys?**
A: No. TokenPak uses your existing API keys and passes them to providers.

**Q: Can I use TokenPak with multiple providers?**
A: Yes. Configure multiple providers and TokenPak will route based on your config.

**Q: What if the proxy goes down?**
A: Your app will fail with a connection error. Use systemd or Docker to auto-restart.

**Q: Can I run TokenPak on a remote server?**
A: Yes. Update `base_url` to the remote address:
```python
client = anthropic.Anthropic(base_url="http://192.168.1.100:8766")
```

**Q: How much faster is TokenPak?**
A: Latency overhead is ~5-10ms per request. Compression saves 20-40% on tokens (costs).
