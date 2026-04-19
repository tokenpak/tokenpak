---
title: Troubleshooting
---

# Troubleshooting

This guide covers the most common issues encountered when installing, configuring, and running TokenPak.
For conceptual explanations and FAQ-style answers, see [FAQ.md](FAQ.md).
For error codes and HTTP status references, see [Error Handling](error-handling.md).

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

### Error: "Permission denied: /usr/local/bin/tokenpak" or `tokenpak: command not found`
**Symptom:** After `pip install`, `tokenpak` command fails with permission error or isn't found.

**Likely cause:** pip installed the binary into a path not in `$PATH`, or you're in a virtual environment where the CLI wasn't installed.

**Fix:**
```bash
# Find where pip put it
pip show tokenpak | grep Location
python3 -m site --user-bin

# Add user bin to PATH (add to ~/.bashrc for permanence)
export PATH="$HOME/.local/bin:$PATH"

# Verify
which tokenpak
tokenpak --version

# Or install to user directory
pip install --user tokenpak

# Or use a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # on Windows: venv\Scripts\activate
pip install tokenpak
```

If using a venv, activate it first or use the full path:
```bash
~/myenv/bin/tokenpak
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

### Error: "Missing API key" or Auth Errors
**Symptom:** `401 Unauthorized`, `Missing API key`, or provider returns auth error on every request.

**Likely cause:** `~/.openclaw/.env` is missing, has the wrong key name, or was not reloaded after update.

**Fix:**
```bash
# Verify the env file exists and has the key
grep "ANTHROPIC_API_KEY" ~/.openclaw/.env

# Check what the proxy actually loaded
curl -s http://localhost:8766/health | python3 -m json.tool

# If the file is missing, check the master source on your gateway machine
# and re-push secrets:
bash ~/vault/06_RUNTIME/scripts/push-secrets.sh

# Restart proxy to reload env
systemctl --user restart tokenpak
```

Alternatively, set the environment variable or add to proxy.yaml:
```bash
# Set the environment variable
export ANTHROPIC_API_KEY="sk-ant-..."

# Or use .env file
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
tokenpak start --env-file .env

# Check what TokenPak sees
tokenpak doctor
```

> See [FAQ.md — Security & Privacy](FAQ.md) for how API keys are managed across agents.

### Error: "Unknown provider" or "Invalid model name"
**Symptom:** Config references a provider that isn't registered, or model returns "not found."

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

## Proxy Won't Start / Port Issues

### Error: "Port 8766 is already in use" / `Address already in use`
**Symptom:** `OSError: [Errno 98]` or "Address already in use" when starting the proxy.

**Cause:** Another process (previous proxy instance, orphaned worker, or unrelated service) is bound to port 8766.

**Fix:**
```bash
# Find what's holding the port
lsof -i :8766

# Kill the specific PID (replace 12345 with actual PID)
kill -9 12345

# Or kill all tokenpak processes
pkill -f "proxy.py"

# Restart
systemctl --user restart tokenpak

# Or use a different port:
tokenpak start --port 9000

# Or in proxy.yaml:
server:
  port: 9000
```

If this happens repeatedly after reboots, check `systemctl --user status tokenpak` for crash loops. See **SIGTERM Restart Loops** below.

### Error: "Could not read configuration file"
**Symptom:** `FileNotFoundError` or "proxy.yaml not found."

**Likely cause:** TokenPak can't find `proxy.yaml` (wrong working directory).

**Fix:**
```bash
# TokenPak looks for proxy.yaml in the current directory
# Make sure you're in the right folder
cd ~/tokenpak
tokenpak start

# Or specify the path explicitly
tokenpak start --config /path/to/proxy.yaml
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
```

### Error: "Rate limit exceeded"
**Symptom:** Requests fail with `429 Too Many Requests`.

**Likely cause:** Hitting the provider's rate limits (requests per minute, tokens per day).

**Fix:**
```yaml
# Configure rate limits in proxy.yaml:
rate_limiting:
  anthropic:
    rpm: 50  # Requests per minute
  openai:
    rpm: 60
```

### Error: "Fallback provider also failed"
**Symptom:** Request fails even though both primary and fallback providers are configured.

**Fix:**
```bash
# Check provider status
tokenpak provider-status

# Check API keys for all providers
tokenpak doctor

# To force a provider back into rotation
tokenpak provider-force-health anthropic healthy

