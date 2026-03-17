# TokenPak Error Reference

All TokenPak errors have a consistent structure:
- **Error Code:** TP-Exxx (where xxx is error category)
- **Message:** Human-readable description
- **Suggestion:** Actionable fix step
- **Context:** Optional additional details

## Error Categories

### TP-E0xx: Config Errors

#### TP-E001: Config Error
Generic config error. Check the context for details.

#### TP-E002: Config Validation Error
A config field has an invalid value.

**Common Causes:**
- Type mismatch (e.g., port is a string instead of integer)
- Value out of range (e.g., port > 65535)
- Invalid format (e.g., URL is malformed)

**Fix:**
1. Check the field mentioned in the error
2. Verify the type and value match requirements
3. Reload config and retry

#### TP-E003: Missing Config Field
A required config field is missing.

**Common Causes:**
- Config file incomplete
- Typo in config field name

**Fix:**
1. Add the missing field to your config
2. Use the suggested value or check documentation

#### TP-E004: Invalid Config File
Config file is invalid JSON or doesn't exist.

**Common Causes:**
- Config file has syntax errors
- Config file path is incorrect
- File was deleted

**Fix:**
1. Check file exists: `ls /path/to/config.json`
2. Validate JSON: `python -m json.tool /path/to/config.json`
3. Fix syntax errors or update file path

---

### TP-E1xx: Connection/Network Errors

#### TP-E101: Connection Error
Generic connection error. Check context for details.

#### TP-E102: Provider Connection Error
Failed to connect to a provider (e.g., Anthropic API).

**Common Causes:**
- Network is down
- Provider API is unreachable
- Firewall blocking connection
- Provider endpoint in config is wrong

**Fix:**
1. Check your internet connection
2. Check provider status: https://status.anthropic.com
3. Verify provider URL in config is correct
4. Check firewall/proxy settings

#### TP-E103: Timeout Error
Request to a service timed out.

**Common Causes:**
- Network is slow
- Provider is slow to respond
- Timeout value is too short

**Fix:**
1. Check network connectivity
2. Check provider status
3. Increase timeout in config if needed
4. Retry the request

---

### TP-E2xx: Authentication Errors

#### TP-E201: Authentication Error
Generic auth error. Check context for details.

#### TP-E202: Invalid API Key
The API key for a provider is invalid or expired.

**Common Causes:**
- API key is wrong
- API key has been revoked
- API key has expired

**Fix:**
1. Get a new API key from the provider (e.g., https://console.anthropic.com)
2. Update your TokenPak config with the new key
3. Restart TokenPak

#### TP-E203: Missing API Key
No API key configured for a provider.

**Common Causes:**
- Key not added to config
- Typo in provider name
- Provider not enabled

**Fix:**
1. Get API key from the provider
2. Add to config under `api_keys` section
3. Match provider name exactly (case-sensitive)

---

### TP-E3xx: Rate Limiting Errors

#### TP-E301: Rate Limit Exceeded
You've exceeded the rate limit for a provider.

**Common Causes:**
- Too many requests in the rate window
- Rate limit quota is low
- Multiple clients using same key

**Fix:**
1. Wait before retrying (see `retry_after` in error)
2. Check provider rate limit settings
3. Upgrade to higher tier if available
4. Use separate API keys for different clients

---

### TP-E4xx: Cache Errors

#### TP-E401: Cache Error
Generic cache error. Check context for details.

#### TP-E402: Cache Corrupted
Cache data is corrupted and cannot be used.

**Common Causes:**
- Disk error while writing cache
- Cache database corrupted
- File permissions issue

**Fix:**
1. Clear the cache: `tokenpak cache clear`
2. Restart TokenPak
3. If persists, check disk health

---

### TP-E5xx: Provider (Upstream) Errors

#### TP-E501: Provider Error
The upstream provider returned an error.

**Common Causes:**
- Provider API error (e.g., 500, 503)
- Request invalid for provider
- Provider maintenance

**Fix:**
1. Check provider status page
2. Verify request format is correct
3. Retry after provider recovers
4. Check TokenPak logs for details

#### TP-E502: Unknown Provider Error
Unknown error from upstream provider.

**Common Causes:**
- Unexpected API response
- Provider API changed
- Other provider issue

**Fix:**
1. Check provider status
2. Check TokenPak logs
3. Report issue to TokenPak GitHub
4. Retry request

---

### TP-E6xx: Internal/System Errors

#### TP-E601: Internal Error
Internal TokenPak error.

**Common Causes:**
- Unexpected condition in code
- Memory/resource issue
- File system error

**Fix:**
1. Check TokenPak logs
2. Check system resources (disk, memory)
3. Restart TokenPak
4. Report to GitHub if persists

#### TP-E602: Not Implemented
Feature is not yet implemented.

**Common Causes:**
- Feature is planned but not ready
- Using unsupported provider
- Using unsupported config option

**Fix:**
1. Check documentation for available features
2. Check GitHub for planned features
3. Subscribe to updates or request feature

---

## Getting Help

When you see an error:

1. **Read the message** — it explains what went wrong
2. **Check the suggestion** — it tells you how to fix it
3. **Read this reference** — for more context on the error code
4. **Check the logs** — `tail -f ~/.tokenpak/tokenpak.log`
5. **GitHub Issues** — https://github.com/openclaw/tokenpak/issues

## Error Log Format

Errors are logged with full context:

```
2026-03-16 19:00:00 ERROR TP-E202: Invalid API key for anthropic
  Context: GET https://api.anthropic.com/v1/messages
  Fix: Check your anthropic API key in TokenPak config
```

This helps diagnose issues quickly.
