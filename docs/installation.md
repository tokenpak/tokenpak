---
title: "installation"
created: 2026-03-24T19:05:55Z
---
# Installation & Setup

Get TokenPak up and running in minutes.

---

## System Requirements

- **Python:** 3.8+ (3.10+ recommended)
- **OS:** Linux, macOS, Windows
- **Disk:** ~50 MB for package + dependencies
- **RAM:** 512 MB minimum (1+ GB recommended for monitoring dashboard)
- **Internet:** Required for API provider access

---

## Step 1: Install via pip

### Basic Installation

```bash
pip install tokenpak
```

### Verify Installation

```bash
tokenpak --version
# Output: tokenpak 0.1.0
```

### (Optional) Install with Extras

TokenPak can optionally integrate with popular frameworks. These are NOT required but make integration easier:

```bash
# For LangChain integration
pip install tokenpak[langchain]

# For CrewAI integration
pip install tokenpak[crewai]

# For agentic frameworks
pip install tokenpak[agentic]

# All extras
pip install tokenpak[all]
```

If you skip extras, you can always add them later by upgrading.

---

## Step 2: Set Your API Keys

TokenPak needs API credentials for the providers you plan to use. Set these as environment variables:

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Get your API key: https://console.anthropic.com

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

Get your API key: https://platform.openai.com/account/api-keys

### Google Gemini

```bash
export GOOGLE_API_KEY="AIza..."
```

Get your API key: https://makersuite.google.com/app/apikey

### (Optional) Save to .env File

For development, create a `.env` file in your project:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
```

Then load it before running:

```bash
set -a
source .env
set +a
```

Or use Python's `python-dotenv`:

```bash
pip install python-dotenv
```

```python
from dotenv import load_dotenv
load_dotenv()  # Loads .env automatically
```

---

## Step 3: Start the Proxy Server

TokenPak runs as a local proxy server on port `8000` (configurable).

### Start the Server

```bash
tokenpak serve
```

**Output:**
```
[2026-03-24 21:20:00] TokenPak Proxy v0.1.0 starting...
[2026-03-24 21:20:00] Listening on http://127.0.0.1:8000
[2026-03-24 21:20:00] Ready to proxy requests
```

The server is now running and ready to accept requests.

### (Optional) Custom Port

```bash
tokenpak serve --port 9000
```

### (Optional) Background Mode

```bash
nohup tokenpak serve &
# or
tokenpak serve &
```

### (Optional) With Dashboard

```bash
tokenpak serve --dashboard
```

Opens a web dashboard at `http://127.0.0.1:8001` to view cost, token counts, and request history.

---

## Step 4: Test the Installation

### Python Client

Create `test_tokenpak.py`:

```python
from tokenpak import Client

# Initialize client pointing to the proxy
client = Client(
    base_url="http://127.0.0.1:8000",  # Your proxy
    api_key="sk-ant-...",  # Your API key
    model="claude-opus-4-6"  # Default model
)

# Make a request
response = client.messages.create(
    model="claude-opus-4-6",
    messages=[
        {"role": "user", "content": "Say 'TokenPak is working!' in one sentence."}
    ],
    max_tokens=100
)

print("✅ TokenPak works!")
print(f"Response: {response.content[0].text}")
```

Run it:

```bash
python test_tokenpak.py
```

**Expected output:**
```
✅ TokenPak works!
Response: TokenPak is working!
```

### Using curl

```bash
curl -X POST http://127.0.0.1:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-ant-..." \
  -d '{
    "model": "claude-opus-4-6",
    "max_tokens": 100,
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  }'
```

---

## Step 5: (Optional) Basic Configuration

TokenPak works out-of-the-box with defaults, but you can customize it with a `config.yaml` file.

### Create config.yaml

```yaml
# config.yaml

# Proxy settings
proxy:
  port: 8000
  host: 127.0.0.1

# Default provider
provider: anthropic  # or: openai, google, passthrough

# Fallback chain (try these if primary fails)
fallback:
  - anthropic
  - google
  - openai

# Compression settings
compression:
  enabled: true
  min_tokens: 1000  # Only compress if >1000 tokens

# Telemetry (cost tracking, logging)
telemetry:
  enabled: true
  log_file: "/tmp/tokenpak.log"

# Vault integration (optional)
vault:
  enabled: false
  root: "~/my-vault"
  index_file: "~/.tokenpak/vault-index.json"
```

Then run:

```bash
tokenpak serve --config config.yaml
```

See [Feature Matrix](./features.md) for full configuration options.

---

## Troubleshooting

### "ImportError: No module named 'tokenpak'"

**Cause:** TokenPak not installed
**Fix:**
```bash
pip install tokenpak
```

### "Connection refused (127.0.0.1:8000)"

**Cause:** Proxy server not running
**Fix:**
```bash
tokenpak serve
# (in another terminal)
```

### "ANTHROPIC_API_KEY not set"

**Cause:** Environment variable missing
**Fix:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Then start the proxy
tokenpak serve
```

### "Port 8000 already in use"

**Cause:** Another process using port 8000
**Fix:**
```bash
# Use a different port
tokenpak serve --port 9000

# Or kill the process using 8000
lsof -i :8000
kill <pid>
```

### "YAML parsing error in config.yaml"

**Cause:** Malformed YAML syntax
**Fix:**
- Check indentation (use spaces, not tabs)
- Validate at https://yamllint.com/
- See example `config.yaml` above

For more help, see [Error Handling Guide](./error-handling.md).

---

## Next Steps

✅ **Installation complete!**

- **Quick start:** Read the [Quick Start Guide](./QUICKSTART.md)
- **Integrate with a framework:** See [Adapter Reference](./adapters.md)
- **Set up monitoring:** Check [Observability](./observability.md)
- **Configure production:** Review [Error Handling](./error-handling.md)

---

## Uninstall

```bash
pip uninstall tokenpak
```

(No configuration files are left behind.)