# Add more fallbacks in proxy.yaml:
# routing:
#   primary: anthropic
#   fallback: [openai, gemini]
```

---

## Performance & Latency

### High Latency (Proxy Overhead vs. Upstream)
**Symptom:** Requests through TokenPak are noticeably slower than calling the provider directly.

**Cause:** Usually one of: vault index loading on first request, cache cold start, StageTrace overhead, or network routing (loopback + provider).

**Fix:**
```bash
# Check health — look for circuit_breaker state and p99 latency
curl -s http://localhost:8766/health | python3 -m json.tool

# Check /stats for cache hit rate and timing breakdown
curl -s http://localhost:8766/stats | python3 -m json.tool
```

- **First-request lag:** Vault index loads on first use — subsequent requests are faster.
- **Consistently slow:** Check if vault index is large; rebuild it:
  ```bash
  bash ~/vault/06_RUNTIME/scripts/rebuild-vault-index.sh
  ```
- **All requests slow:** Check upstream provider status (Anthropic/OpenAI status pages).

Expected proxy overhead: **2–15ms** per request on local hardware.

### Cache Hit Rate Unexpectedly Low
**Symptom:** `/stats` shows `cache_hit_rate: 0` or very low percentage despite repeated queries.

**Cause:** Caching disabled, requests have non-deterministic fields, TTL too short, or cache cleared by restart.

**Fix:**
```bash
# Check cache config
grep -i cache ~/tokenpak/proxy.yaml

# Check current cache stats
curl -s http://localhost:8766/stats | python3 -m json.tool | grep -i cache
```

For caching to work, requests must be **byte-identical** (same model, same prompt, same parameters).

```yaml
# Configure cache limits in proxy.yaml:
cache:
  max_size_mb: 256  # Max cache size
  ttl_seconds: 3600  # Entries expire after 1 hour
  eviction_policy: "lru"  # Least recently used
```

> See [FAQ.md — How does caching work?](FAQ.md) for TTL and per-request override details.

### /stats Endpoint Slow (>100ms)
**Symptom:** `curl http://localhost:8766/stats` takes >100ms to respond.

**Fix:**
```bash
# Measure baseline
time curl -s http://localhost:8766/stats > /dev/null

# Check request history size in stats output
curl -s http://localhost:8766/stats | python3 -m json.tool | grep -i "total_requests\|history"
```

If request count is very high (>10k), restart the proxy to clear in-memory history:
```bash
systemctl --user restart tokenpak
```

Reduce history buffer size in `proxy.yaml`:
```yaml
stats:
  history_limit: 1000   # Keep only last 1000 requests in memory
```

Expected `/stats` response time: **<20ms** on local hardware.

---

## Vault & Index

### Vault Index Stale / Vault Context Not Loading
**Symptom:** Requests return responses with no vault awareness. `/stats` shows `vault_hits: 0`.

**Fix:**
```bash
# Force-rebuild the vault index
bash ~/vault/06_RUNTIME/scripts/rebuild-vault-index.sh

# Verbose output to verify paths
bash ~/vault/06_RUNTIME/scripts/rebuild-vault-index.sh --verbose

# Check the index file exists and is recent
ls -lh ~/vault/.tokenpak/index.json
```

> ⚠️ Do NOT use `python3 -m tokenpak index ~/vault` — this routes differently and may write to the wrong path.

Index auto-reloads every 5 minutes (`VAULT_INDEX_RELOAD_INTERVAL=300s`). For immediate reload:
```bash
systemctl --user restart tokenpak
```

### Error: "Vault index not loading" (startup failure)
**Symptom:** TokenPak fails to start with "Vault index missing" or "Index format invalid."

**Fix:**
```bash
# Rebuild the vault index
tokenpak rebuild-vault-index

# Or clear and let it regenerate
rm -f ~/.tokenpak/vault-index.json
tokenpak start  # Will regenerate on startup

# Or using vault scripts
bash ~/vault/06_RUNTIME/scripts/rebuild-vault-index.sh
```

---

## Systemd & Service Issues

### SIGTERM Restart Loops
**Symptom:** `systemctl --user status tokenpak` shows `(Result: signal)` and restarts repeatedly. Logs show `SIGTERM` or `Killed`.

**Cause:** Usually out-of-memory (OOM) kill from the kernel, or a misconfigured `ExecStop` command that sends SIGTERM before the process initializes.

**Fix:**
```bash
# Check if OOM killer hit it
journalctl --user -u tokenpak --no-pager -n 100 | grep -i "killed\|oom\|memory"

# Check system memory
free -h

# Check service status detail
systemctl --user status tokenpak -l
```

