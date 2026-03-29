# TokenPak Error Codes Reference

This document lists all HTTP error codes returned by TokenPak proxy, their meanings, and resolution steps.

## HTTP Error Code Categories

### 2xx Success
- **200 OK** — Request succeeded
- **204 No Content** — Request succeeded but has no response body

### 4xx Client Errors

| Code | Name | Cause | Fix |
|------|------|-------|-----|
| **400** | Bad Request | Invalid request format, missing required field, wrong content-type | Validate JSON structure, check Content-Type header is `application/json` |
| **401** | Unauthorized | Missing, invalid, or expired API key | Add `Authorization: Bearer <key>` header |
| **403** | Forbidden | API key lacks permission for this operation | Check key has required scopes/permissions |
| **404** | Not Found | Endpoint doesn't exist | Verify URL is correct (e.g., `/health`, `/stats`, not `/api/chat`) |
| **409** | Conflict | Request conflicts with current state (e.g., duplicate cache entry) | Retry with different payload or clear cache |
| **429** | Too Many Requests | Rate limit exceeded (TokenPak or provider) | Wait `Retry-After` seconds before retrying |
| **431** | Request Header Fields Too Large | Headers exceed size limit | Reduce header count or size |

### 5xx Server Errors

| Code | Name | Cause | Fix |
|------|------|-------|-----|
| **500** | Internal Server Error | Unhandled exception in TokenPak | Check logs: `tail ~/.tokenpak/logs/tokenpak.log` |
| **502** | Bad Gateway | Upstream provider error or unreachable | Check provider API key, network connectivity, provider status |
| **503** | Service Unavailable | TokenPak is overloaded or shutting down | Wait and retry, or restart TokenPak |
| **504** | Gateway Timeout | Request to upstream took too long | Increase timeout config, check network latency to provider |

---

## Error Response Format

All error responses follow this format:

```json
{
  "error": {
    "type": "error_type",
    "message": "Human-readable description",
    "code": "ERROR_CODE"
  }
}
```

**Example:**
```json
{
  "error": {
    "type": "unauthorized",
    "message": "Invalid or missing authentication",
    "code": "AUTH_001"
  }
}
```

---

## Common Error Types and Codes

### Authentication Errors (4xx)

#### `AUTH_001` — Missing API Key
**HTTP:** 401 Unauthorized  
**Message:** `Invalid or missing authentication`  
**Cause:** No `Authorization` header in request

**Fix:**
```bash
curl -H "Authorization: Bearer YOUR_KEY" http://localhost:8766/health
```

#### `AUTH_002` — Invalid API Key Format
**HTTP:** 401 Unauthorized  
**Message:** `Invalid authentication format. Expected: Bearer <token>`  
**Cause:** Authorization header is malformed or missing "Bearer " prefix

**Fix:**
```bash
# ✅ Correct
Authorization: Bearer sk-ant-v3-...

# ❌ Wrong
Authorization: sk-ant-v3-...
Authorization: Basic xyz...
X-API-Key: sk-ant-v3-...
```

#### `AUTH_003` — Expired or Revoked Key
**HTTP:** 401 Unauthorized  
**Message:** `Authentication failed. Key may be expired or revoked`  
**Cause:** The API key was deleted, rotated, or expired

**Fix:**
```bash
# Generate a new key
tokenpak auth --generate

# Verify the new key in config
grep "keys:" ~/.tokenpak/config.yaml

# Restart proxy
tokenpak serve
```

---

### Validation Errors (4xx)

#### `VALIDATION_001` — Invalid JSON
**HTTP:** 400 Bad Request  
**Message:** `Invalid JSON payload. Check syntax.`  
**Cause:** Request body is not valid JSON (trailing commas, unquoted keys, etc.)

**Fix:**
```bash
# ❌ Invalid
{"model": "claude-3-sonnet",}

# ✅ Valid
{"model": "claude-3-sonnet"}
```

#### `VALIDATION_002` — Missing Required Field
**HTTP:** 400 Bad Request  
**Message:** `Missing required field: model`  
**Cause:** Request is missing a required parameter

**Fix:**
```bash
# Must include all required fields:
curl -X POST http://localhost:8766/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{
    "model": "claude-3-sonnet",
    "messages": [{"role": "user", "content": "hi"}]
  }'
```

#### `VALIDATION_003` — Invalid Field Type
**HTTP:** 400 Bad Request  
**Message:** `Field 'max_tokens' must be a number, got string`  
**Cause:** Field has wrong type (e.g., string instead of integer)

