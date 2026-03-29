# TokenPak Troubleshooting Guide

A quick reference for the most common issues and their fixes. If something isn't here, check [GitHub Issues](https://github.com/kaywhy331/tokenpak/issues) or open a new one.

---

## Installation

### Error: "No module named 'tokenpak'"
**Symptom:** `python -c "import tokenpak"` fails or your code can't find TokenPak.

**Likely cause:** TokenPak isn't installed, or you're using a different Python version.

**Fix:**
```bash
# Check which Python you're using
which python3

# Install for that version
python3 -m pip install --upgrade tokenpak

# Verify
python3 -c "import tokenpak; print(tokenpak.__version__)"
```

### Error: "ModuleNotFoundError: No module named 'fastapi'"
**Symptom:** TokenPak fails to start with a dependency error.

**Likely cause:** Optional dependencies weren't installed. You may have skipped the full installation.

**Fix:**
```bash
# Reinstall with all deps
pip install tokenpak[all]

# Or install specific extras
pip install tokenpak[docs]  # for documentation
pip install tokenpak[dev]   # for development
```

### Error: "Python 3.9+ required"
**Symptom:** Installation fails with "This project requires Python 3.9 or later."

**Likely cause:** You're running Python 3.8 or older.

**Fix:**
```bash
# Check your Python version
python3 --version

# Install Python 3.9 or later (depends on your OS)
# macOS:
brew install python@3.11

# Ubuntu/Debian:
sudo apt-get install python3.11

# Windows: Download from python.org or use Windows Store

# Then reinstall
python3.11 -m pip install tokenpak
```

### Error: "Permission denied: /usr/local/bin/tokenpak"
**Symptom:** After `pip install`, `tokenpak` command fails with permission error.

**Likely cause:** pip installed to a system directory without write permissions.

**Fix:**
```bash
# Install to user directory
pip install --user tokenpak

# Or use a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # on Windows: venv\Scripts\activate
pip install tokenpak
```

---

## Configuration

### Error: "Invalid YAML in proxy.yaml"
**Symptom:** TokenPak fails to start with "YAML parsing error" or "Invalid configuration."

**Likely cause:** Syntax error in `proxy.yaml` (bad indentation, missing quotes, etc.).

**Fix:**
```bash
# Validate the YAML syntax
python3 -c "import yaml; yaml.safe_load(open('proxy.yaml'))"

# If that works, check TokenPak parsing
tokenpak validate-config proxy.yaml

# Common mistakes:
# - Tabs instead of spaces (use spaces only)
# - Missing colons after keys
# - Mismatched quotes

# Example (correct):
routing:
  primary: "anthropic"
  fallback: "openai"
```

### Error: "Missing API key for provider: anthropic"
**Symptom:** Requests fail with "No API key found" or "Invalid credentials."

**Likely cause:** Environment variable isn't set, or TokenPak isn't reading it.

**Fix:**
```bash
# Set the environment variable
export ANTHROPIC_API_KEY="sk-ant-..."

# Or add to proxy.yaml
providers:
  anthropic:
    api_key: "sk-ant-..."  # Not recommended (secrets in code)

# Better: use .env file
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
tokenpak serve --env-file .env

# Check what TokenPak sees
tokenpak doctor
```

### Error: "Unknown provider: 'custom-llm'"
**Symptom:** Config references a provider that isn't registered.

**Likely cause:** Typo in provider name, or the provider module isn't loaded.

**Fix:**
```yaml
# Check your provider name matches exactly
routing:
  primary: "anthropic"  # Not "anthropic_api" or "ANTHROPIC"

# If using a custom provider, ensure it's imported:
custom_providers:
  - module: "mylib.custom_llm"  # Must be importable
    name: "custom-llm"
```

### Error: "Invalid model name: 'gpt-4-turbo-preview'"
**Symptom:** Requests fail with "Model not found" or "Invalid model."

**Likely cause:** The model isn't available for that provider, or TokenPak doesn't recognize it.

