# TokenPak Troubleshooting Guide

Find your problem fast. Every section follows **Problem → Cause → Fix** with copy-paste commands.

---

## Table of Contents

1. [Can't Connect](#1-cant-connect)
2. [401 Unauthorized](#2-401-unauthorized)
3. [Provider Errors (502)](#3-provider-errors-502)
4. [Rate Limit Errors (429)](#4-rate-limit-errors-429)
5. [Config Won't Load](#5-config-wont-load)
6. [Docker Container Exits Immediately](#6-docker-container-exits-immediately)
7. [pip install Fails](#7-pip-install-fails)
8. [High Latency](#8-high-latency)
9. [Cost Data Missing or Zero](#9-cost-data-missing-or-zero)
10. [Logs Not Showing / Wrong Level](#10-logs-not-showing--wrong-level)
11. [Cache Not Working](#11-cache-not-working)
12. [Compression Not Reducing Tokens](#12-compression-not-reducing-tokens)
13. [Getting More Help](#getting-more-help)

---

## 1. Can't Connect

### Problem

Client gets "Connection refused" or hangs when trying to reach the TokenPak proxy.

### Diagnose

```bash
# Is the proxy running?
ps aux | grep -E 'tokenpak|proxy' | grep -v grep

# Is anything listening on the expected port?
ss -ltnp | grep :8766

# Can you reach it locally?
curl -fsS http://127.0.0.1:8766/health
```

### Cause A: Proxy not running

**Fix:**
```bash
# Start the proxy
tokenpak serve

# Or start via Python module
python -m tokenpak proxy --port 8766
```

### Cause B: Wrong port

The default port is `8766`. If you changed it, make sure your client matches.

**Fix:**
```bash
# Check what port the proxy is configured to use
echo $TOKENPAK_PORT

# Start on a specific port
tokenpak serve --port 8766

# Or set via environment
export TOKENPAK_PORT=8766
tokenpak serve
```

### Cause C: Firewall blocking the port

**Fix:**
```bash
# Check if firewall is blocking (Linux)
sudo iptables -L -n | grep 8766

# Allow the port (ufw)
sudo ufw allow 8766/tcp

# Allow the port (firewalld)
sudo firewall-cmd --add-port=8766/tcp --permanent
sudo firewall-cmd --reload
```

### Cause D: Port already in use by another process

**Fix:**
```bash
# Find what's using the port
sudo lsof -iTCP:8766 -sTCP:LISTEN -n -P

# Kill the conflicting process (replace PID)
kill <PID>

# Or use a different port
export TOKENPAK_PORT=8767
tokenpak serve
```

See also: [TP-E101: Connection Error](errors.md#tp-e101-connection-error)

---

## 2. 401 Unauthorized

### Problem

Requests to providers through TokenPak fail with `401 Unauthorized` or `Authentication failed`.

### Diagnose

```bash
# Check if API keys are set
printenv | grep -iE 'ANTHROPIC_API_KEY|OPENAI_API_KEY'

# Test the key directly (Anthropic example)
curl -s -o /dev/null -w "%{http_code}" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-3-5","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  https://api.anthropic.com/v1/messages

# Should return 200. If 401, the key is bad.
```

### Cause A: API key not set or empty

**Fix:**
```bash
# Set the key (Anthropic)
export ANTHROPIC_API_KEY="sk-ant-..."

# Set the key (OpenAI)
export OPENAI_API_KEY="sk-..."

# Restart TokenPak after setting keys
tokenpak serve
```

### Cause B: API key is wrong or expired

**Fix:**
1. Go to the provider's console:
   - Anthropic: https://console.anthropic.com/settings/keys
   - OpenAI: https://platform.openai.com/api-keys
2. Generate a new API key
3. Update your environment variable
4. Restart TokenPak

### Cause C: Key not passed correctly through proxy

The proxy must forward the `Authorization` or `x-api-key` header to the upstream provider.

**Fix:**
```bash
# Verify the proxy is forwarding auth headers
curl -v -H "x-api-key: $ANTHROPIC_API_KEY" http://127.0.0.1:8766/v1/messages 2>&1 | grep -i "authorization\|x-api-key"
```

If headers are stripped, check your proxy config for header forwarding rules.

See also: [TP-E202: Invalid API Key](errors.md#tp-e202-invalid-api-key), [TP-E203: Missing API Key](errors.md#tp-e203-missing-api-key)

---

## 3. Provider Errors (502)

### Problem

TokenPak returns 502 Bad Gateway. The upstream provider (Anthropic, OpenAI, etc.) is unreachable or erroring.

### Diagnose

```bash
# Check proxy health (should be 200)
curl -fsS http://127.0.0.1:8766/health

# Test upstream provider directly (bypass TokenPak)
curl -s -o /dev/null -w "%{http_code}" https://api.anthropic.com/v1/messages
# Should return 401 (no key) or 200 (with key). If timeout/5xx, provider is down.

# Check provider status pages
# Anthropic: https://status.anthropic.com
# OpenAI: https://status.openai.com
```

### Cause A: Provider is having an outage

**Fix:**
1. Check the provider status page (links above)
2. Wait for the outage to resolve
3. If you have multiple providers configured, TokenPak will failover automatically

### Cause B: Your API key is invalid for the requested model

**Fix:**
```bash
# Test with a cheaper model first
curl -s -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-3-5","max_tokens":10,"messages":[{"role":"user","content":"test"}]}' \
  https://api.anthropic.com/v1/messages
```

If Haiku works but Opus doesn't, your account may not have access to that model tier.

### Cause C: Network issue between TokenPak and provider

**Fix:**
```bash
# Check DNS resolution
nslookup api.anthropic.com

# Check connectivity
curl -v --max-time 10 https://api.anthropic.com 2>&1 | head -20

# Check proxy/firewall rules for outbound HTTPS
```

See also: [TP-E501: Provider Error](errors.md#tp-e501-provider-error)

---

## 4. Rate Limit Errors (429)

### Problem

Getting `429 Too Many Requests` from the provider through TokenPak.

### Diagnose

```bash
# Check the Retry-After header in the error response
curl -v http://127.0.0.1:8766/v1/messages ... 2>&1 | grep -i "retry-after"

# Check TokenPak's configured rate limit
echo "Local rate limit: $TOKENPAK_RATE_LIMIT_RPM requests per minute"
# Default: 60 RPM
```

### Cause A: Exceeding provider rate limits

**Fix:**
1. Wait the duration specified in `Retry-After`
2. Reduce request frequency
3. Upgrade your provider plan for higher limits

### Cause B: TokenPak's own rate limiter is too strict

The default is 60 requests per minute. If you need more:

**Fix:**
```bash
# Increase the local rate limit
export TOKENPAK_RATE_LIMIT_RPM=120
tokenpak serve
```

### Cause C: Multiple clients sharing the same API key

**Fix:**
- Use separate API keys per client/agent if possible
- If sharing a key, coordinate rate limits across clients
- Configure TokenPak's rate limiter to stay under the shared quota

See also: [TP-E301: Rate Limit Exceeded](errors.md#tp-e301-rate-limit-exceeded)

---

## 5. Config Won't Load

### Problem

TokenPak exits on startup with config-related errors, or loads with unexpected defaults.

### Diagnose

```bash
# Check if a config file exists
ls -la ~/.tokenpak/config.json 2>/dev/null || echo "No config file found"

# Validate JSON syntax
python3 -m json.tool ~/.tokenpak/config.json

# Check environment variable overrides
printenv | grep TOKENPAK_
```

### Cause A: JSON syntax errors

**Fix:**
```bash
# Find the error
python3 -m json.tool ~/.tokenpak/config.json
# Python will report the line and column of the syntax error

# Common issues:
# - Trailing comma on last item in object/array
# - Missing quotes on keys
# - Single quotes instead of double quotes
```

### Cause B: Wrong data types

For example, `port` must be an integer, not a string.

**Fix:**
```json
{
  "port": 8766,
  "mode": "hybrid",
  "compression": {
    "enabled": true,
    "threshold_tokens": 4500
  }
}
```

Common type mistakes:
- `"port": "8766"` → should be `"port": 8766`
- `"enabled": "true"` → should be `"enabled": true`
- `"threshold_tokens": "4500"` → should be `"threshold_tokens": 4500`

### Cause C: Config file not found

TokenPak looks for config in this order:
1. Path specified via `--config` flag
2. `TOKENPAK_CONFIG` environment variable
3. `~/.tokenpak/config.json`
4. Environment variables (`TOKENPAK_PORT`, `TOKENPAK_MODE`, etc.)
5. Built-in defaults

**Fix:**
```bash
# Create a minimal config
mkdir -p ~/.tokenpak
cat > ~/.tokenpak/config.json << 'EOF'
{
  "port": 8766,
  "mode": "hybrid"
}
EOF

# Or just use env vars (no config file needed)
export TOKENPAK_PORT=8766
export TOKENPAK_MODE=hybrid
tokenpak serve
```

See also: [TP-E001: Config Error](errors.md#tp-e001-config-error), [TP-E004: Invalid Config File](errors.md#tp-e004-invalid-config-file)

---

## 6. Docker Container Exits Immediately

### Problem

`docker run tokenpak` starts then immediately stops. `docker ps` shows the container as exited.

### Diagnose

```bash
# Check exit code and logs
docker ps -a | grep tokenpak
docker logs <container_id>

# Check if config volume is mounted
docker inspect <container_id> | grep -A5 Mounts
```

### Cause A: Missing environment variables

**Fix:**
```bash
docker run -d \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e TOKENPAK_PORT=8766 \
  -p 8766:8766 \
  tokenpak:latest
```

### Cause B: Port conflict inside container

**Fix:**
```bash
# Ensure TOKENPAK_PORT matches the EXPOSE and -p mapping
docker run -d \
  -e TOKENPAK_PORT=8766 \
  -p 8766:8766 \
  tokenpak:latest

# If you change the port, update all three:
docker run -d \
  -e TOKENPAK_PORT=9000 \
  -p 9000:9000 \
  tokenpak:latest
```

### Cause C: Config path doesn't exist in container

**Fix:**
```bash
# Mount your config file
docker run -d \
  -v ~/.tokenpak/config.json:/app/config.json \
  -e TOKENPAK_CONFIG=/app/config.json \
  -p 8766:8766 \
  tokenpak:latest
```

### Cause D: Python version mismatch in image

TokenPak requires Python ≥ 3.10. The official Dockerfile uses 3.11.

**Fix:**
```bash
# Rebuild with correct base
docker build --no-cache -t tokenpak:latest .

# Verify Python version in container
docker run --rm tokenpak:latest python -V
```

---

## 7. pip install Fails

### Problem

`pip install tokenpak` fails with errors.

### Diagnose

```bash
# Check Python version
python3 --version
# Must be >= 3.10

# Check pip version
pip --version

# Try verbose install to see the full error
pip install tokenpak -v 2>&1 | tail -30
```

### Cause A: Python version too old

TokenPak requires Python ≥ 3.10.

**Fix:**
```bash
# Check version
python3 --version

# If < 3.10, install a newer Python
# Ubuntu/Debian:
sudo apt update && sudo apt install python3.11 python3.11-venv

# macOS (Homebrew):
brew install python@3.11

# Create a venv with the right version
python3.11 -m venv ~/.tokenpak-venv
source ~/.tokenpak-venv/bin/activate
pip install tokenpak
```

### Cause B: Dependency conflict

**Fix:**
```bash
# Use a fresh virtual environment (recommended)
python3 -m venv ~/.tokenpak-venv
source ~/.tokenpak-venv/bin/activate
pip install --upgrade pip
pip install tokenpak

# If a specific dependency conflicts:
pip install tokenpak --no-deps
pip install -r <(pip show tokenpak | grep Requires | sed 's/Requires: //' | tr ',' '\n')
```

### Cause C: Missing system dependencies

Some optional features need system libraries (e.g., Pillow for image compression).

**Fix:**
```bash
# Ubuntu/Debian
sudo apt install python3-dev libjpeg-dev zlib1g-dev

# macOS
brew install libjpeg zlib

# Then retry
pip install tokenpak
```

---

## 8. High Latency

### Problem

Requests through TokenPak are noticeably slower than going directly to the provider.

### Diagnose

```bash
# Measure TokenPak overhead vs direct provider
# Step 1: Time through TokenPak
time curl -s -o /dev/null \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-3-5","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8766/v1/messages

# Step 2: Time direct to provider
time curl -s -o /dev/null \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-3-5","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  https://api.anthropic.com/v1/messages

# The difference is TokenPak overhead. Should be < 50ms.
```

### Cause A: Provider latency (not TokenPak)

If both times are slow, the provider is slow. TokenPak can't fix upstream latency.

**Fix:**
- Check provider status page
- Try a different model (Haiku is faster than Opus)
- Wait for the provider to recover

### Cause B: Compression overhead on large prompts

Compression processing time scales with input size. For very large prompts (>50K tokens), this can add noticeable latency.

**Fix:**
```bash
# Disable compression if latency matters more than cost
export TOKENPAK_COMPACT=0
tokenpak serve

# Or increase the compression threshold (only compress large prompts)
export TOKENPAK_COMPACT_THRESHOLD_TOKENS=10000
tokenpak serve
```

### Cause C: Slow disk I/O (affects cache and telemetry)

**Fix:**
```bash
# Check disk performance
dd if=/dev/zero of=/tmp/testfile bs=1M count=100 oflag=direct 2>&1 | tail -1

# If slow, move the database to faster storage
export TOKENPAK_DB=/fast-ssd/.tokenpak/telemetry.db
tokenpak serve
```

---

## 9. Cost Data Missing or Zero

### Problem

The dashboard or `tokenpak cost` shows $0.00 or empty data even though requests are going through.

### Diagnose

```bash
# Check if telemetry DB has data
python3 -c "
from tokenpak.telemetry.storage import TelemetryDB
from pathlib import Path
db = TelemetryDB(str(Path.home() / '.tokenpak/telemetry.db'))
stats = db.stats()
print(stats)
db.close()
"

# Check if events are being recorded
python3 -c "
from tokenpak.telemetry.storage import TelemetryDB
from pathlib import Path
db = TelemetryDB(str(Path.home() / '.tokenpak/telemetry.db'))
traces = db.list_traces(limit=5)
for t in traces:
    print(f'  trace={t[\"trace_id\"][:12]}... cost={t.get(\"actual_cost\", 0):.4f}')
db.close()
"
```

### Cause A: Telemetry database doesn't exist

**Fix:**
```bash
# Check the DB path
ls -la ~/.tokenpak/telemetry.db

# If missing, it will be created automatically on next proxy start
tokenpak serve
```

### Cause B: Events recorded but no cost data

The cost table may be empty even when events exist. This happens if the pricing lookup fails (unsupported model or missing pricing data).

**Fix:**
```bash
# Backfill baseline costs from existing data
python3 -c "
from tokenpak.telemetry.storage import TelemetryDB
from pathlib import Path
db = TelemetryDB(str(Path.home() / '.tokenpak/telemetry.db'))
result = db.backfill_baseline_costs()
print(f'Eligible: {result[\"eligible\"]}, Updated: {result[\"updated\"]}, Skipped: {result[\"skipped\"]}')
db.close()
"
```

### Cause C: Provider doesn't report usage

Some providers or endpoints don't include token usage in their response.

**Fix:**
Check the `usage_source` field in the tp_usage table:
```bash
python3 -c "
import sqlite3
from pathlib import Path
conn = sqlite3.connect(str(Path.home() / '.tokenpak/telemetry.db'))
for row in conn.execute('SELECT usage_source, COUNT(*) FROM tp_usage GROUP BY usage_source').fetchall():
    print(f'  {row[0]}: {row[1]} records')
conn.close()
"
```

If most records show `unknown`, the provider responses aren't including usage data.

---

## 10. Logs Not Showing / Wrong Level

### Problem

TokenPak logs are missing, empty, or not showing enough detail to debug issues.

### Diagnose

```bash
# Check current log level
echo $TOKENPAK_LOG_LEVEL
# Empty means default (info)

# Check if logs are going to a file
ls -la ~/.tokenpak/*.log 2>/dev/null

# Check systemd journal (if running as service)
journalctl --user -u tokenpak -n 20 --no-pager
```

### Cause A: Log level too high (hiding useful messages)

**Fix:**
```bash
# Enable debug logging
export TOKENPAK_LOG_LEVEL=debug

# Enable compression debug output
export TOKENPAK_DEBUG_COMPRESSION=1

# Restart the proxy
tokenpak serve
```

Log levels (least → most verbose): `error` → `warning` → `info` → `debug`

### Cause B: Logs going to wrong destination

**Fix:**
```bash
# Run in foreground to see stdout logs
tokenpak serve

# If running via systemd, check journal
journalctl --user -u tokenpak -f

# If running via Docker
docker logs -f <container_name>
```

### Cause C: Log file permissions

**Fix:**
```bash
# Check log directory permissions
ls -la ~/.tokenpak/

# Fix permissions
chmod 755 ~/.tokenpak
chmod 644 ~/.tokenpak/*.log 2>/dev/null

# If running as a different user (Docker/systemd)
chown -R $(whoami) ~/.tokenpak
```

---

## 11. Cache Not Working

### Problem

Same prompts aren't hitting the cache. Cache hit rate is 0% or unexpectedly low.

### Diagnose

```bash
# Check cache stats
curl -fsS http://127.0.0.1:8766/stats | python3 -m json.tool

# Check cache size setting
echo "Cache size: ${TOKENPAK_COMPACT_CACHE_SIZE:-2000} entries"
```

### Cause A: Cache is disabled

**Fix:**
```bash
# Enable compression (which includes caching)
export TOKENPAK_COMPACT=1
tokenpak serve
```

### Cause B: Prompts have varying metadata

If each request includes timestamps, random IDs, or other changing data in the prompt, the cache key will differ every time.

**Fix:**
- Normalize prompts before sending (strip timestamps, request IDs)
- Move variable data out of the system prompt into user messages
- Use stable message ordering

### Cause C: Cache evicted too quickly (too small)

**Fix:**
```bash
# Increase cache size (default: 2000 entries)
export TOKENPAK_COMPACT_CACHE_SIZE=5000
tokenpak serve
```

See also: [TP-E401: Cache Error](errors.md#tp-e401-cache-error)

---

## 12. Compression Not Reducing Tokens

### Problem

Token counts show minimal or no reduction even though compression is enabled.

### Diagnose

```bash
# Check compression settings
printenv | grep TOKENPAK_COMPACT

# Run a compression demo to see it in action
tokenpak demo

# Check stats endpoint for compression ratios
curl -fsS http://127.0.0.1:8766/stats | python3 -m json.tool
```

### Cause A: Input below compression threshold

By default, prompts under 4,500 tokens are not compressed (overhead isn't worth it).

**Fix:**
```bash
# Lower the threshold if you want to compress smaller prompts
export TOKENPAK_COMPACT_THRESHOLD_TOKENS=1000
tokenpak serve
```

### Cause B: Compression mode is too conservative

**Fix:**
```bash
# Try a more aggressive mode
# Modes: strict (safest) → hybrid (default) → aggressive (maximum savings)
export TOKENPAK_MODE=aggressive
tokenpak serve
```

### Cause C: Content isn't compressible

Some prompts (short, unique, no repetition) don't compress well. This is expected.

**Fix:**
- TokenPak works best on prompts with repeated context, system prompts, or structured data
- Check `tokenpak demo --list` to see which compression recipes are available
- Use `tokenpak demo --file <path>` to see which recipes match your content

---

## Getting More Help

### 1. Search existing issues

Check if someone has already reported your problem:
https://github.com/kaywhy331/tokenpak/issues

### 2. File a bug report

Include the following in your report:

```markdown
**Environment:**
- TokenPak version: `tokenpak --version`
- Python version: `python3 --version`
- OS: `uname -a`
- Install method: pip / Docker / source

**Steps to reproduce:**
1. ...
2. ...

**Expected behavior:**
...

**Actual behavior:**
...

**Logs:**
<paste relevant log output>

**Config (redact API keys!):**
<paste sanitized config>
```

File at: https://github.com/kaywhy331/tokenpak/issues/new

### 3. Error codes reference

For detailed error code descriptions and fixes, see [Error Codes Reference](errors.md).

### 4. Community

- **GitHub Discussions:** https://github.com/kaywhy331/tokenpak/discussions
- **Documentation:** See [docs/INDEX.md](INDEX.md) for the full documentation index