**Fix:**
```bash
# ❌ Wrong
{"max_tokens": "1000"}

# ✅ Correct
{"max_tokens": 1000}
```

---

### Provider Errors (5xx)

#### `PROVIDER_001` — Invalid API Key (Provider Side)
**HTTP:** 502 Bad Gateway  
**Message:** `Provider rejected request: Invalid API key`  
**Cause:** The API key in your TokenPak config is wrong or expired

**Fix:**
```bash
# 1. Verify the key works directly
curl -X POST https://api.anthropic.com/v1/messages \
  -H "x-api-key: YOUR_KEY" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-3-haiku",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "hi"}]
  }'

# 2. If that fails, regenerate the key in Anthropic console

# 3. Update TokenPak config
grep "anthropic_key:" ~/.tokenpak/config.yaml
# Edit ~/.tokenpak/config.yaml and update the key

# 4. Restart
tokenpak serve
```

#### `PROVIDER_002` — Rate Limited (Provider Side)
**HTTP:** 502 Bad Gateway  
**Message:** `Provider rate limit exceeded`  
**Cause:** The provider's rate limit was hit (not TokenPak's)

**Fix:**
```bash
# 1. Check the error details
tail -20 ~/.tokenpak/logs/tokenpak.log | grep "rate_limit\|429"

# 2. Wait before retrying (check Retry-After header)
curl -i http://localhost:8766/v1/messages ...
# Look for: Retry-After: 60

# 3. Increase TokenPak queue and adjust batch settings
grep -A 5 "provider_rate_limit:" ~/.tokenpak/config.yaml

# 4. Switch to a different provider key (if you have one)
```

#### `PROVIDER_003` — Provider Unreachable
**HTTP:** 502 Bad Gateway  
**Message:** `Unable to reach provider: Connection timeout`  
**Cause:** Network path to provider API is blocked or provider is down

**Fix:**
```bash
# 1. Test connectivity to provider
ping api.anthropic.com
ping api.openai.com

# 2. Test with curl directly
curl -v https://api.anthropic.com/v1/health

# 3. Check if provider is down
# Visit: https://status.anthropic.com or https://status.openai.com

# 4. Check firewall rules
sudo ufw status
sudo iptables -L -n | grep 443

# 5. If behind proxy, configure TokenPak to use it:
export HTTPS_PROXY=socks5://localhost:1080
tokenpak serve
```

#### `PROVIDER_004` — Provider Returned 5xx Error
**HTTP:** 502 Bad Gateway  
**Message:** `Provider error: 500 Internal Server Error`  
**Cause:** Provider's API is experiencing an outage or internal error

**Fix:**
```bash
# 1. This is not your fault. The provider is having issues.

# 2. Check provider status page
curl https://status.anthropic.com/api/v2/status.json | jq .

# 3. Retry after 1-5 minutes

# 4. Use fallback provider in config:
grep -A 10 "fallback:" ~/.tokenpak/config.yaml

# 5. Enable circuit breaker to auto-fallback
grep "circuit_breaker:" ~/.tokenpak/config.yaml
# Should show: enabled: true
```

---

### Rate Limiting Errors (429)

#### `RATELIMIT_001` — TokenPak Rate Limit Hit
**HTTP:** 429 Too Many Requests  
**Message:** `Rate limit exceeded. Retry-After: 30`  
**Cause:** Your request rate to TokenPak exceeds configured limit

**Fix:**
```bash
# 1. Check current limits
grep -A 5 "rate_limit:" ~/.tokenpak/config.yaml

# 2. Increase limits (if needed)
# Edit ~/.tokenpak/config.yaml:
rate_limit:
  requests_per_second: 200  # Was 100
  burst_size: 1000          # Was 500

# 3. Restart
tokenpak serve

# 4. Or implement exponential backoff in your client:
for attempt in {1..5}; do
  response=$(curl -s http://localhost:8766/v1/messages ...)
  if [[ $? -ne 0 ]]; then
    sleep $((2 ** attempt))
    continue
  fi
  break
done
```

#### `RATELIMIT_002` — Provider Rate Limit (Passed Through)
**HTTP:** 429 Too Many Requests  
**Message:** `Provider rate limit exceeded. Retry-After: 60`  
**Cause:** Provider's rate limit was hit

**Fix:** Same as `PROVIDER_002` above.

---

### Configuration Errors (5xx)

#### `CONFIG_001` — Invalid Config Format
**HTTP:** 500 Internal Server Error  
**Message:** `Failed to load config: YAML syntax error at line 5`  
**Cause:** Config file has syntax errors

