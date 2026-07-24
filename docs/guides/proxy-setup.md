# Proxy Setup Guide

How to connect any LLM client to TokenPak.

---

## How the Proxy Works

TokenPak acts as an HTTP proxy between your LLM client and the provider API:

```
Your Client → http://localhost:8766 → [Compress] → Provider API → [Stats] → Your Client
```

1. Your client sends a normal API request to `localhost:8766`
2. TokenPak compresses the prompt (if beneficial)
3. The compressed request is forwarded to the real provider
4. The response comes back unchanged by the receipt display
5. Cost and token data are recorded locally

The proxy receives the provider credential header only to forward the request;
it does not persist or log that credential. Anthropic normally uses
`x-api-key`; OpenAI-compatible providers normally use `Authorization`.

---

## Start the Proxy

For a first measured receipt from a real request, use the canonical
[three-command first-receipt path](../first-receipt.md). Its second command is:

```bash
tokenpak serve --profile aggressive --stats-footer
```

The normal defaults remain `balanced` profile with the receipt footer off. The
flags above apply only to that process.

**Supported options:**

```bash
tokenpak serve \
  --port 8766 \
  --profile balanced \
  --stats-footer
```

**Workflow profiles:**

| Profile | When to use |
|---------|-------------|
| `safe` | Conservative operation; not a positive compression-receipt path |
| `balanced` | Normal balance of compression and latency (default) |
| `aggressive` | Maximum eligible compression; used by the reference receipt path |
| `agentic` | Longer-context agent workflows |
| `transparent` | Compatibility passthrough; no positive compression receipt expected |

---

## Provider Auto-Detection

TokenPak detects the target provider from your request headers and routes accordingly:

| Your `Authorization` format | Routes to |
|----------------------------|-----------|
| `x-api-key` or `anthropic-version` header | `api.anthropic.com` |
| `Bearer sk-...` | `api.openai.com` |
| Custom headers | Configurable via `proxy.passthrough_url` |

---

## Setup by Client

### Claude Code

=== "Environment variable"
    ```bash
    export ANTHROPIC_BASE_URL=http://localhost:8766
    claude
    ```

=== "Settings file"
    In `~/.claude/settings.json`:
    ```json
    {
      "env": {
        "ANTHROPIC_BASE_URL": "http://localhost:8766"
      }
    }
    ```

=== "Per-project"
    In `.claude/settings.json` at your project root:
    ```json
    {
      "env": {
        "ANTHROPIC_BASE_URL": "http://localhost:8766"
      }
    }
    ```

---

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-your-key",
    base_url="http://localhost:8766/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

---

### Config-file clients

In your config file:
```json
{
  "providers": {
    "anthropic": {
      "baseUrl": "http://localhost:8766"
    },
    "openai": {
      "baseUrl": "http://localhost:8766/v1"
    }
  }
}
```

---

### Cursor / Copilot

Set the OpenAI base URL in settings:
```
http://localhost:8766/v1
```

---

### Generic HTTP Client

Replace any provider base URL with `http://localhost:8766`. All standard REST API paths work as-is. TokenPak is protocol-transparent.

---

## Multi-Provider Setup

Use different upstream URLs per provider:

```json
{
  "proxy": {
    "port": 8766,
    "providers": {
      "anthropic": { "url": "https://api.anthropic.com" },
      "openai": { "url": "https://api.openai.com" },
      "custom": { "url": "https://your-openai-compatible.api" }
    }
  }
}
```

---

## Compression Configuration

Select a supported workflow profile for one proxy process with `--profile`.
Environment variables remain available for explicit operator overrides:

```bash
TOKENPAK_COMPACT_THRESHOLD_TOKENS=2000 \
  tokenpak serve --profile balanced
```

Leaving both controls unset preserves the normal `balanced` profile.

---

## Local Receipt Footer

The footer is off by default. Enable it for one proxy process with
`--stats-footer`. After each completed request, TokenPak prints a one-line
receipt to the proxy terminal's standard error:

```
⚡ TokenPak: -1,384 tokens (33%) | $0.004 saved
```

It does not alter the provider response. Token savings are measured from that
request's before/after counts; the dollar value is estimated from the local
model-pricing table. To make the setting explicit without a CLI flag, use:

```bash
TOKENPAK_STATS_FOOTER=1 tokenpak serve --profile aggressive
```

The offline `tokenpak demo` fixture does not qualify as a first-request receipt.
Short/protected inputs and byte-preserved routes may correctly report zero.

---

## Environment Variables

Override any config value with env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_MODE` | `hybrid` | Compression mode |
| `TOKENPAK_COMPACT` | `1` | Master compression switch (0/1) |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `1500` in `balanced` | Min tokens to trigger compression |
| `TOKENPAK_DB` | `~/.tokenpak/monitor.db` | Database path |
| `TOKENPAK_PROFILE` | `balanced` | Workflow profile used by the proxy |
| `TOKENPAK_STATS_FOOTER` | `0` | Print per-request receipt to proxy stderr |

---

## Systemd Service (Linux)

Run TokenPak as a system service:

```bash
# Install the service unit
tokenpak service install

# Start and enable
systemctl --user enable tokenpak
systemctl --user start tokenpak

# Check status
systemctl --user status tokenpak
```

The service file is written to `~/.config/systemd/user/tokenpak.service`.

---

## Troubleshooting

**Proxy not reachable:**
```bash
tokenpak doctor
# Checks port binding, config, firewall
```

**Requests not being compressed:**
```bash
tokenpak status --full
# Look for: compression: enabled | mode: hybrid
```

**Higher latency than expected:**
```bash
# Try strict mode (skips compression on small requests)
tokenpak config set proxy.mode strict
```

**API key errors:**
```bash
# Verify passthrough is clean
tokenpak debug on --requests 1
# Make a request, then:
tokenpak debug off
tokenpak trace --last
# Check that Authorization header was forwarded unchanged
```