**Fix:**
```bash
# Check what models the provider supports
tokenpak list-models anthropic
tokenpak list-models openai

# Use a valid model name:
# Anthropic: claude-3-opus, claude-3-sonnet, claude-3-haiku
# OpenAI: gpt-4, gpt-3.5-turbo, gpt-4-turbo

# If using a custom model, add it to proxy.yaml:
providers:
  openai:
    model_aliases:
      "gpt-4-latest": "gpt-4-turbo-preview"
```

---

## Startup & Runtime

### Error: "Port 8766 is already in use"
**Symptom:** TokenPak fails to start with "Address already in use" or "Port is occupied."

**Likely cause:** Another service (or another TokenPak instance) is using port 8766.

**Fix:**
```bash
# Find what's using the port
lsof -i :8766  # macOS/Linux
netstat -ano | findstr :8766  # Windows

# Either:
# 1. Stop the other service
# 2. Kill the process: kill -9 <PID>
# 3. Or use a different port:
tokenpak serve --port 9000

# Or in proxy.yaml:
server:
  port: 9000
```

### Error: "Could not read configuration file"
**Symptom:** `FileNotFoundError` or "proxy.yaml not found."

**Likely cause:** TokenPak can't find `proxy.yaml` (wrong working directory).

**Fix:**
```bash
# TokenPak looks for proxy.yaml in the current directory
# Make sure you're in the right folder
cd ~/tokenpak
tokenpak serve

# Or specify the path explicitly
tokenpak serve --config /path/to/proxy.yaml
```

### Error: "Connection refused: localhost:8766"
**Symptom:** Your app can't connect to TokenPak, even though it's running.

**Likely cause:** TokenPak is listening on `127.0.0.1` (localhost), but you're connecting from a different machine or Docker container.

**Fix:**
```yaml
# In proxy.yaml, bind to all interfaces:
server:
  host: "0.0.0.0"  # Listen on all interfaces
  port: 8766

# Or from Docker, map the port correctly:
docker run -p 8766:8766 tokenpak/tokenpak
```

---

## Requests & Routing

### Error: "Request timeout: no response from provider"
**Symptom:** Requests take >30s and fail with timeout.

**Likely cause:** Provider is slow, rate-limited, or unreachable.

**Fix:**
```yaml
# Increase timeout in proxy.yaml
providers:
  anthropic:
    timeout_seconds: 60  # Default is 30

# Or check provider status
tokenpak provider-status anthropic

# If the provider is actually down:
# TokenPak will automatically try fallback providers
```

### Error: "Invalid API key for provider: openai"
**Symptom:** All requests fail with "Authentication failed" or "Invalid API key."

**Likely cause:** API key is wrong, expired, or doesn't have the right permissions.

**Fix:**
```bash
# Verify the key is correct (check your provider dashboard)
# Keys are often long strings starting with specific prefixes:
# - Anthropic: sk-ant-...
# - OpenAI: sk-...

# Test the key directly
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# If that fails, the key is invalid
# If it works, TokenPak should work too

# Check that the key has the right permissions:
# - OpenAI keys need "API access" enabled
# - Anthropic keys need to be for the right project
```

### Error: "Model not supported by provider"
**Symptom:** You request GPT-4, but the provider only has GPT-3.5.

**Likely cause:** The model isn't available in the provider, or it's not in your API tier.

**Fix:**
```bash
# Check available models:
tokenpak list-models openai

# If the model exists but isn't available to you:
# - Check your API tier (free vs paid)
# - Check your quota (some models are limited)
# - For Claude, you may need to request early access

# Use a model you have access to:
# - OpenAI: gpt-3.5-turbo (always available)
# - Claude: claude-3-haiku (free tier)
```

### Error: "Rate limit exceeded"
**Symptom:** Requests fail with `429 Too Many Requests` or "Rate limit exceeded."

**Likely cause:** You're hitting the provider's rate limits (requests per minute, tokens per day, etc.).

**Fix:**
```yaml
# Configure rate limits in proxy.yaml:
rate_limiting:
  anthropic:
    rpm: 50  # Requests per minute
  openai:
    rpm: 60

# TokenPak will queue requests and respect these limits

# Or check your provider's actual limits:
# - Claude: varies by tier (check https://console.anthropic.com)
# - OpenAI: varies by tier (check https://platform.openai.com/account/rate-limits)

# If you hit a hard limit, you'll need to upgrade your tier
```

