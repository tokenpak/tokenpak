# TokenPak Quickstart — First Savings in 5 Minutes

Get TokenPak running and see your first cost savings in under 5 minutes.

## 1. Install

```bash
pip install tokenpak
```

Requires Python 3.8+.

## 2. Start the Proxy

```bash
tokenpak start
```

Expected output:
```
TokenPak proxy running at http://localhost:8766
Ready to intercept API requests
```

The proxy is now listening for requests.

## 3. Point Your Client at the Proxy

Choose your integration method:

### OpenClaw Users
Edit your `openclaw.json`:
```json
{
  "models": {
    "tokenpak-anthropic/claude-sonnet-4-6": {
      "provider": "anthropic",
      "base_url": "http://localhost:8766",
      "apiKey": "$ANTHROPIC_API_KEY"
    }
  }
}
```

### Direct API Calls
```bash
export ANTHROPIC_BASE_URL=http://localhost:8766
```

### Any OpenAI-Compatible Client
```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8766",
    api_key="your-key-here"
)
```

## 4. Verify It's Working

```bash
tokenpak status
```

Check the health endpoint:
```bash
curl http://localhost:8766/health
```

Expected response: `{"status": "healthy"}`

## 5. See Your Savings

Make a few API requests through the proxy, then run:

```bash
tokenpak savings
```

Or check the stats endpoint:
```bash
curl http://localhost:8766/stats
```

### What You'll See
- **Cache hit ratio:** Climbs toward 80-90% as patterns repeat
- **Token compression:** Compressed tokens vs. original token count
- **Cost per request:** Decreasing with each cached/compressed hit
- **Total savings:** Running tally of reduced API charges

## 6. Keep It Running

For continuous savings, keep the proxy running in the background:

```bash
nohup tokenpak start > ~/.tokenpak/proxy.log 2>&1 &
echo $! > ~/.tokenpak/proxy.pid
```

To stop:
```bash
kill $(cat ~/.tokenpak/proxy.pid)
```

---

## Next Steps

- **Tune cache settings:** See `tokenpak config` for TTL and compression options
- **Monitor savings:** Visit `http://localhost:8766/dashboard` for a live view
- **Full docs:** See `GETTING-STARTED.md` for advanced config and troubleshooting

## Troubleshooting

**"Connection refused" on `http://localhost:8766`**
- Verify the proxy started: `tokenpak status`
- Check port 8766 isn't in use: `lsof -i :8766`

**"API key invalid" errors**
- Ensure `ANTHROPIC_API_KEY` is set: `echo $ANTHROPIC_API_KEY`
- Proxy is transparent — your API key must be valid

**No savings showing**
- Cache and compression need multiple requests to show ROI
- First request always passes through uncached
- Check `tokenpak stats` for live hit counts
