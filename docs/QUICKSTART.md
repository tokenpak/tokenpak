# TokenPak — First Savings in 5 Minutes

> **TL;DR:** Install → start → swap `base_url` → see savings.

---

## 1. Install

```bash
pip install "tokenpak[server]"
```

> **Note:** The `[server]` extra includes `fastapi` required to run the proxy. Without it, `tokenpak start` will fail.

Verify your installation:
```bash
python3 -m tokenpak --help
tokenpak status
```

> **Dev/local install:** If working from a repo clone, run `pip install -e . --break-system-packages`
> from the repo root — `pyproject.toml` handles the nested layout automatically.

Requires Python 3.10+.

---

## 2. Configure Your API Key

```bash
export ANTHROPIC_API_KEY=your_key_here
# or for OpenAI:
export OPENAI_API_KEY=your_key_here
```

---

## 3. Start the Proxy

```bash
tokenpak start
```

Expected output:
```
TokenPak proxy running on http://localhost:8766
Compression mode: hybrid (threshold: 4500 tokens)
```

Verify it's up:

```bash
curl http://localhost:8766/health
# {"status":"ok", ...}
```

---

## 4. Point Your Client at the Proxy

One line change in your existing code — no other modifications needed.

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8766
claude
```

### Anthropic Python SDK

```python
import anthropic

client = anthropic.Anthropic(
    api_key="your-anthropic-key",
    base_url="http://localhost:8766",   # ← only change
)

message = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content)
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-openai-key",
    base_url="http://localhost:8766/v1",   # ← only change
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### OpenClaw Users

Edit your `openclaw.json`:
```json
{
  "providers": {
    "anthropic": { "baseUrl": "http://localhost:8766" },
    "openai": { "baseUrl": "http://localhost:8766/v1" }
  }
}
```

Or use a named model entry:
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

### Environment Variable (any client)

```bash
# Anthropic clients
export ANTHROPIC_BASE_URL=http://localhost:8766

# OpenAI-compatible clients
export OPENAI_BASE_URL=http://localhost:8766/v1
```

---

## 5. Check Your Savings

After a few requests:

```bash
tokenpak savings
```

Or from the stats endpoint:

```bash
curl http://localhost:8766/stats
```

Example output:
```
TOKENPAK v1.0.2  |  Status
────────────────────────────────────────

● Proxy: running (port 8766)
  Uptime:          2h 14m
  Requests:        247
  Tokens saved:    12,841 (38.4%)
  Cost:            $0.018
```

First request passes through (cache cold); repeat requests hit 70–85% cache rate; ~20% average compression savings.

Real-time dashboard:

```bash
tokenpak dashboard
```

Or in your browser: `http://localhost:8766/dashboard`

---

## 6. Keep It Running

Run in the background:

```bash
nohup tokenpak start > ~/.tokenpak/proxy.log 2>&1 &
echo $! > ~/.tokenpak/proxy.pid
```

Stop it:

```bash
kill $(cat ~/.tokenpak/proxy.pid)
```

For persistent background service, see the deployment guides under [`../deployments/`](../deployments/) (Docker, docker-compose, Kubernetes, AWS ECS, GCP Cloud Run).

---

## Troubleshooting

```bash
tokenpak doctor   # auto-diagnose common issues
```

| Problem | Fix |
|---------|-----|
| `Connection refused` | Run `tokenpak status` — proxy may not have started |
| Port 8766 in use | `tokenpak start --port 8767` |
| `API key invalid` | Your real API key must be set — TokenPak forwards it transparently |
| No savings showing | Need 5–10 requests to build cache; run `tokenpak savings --days 1` |
| `fastapi` missing | Reinstall with `pip install "tokenpak[server]"` |

---

## Next Steps

- **[Configuration →](configuration.md)** — cache TTL, compression levels, provider routing
- **[FAQ →](faq.md)** — common questions answered
- **[Troubleshooting →](troubleshooting.md)** — common issues and fixes
- **[Architecture →](architecture.md)** — how compression and routing work
- **[Full Documentation →](index.md)** — all guides and references
