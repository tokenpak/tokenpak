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
4. The response comes back (with optional stats footer)
5. Cost and token data are recorded locally

Your API key is in the `Authorization` header and passes through untouched. TokenPak never reads, stores, or logs credentials.

---

## Start the Proxy

```bash
tokenpak serve
# Default: port 8766, hybrid compression mode
```

**Options:**

```bash
tokenpak serve \
  --port 8766 \
  --mode hybrid \      # strict | hybrid | aggressive
  --daemon             # background mode
```

**Compression modes:**

| Mode | When to use |
|------|-------------|
| `strict` | Only compress when clearly beneficial (>4500 tokens) |
| `hybrid` | Balance compression and latency (recommended) |
| `aggressive` | Maximum compression, every request |

---

## Provider Auto-Detection

TokenPak detects the target provider from your request headers and routes accordingly:

| Your `Authorization` format | Routes to |
|----------------------------|-----------|
| `Bearer sk-ant-...` | `api.anthropic.com` |
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

Control what gets compressed via `~/.tokenpak/config.json`:

```json
{
  "compression": {
    "enabled": true,
    "level": "balanced",
    "threshold_tokens": 4500,
    "preserve_code": true,
    "preserve_json": false
  }
}
```

Or via CLI:

```bash
tokenpak config set compression.level aggressive
tokenpak config set compression.threshold_tokens 2000
```

---

## Response Footer (Stats Injection)

By default, TokenPak appends a one-line stats footer to each response:

```
[TokenPak: 4,231→2,847 tokens | saved 33% | $0.004]
```

Disable it:

```bash
tokenpak config set proxy.stats_footer false
```

---

## Environment Variables

Override any config value with env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_MODE` | `hybrid` | Compression mode |
| `TOKENPAK_COMPACT` | `1` | Master compression switch (0/1) |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | Min tokens to trigger compression |
| `TOKENPAK_DB` | `~/.tokenpak/monitor.db` | Database path |

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