### Error: "Fallback provider also failed"
**Symptom:** Request fails even though both primary and fallback providers are configured.

**Likely cause:** Both providers are either down, rate-limited, or having auth issues.

**Fix:**
```bash
# Check provider status
tokenpak provider-status

# Check API keys for all providers
tokenpak doctor

# If a provider is temporarily down, TokenPak will mark it as unhealthy
# and only use it again after a recovery check (default 30s)

# To force a provider back into rotation
tokenpak provider-force-health anthropic healthy

# Add more fallbacks in proxy.yaml:
routing:
  primary: anthropic
  fallback: [openai, gemini]
```

---

## Cost & Observability

### Error: "Cost calculation mismatch with provider"
**Symptom:** TokenPak reports a different cost than the provider's billing.

**Likely cause:** TokenPak uses list pricing; you may have negotiated rates.

**Fix:**
```yaml
# Configure custom pricing in proxy.yaml:
providers:
  anthropic:
    pricing:
      input_cost_per_1m_tokens: 3.00  # cents
      output_cost_per_1m_tokens: 15.00

# Check TokenPak's calculated costs:
tokenpak cost-breakdown

# If you have enterprise pricing, contact us for custom rates
```

### Error: "Missing cost data for new model"
**Symptom:** TokenPak doesn't have pricing for a new model you're using.

**Likely cause:** The model was released recently, or it's in beta.

**Fix:**
```bash
# Check what models have pricing
tokenpak list-pricing

# If a model is missing, add it manually:
# Edit proxy.yaml with the pricing from the provider's docs

# Or request it be added to TokenPak
# (Create a GitHub issue with the model name and pricing)
```

### Error: "No logs or metrics visible"
**Symptom:** No request logs in stdout, and `/metrics` endpoint is empty.

**Likely cause:** Logging might be disabled, or you're not hitting any requests.

**Fix:**
```yaml
# Enable logging in proxy.yaml:
logging:
  level: "info"  # or "debug"
  format: "json"  # for structured logs

# Verify metrics are being collected:
curl http://localhost:8766/metrics

# If still empty, make a test request first:
curl -X POST http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{"model": "claude-3", "messages": [{"role": "user", "content": "test"}]}'
```

---

## Advanced

### Error: "Vault index not loading"
**Symptom:** TokenPak fails to start with "Vault index missing" or "Index format invalid."

**Likely cause:** Vault index is corrupted or out of sync.

**Fix:**
```bash
# Rebuild the vault index
tokenpak rebuild-vault-index

# Or clear and let it regenerate
rm -f ~/.tokenpak/vault-index.json
tokenpak serve  # Will regenerate on startup
```

### Error: "Memory usage growing unbounded"
**Symptom:** TokenPak's memory usage keeps increasing (cache leak).

**Likely cause:** Cache isn't evicting old entries, or there's a memory leak.

**Fix:**
```yaml
# Configure cache limits in proxy.yaml:
cache:
  max_size_mb: 256  # Max cache size
  ttl_seconds: 3600  # Entries expire after 1 hour
  eviction_policy: "lru"  # Least recently used

# Check cache stats:
tokenpak cache-stats

# If memory still grows, file a bug with logs:
tokenpak serve --debug > tokenpak.log 2>&1
# Run for a while, then attach the log to an issue
```

### Getting Help

If nothing above fixes your issue:

1. **Gather diagnostics:**
   ```bash
   tokenpak doctor > diagnostics.txt
   ```

2. **Enable debug logging:**
   ```yaml
   logging:
     level: "debug"
   ```

3. **Open an issue on GitHub:**
   - Include the output of `tokenpak doctor`
   - Include relevant logs (with API keys redacted)
   - Include your OS, Python version, and TokenPak version
   - Describe the exact steps to reproduce

4. **Ask in Discussions:**
   - If you're unsure whether it's a bug, start a Discussion first

We'll help you get it running!