**Fix:**
```bash
# 1. Validate config syntax
python3 -m yaml ~/.tokenpak/config.yaml

# 2. Check for common YAML errors:
# - Tabs instead of spaces (use: cat -A ~/.tokenpak/config.yaml)
# - Wrong indentation (must be 2 spaces)
# - Unquoted colons in strings

# 3. Use a YAML validator
echo "port: 8766" | python3 -c "import sys, yaml; yaml.safe_load(sys.stdin)"

# 4. Restart after fixing
tokenpak serve
```

#### `CONFIG_002` — Missing Required Config Field
**HTTP:** 500 Internal Server Error  
**Message:** `Config validation failed: Missing required field 'port'`  
**Cause:** A mandatory config field is not set

**Fix:**
```bash
# 1. Check required fields
grep -E "^(port|listen_addr|providers):" ~/.tokenpak/config.yaml

# 2. Add missing field
# Edit ~/.tokenpak/config.yaml and add:
port: 8766

# 3. Validate and restart
tokenpak config validate
tokenpak serve
```

---

### Resource Errors (5xx)

#### `RESOURCE_001` — Insufficient Memory
**HTTP:** 500 Internal Server Error  
**Message:** `Cannot allocate memory`  
**Cause:** TokenPak ran out of RAM (OOM kill)

**Fix:**
```bash
# 1. Check available memory
free -h

# 2. Stop TokenPak
pkill -f tokenpak

# 3. Reduce cache size in config
grep "cache_max_size" ~/.tokenpak/config.yaml
# Lower this value, e.g., from 1GB to 500MB

# 4. Restart
tokenpak serve

# 5. Monitor memory usage
while true; do ps aux | grep tokenpak | grep -v grep | awk '{print $6}'; sleep 5; done
```

#### `RESOURCE_002` — Disk Full
**HTTP:** 500 Internal Server Error  
**Message:** `No space left on device`  
**Cause:** Disk is full, cache can't be written

**Fix:**
```bash
# 1. Check disk usage
df -h

# 2. Clear cache and logs
rm ~/.tokenpak/cache/*
rm ~/.tokenpak/logs/*.gz

# 3. Or clean old logs
find ~/.tokenpak/logs -name "*.log.*" -mtime +7 -delete

# 4. Restart
tokenpak serve

# 5. Monitor disk
watch -n 5 'df -h'
```

---

### Timeout Errors (504)

#### `TIMEOUT_001` — Provider Response Timeout
**HTTP:** 504 Gateway Timeout  
**Message:** `Request to upstream took too long (>30s)`  
**Cause:** Provider didn't respond within timeout window

**Fix:**
```bash
# 1. Check current timeout
grep "timeout:" ~/.tokenpak/config.yaml

# 2. Increase timeout (if reasonable)
timeout: 60  # Was 30

# 3. Check provider latency
time curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: YOUR_KEY" \
  -H "content-type: application/json" \
  -d '{"model": "claude-3-haiku", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]}'

# 4. If provider is genuinely slow, add request queuing
grep "queue:" ~/.tokenpak/config.yaml
# Increase queue_size if needed

# 5. Restart
tokenpak serve
```

---

## Debugging Response Headers

When you get an error, check the response headers for clues:

```bash
curl -i http://localhost:8766/v1/messages
```

Look for:
- **`Retry-After`** — How long to wait before retrying (in seconds)
- **`X-RateLimit-Limit`** — Your rate limit
- **`X-RateLimit-Remaining`** — Requests remaining
- **`X-RateLimit-Reset`** — When limit resets (Unix timestamp)
- **`X-Request-ID`** — Use this when reporting bugs

Example:
```
HTTP/1.0 429 Too Many Requests
Retry-After: 30
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1742424130
X-Request-ID: req-92384uasdh
```

---

## How to Report an Error

If you get an error not listed here:

1. **Capture the full response:**
   ```bash
   curl -v http://localhost:8766/... 2>&1 | tee error-output.txt
   ```

2. **Collect logs:**
   ```bash
   tokenpak serve --debug 2>&1 | tee debug.log &
   # Reproduce the error
   curl http://localhost:8766/...
   # Stop proxy (Ctrl+C)
   ```

3. **File a GitHub issue:**
   - Include: error code, HTTP status, error message
   - Include: your config (sanitize API keys!)
   - Include: `tokenpak --version && python3 --version && uname -a`
   - Include: debug logs (debug.log)

---

**Last Updated:** 2026-03-17  
**For:** TokenPak OSS v1.0