If OOM-killed:
- Reduce vault index size (archive old files)
- Add `MemoryLimit=512M` to the service unit to get cleaner kills instead of OOM

If it's a startup race (SIGTERM before ready):
```ini
# Add to tokenpak.service [Service] section
TimeoutStartSec=30
Restart=on-failure
RestartSec=5s
```

Reload and restart:
```bash
systemctl --user daemon-reload
systemctl --user restart tokenpak
```

### Error: "Memory usage growing unbounded"
**Symptom:** TokenPak's memory usage keeps increasing (cache leak).

**Fix:**
```yaml
# Configure cache limits in proxy.yaml:
cache:
  max_size_mb: 256  # Max cache size
  ttl_seconds: 3600  # Entries expire after 1 hour
  eviction_policy: "lru"  # Least recently used
```

```bash
# Check cache stats:
tokenpak cache-stats
```

If memory still grows, file a bug with logs:
```bash
tokenpak start --debug > tokenpak.log 2>&1
# Run for a while, then attach the log to an issue
```

---

## Stage Processing

### StageTrace or Compression Stage Silently Skipped
**Symptom:** Expected compression or stage processing isn't happening. Requests pass through unmodified. No error in logs.

**Cause:** Stage is disabled in config, stage threshold wasn't met (e.g., prompt too short to compress), or a stage dependency failed silently.

**Fix:**
```bash
# Check proxy logs for stage processing output
journalctl --user -u tokenpak --no-pager -n 50 | grep -i stage

# Enable debug logging temporarily
TOKENPAK_LOG_LEVEL=DEBUG systemctl --user restart tokenpak
journalctl --user -u tokenpak -f
```

Check `proxy.yaml` to confirm stages are enabled:
```yaml
stages:
  compression:
    enabled: true
    min_tokens: 500   # Only applies if prompt >= 500 tokens
```

If `min_tokens` is set high, short prompts are intentionally skipped — this is correct behavior, not a bug.

---

## Multi-Agent / Fleet

### Agent Proxy Drift — Running Old Version
**Symptom:** `check-proxy-drift.sh` reports a diff between vault canonical and a deployed agent's proxy.

**Cause:** A sync was run when the agent was offline, or someone edited `~/tokenpak/proxy.py` directly on an agent machine instead of going through the vault workflow.

**Fix:**
```bash
# Check current drift across all agents
bash ~/vault/06_RUNTIME/scripts/check-proxy-drift.sh

# Deploy canonical to all agents (idempotent — already-synced agents are no-ops)
bash ~/vault/06_RUNTIME/scripts/sync-tokenpak-proxy.sh --restart

# Confirm all agents healthy
for agent in sue trix cali; do
  echo "=== $agent ==="
  ssh ${agent}bot "curl -s http://localhost:8766/health" | python3 -m json.tool
done
```

> ⚠️ **Never edit `~/tokenpak/proxy.py` directly on agent machines.** Always edit `~/tokenpak-dev/proxy.py` on staging, commit to vault, then deploy via `sync-tokenpak-proxy.sh`.

---

## Cost & Observability

### Cost Calculation Mismatch
**Symptom:** TokenPak reports a different cost than the provider's billing.

**Fix:**
```yaml
# Configure custom pricing in proxy.yaml:
providers:
  anthropic:
    pricing:
      input_cost_per_1m_tokens: 3.00  # cents
      output_cost_per_1m_tokens: 15.00
```

### No Logs or Metrics Visible
**Symptom:** No request logs in stdout, and `/metrics` endpoint is empty.

**Fix:**
```yaml
# Enable logging in proxy.yaml:
logging:
  level: "info"  # or "debug"
  format: "json"  # for structured logs
```

```bash
# Verify metrics are being collected:
curl http://localhost:8766/metrics

# If still empty, make a test request first:
curl -X POST http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{"model": "claude-3", "messages": [{"role": "user", "content": "test"}]}'
```

---

## Still Stuck?

1. Check [FAQ.md](FAQ.md) for conceptual explanations
2. Check [Error Handling](error-handling.md) for specific error codes
3. **Gather diagnostics:** `tokenpak doctor > diagnostics.txt`
4. **Enable debug logging:**
   ```yaml
   logging:
     level: "debug"
   ```
5. Run `journalctl --user -u tokenpak --no-pager -n 100` for recent logs
6. Open an issue on GitHub: <https://github.com/tokenpak/tokenpak/issues> — include `tokenpak doctor` output, relevant logs (with API keys redacted), your OS, Python version, and steps to reproduce
