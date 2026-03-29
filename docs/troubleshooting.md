# TokenPak Troubleshooting Guide

**Find your problem below and solve it in under 60 seconds.**

This guide is organized by symptom. Look for your error message or behavior, read the **Problem → Cause → Fix** section, and follow the exact steps.

---

## 1. Can't Connect to TokenPak

**Problem:** `Connection refused`, `[Errno 111]`, or proxy won't respond  
**Common Symptoms:** `curl localhost:8766` returns "refused" or times out

### Cause
- TokenPak is not running
- Wrong port number in your client config
- Firewall blocking the connection
- Proxy is listening on 127.0.0.1 but you're connecting from a different host

### Fix

**Step 1: Check if TokenPak is running**
```bash
ps aux | grep tokenpak
```
If you see no process, TokenPak crashed or wasn't started.

**Step 2: Check the configured port**
```bash
grep "port:" ~/.tokenpak/config.yaml
```
Note the port number (default is `8766`). Verify your client is connecting to the same port.

**Step 3: Verify the proxy is listening**
```bash
lsof -i :8766
```
If nothing shows up, the proxy is not bound to that port. Restart it:
```bash
tokenpak serve
```

**Step 4: Test local connectivity**
```bash
curl -v http://localhost:8766/health
```
If that works, check firewall rules:
```bash
sudo ufw status
# OR
sudo iptables -L -n | grep 8766
```

**Step 5: If connecting from another machine**
Edit `~/.tokenpak/config.yaml` and change:
```yaml
listen_addr: "127.0.0.1"  # Change this to "0.0.0.0"
```
Then restart:
```bash
tokenpak serve
```

---

## 2. 401 Unauthorized — Missing or Invalid API Key

**Problem:** Requests return `401` or "Unauthorized"  
**Error Message:** `Invalid or missing authentication`

### Cause
- You didn't pass an API key
- The key format is wrong (e.g., missing "Bearer ")
- The key is not in the headers at all
- TokenPak is configured without checking auth (rare)

### Fix

**Step 1: Verify you're sending an Authorization header**
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8766/api/request
```

**Step 2: Check your API key format**
Your key must be passed as:
```
Authorization: Bearer <your_key_here>
```
NOT just `Authorization: <your_key_here>` or `X-API-Key: <your_key>`

**Step 3: Check TokenPak auth configuration**
```bash
grep -A 5 "auth:" ~/.tokenpak/config.yaml
```
Ensure it looks like:
```yaml
auth:
  enabled: true
  keys:
    - "your-key-here"
```

**Step 4: Generate a new test key if needed**
```bash
tokenpak auth --generate
```
This outputs a new valid key. Use it in your requests.

**Step 5: Verify the key exists**
```bash
grep "keys:" -A 10 ~/.tokenpak/config.yaml
```
Copy one of the listed keys and test:
```bash
curl -H "Authorization: Bearer <copied_key>" http://localhost:8766/health
```

---

## 3. 502 Bad Gateway — Upstream Provider Error

**Problem:** Requests to OpenAI/Anthropic/other providers fail with `502`  
**Error Message:** `upstream service returned error`

### Cause
- Provider API key is invalid or expired
- Provider rate limit exceeded
- Provider API is down or unreachable
- Your network can't reach the provider

### Fix

**Step 1: Check which provider failed**
```bash
tail -50 ~/.tokenpak/logs/tokenpak.log
```
Look for "upstream_error" or "provider:" lines. Note the provider name.

**Step 2: Verify the provider API key**
```bash
grep -E "anthropic_key|openai_key|provider_keys" ~/.tokenpak/config.yaml
```
Check that you have a key for the failing provider. If missing, add it:
```yaml
anthropic_key: "sk-ant-..."
openai_key: "sk-..."
```

**Step 3: Test the provider API directly**
For Anthropic:
```bash
curl -X POST https://api.anthropic.com/v1/messages \
  -H "x-api-key: YOUR_KEY" \
  -H "content-type: application/json" \
  -d '{"model": "claude-3-sonnet", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}'
```

For OpenAI:
```bash
curl -X POST https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "content-type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}'
```

**Step 4: If provider API is unreachable**
Check your network:
```bash
ping api.anthropic.com
traceroute api.anthropic.com
```

**Step 5: Retry with exponential backoff**
TokenPak will retry failed requests. Check the retry settings:
```bash
grep -E "retry|backoff" ~/.tokenpak/config.yaml
```

---

## 4. 429 Too Many Requests — Rate Limit Hit

**Problem:** Requests return `429` or "Rate Limit Exceeded"  
**Message:** `too many requests`, `Retry-After`

### Cause
- You exceeded TokenPak's rate limit
- You exceeded your provider's rate limit (and TokenPak passed it through)
- Cache hit rate is low (more requests hitting the provider)

### Fix

**Step 1: Check TokenPak rate limit**
```bash
grep -A 5 "rate_limit:" ~/.tokenpak/config.yaml
```
Look for values like:
```yaml
rate_limit:
  requests_per_second: 100
  burst_size: 500
```

**Step 2: Increase TokenPak limits if needed**
Edit `~/.tokenpak/config.yaml`:
```yaml
rate_limit:
  requests_per_second: 200  # Increase this
  burst_size: 1000          # Increase this
```
Then restart:
```bash
tokenpak serve
```

**Step 3: Check provider limits**
```bash
grep -E "provider_rate_limit|anthropic.*limit|openai.*limit" ~/.tokenpak/config.yaml
```
If these are too low, increase them. However, note your provider's actual limits:
- Anthropic: up to 40 requests/min on free tier, 10k/min on paid
- OpenAI: varies by model; check your account

**Step 4: Check the `Retry-After` header**
When you get a 429, look at the response headers:
```bash
curl -i http://localhost:8766/request
```
The `Retry-After` header tells you how many seconds to wait before retrying.

**Step 5: Improve cache hit rate**
If most of your requests are hitting the limit, you're not caching well:
```bash
curl http://localhost:8766/metrics
```
Look for `cache_hit_rate`. If it's below 50%, check your cache TTL:
```bash
grep "cache:" -A 3 ~/.tokenpak/config.yaml
```
Increase the TTL:
```yaml
cache:
  ttl_seconds: 3600  # Increase from 1800 or lower
```

---

## 5. Config Won't Load — Syntax or Format Errors

**Problem:** Startup fails with "config error" or "failed to parse"  
**Error:** `YAML error`, `JSON error`, `invalid syntax`

### Cause
- YAML indentation is wrong (2 spaces, not 4 or tabs)
- JSON has trailing commas or other syntax errors
- A required field is missing
- A field has the wrong type (string instead of number, etc.)

### Fix

**Step 1: Validate your config file**
```bash
python3 -c "import yaml; yaml.safe_load(open(Path.home() / '.tokenpak' / 'config.yaml'))"
```
If there's an error, it will print the exact line and problem.

**Step 2: Check indentation**
YAML must use exactly 2 spaces per indent level. NO TABS. View it:
```bash
cat -A ~/.tokenpak/config.yaml | head -20
```
If you see `^I`, that's a tab — replace with 2 spaces.

**Step 3: Check required fields**
Your config must have at minimum:
```yaml
port: 8766
providers:
  - name: "anthropic"
    api_key: "sk-ant-..."
```

**Step 4: Verify field types**
```bash
# port must be a number (not "8766" in quotes)
port: 8766

# enable_cache must be true/false (not "yes"/"no")
enable_cache: true

# ttl_seconds must be a number
cache:
  ttl_seconds: 3600
```

**Step 5: Use a config validation tool**
```bash
tokenpak config validate
```
This checks your entire config and reports errors.

---

## 6. Docker Container Exits Immediately

**Problem:** Container starts and then exits instantly  
**Error:** Container exited with code 1, or no logs

### Cause
- TokenPak config file not mounted or at wrong path
- Missing environment variables
- Port 8766 is already in use on the host
- File permissions are wrong

### Fix

**Step 1: Check container logs**
```bash
docker logs <container_id>
# Or if it exited:
docker logs --since 5m <container_id>
```
Look for the actual error message.

**Step 2: Verify config mount**
```bash
docker run -it \
  -v ~/.tokenpak/config.yaml:/root/.tokenpak/config.yaml \
  -p 8766:8766 \
  tokenpak:latest
```
The config must be mounted at `/root/.tokenpak/config.yaml` inside the container.

**Step 3: Check port conflicts**
```bash
sudo lsof -i :8766
```
If something is using port 8766, either:
- Kill that process: `kill <PID>`
- Or map to a different port: `-p 9000:8766`

**Step 4: Verify environment variables**
If using env vars instead of config file:
```bash
docker run -it \
  -e TOKENPAK_PORT=8766 \
  -e TOKENPAK_ANTHROPIC_KEY=sk-ant-... \
  -p 8766:8766 \
  tokenpak:latest
```

**Step 5: Run in foreground with full output**
```bash
docker run --rm -it \
  -v ~/.tokenpak/config.yaml:/root/.tokenpak/config.yaml \
  -p 8766:8766 \
  tokenpak:latest /bin/bash -c "tokenpak serve --debug"
```
The `--debug` flag gives more detailed logs.

---

## 7. pip install Fails — Python or Dependency Issues

**Problem:** `pip install tokenpak` fails  
**Error:** `No matching distribution`, `Incompatible wheel`, `Dependency conflict`

### Cause
- Python version is too old (< 3.8)
- pip is outdated
- A dependency version conflicts with something else you have installed
- You're using an unsupported platform (Windows without WSL, etc.)

### Fix

**Step 1: Check Python version**
```bash
python3 --version
```
TokenPak requires Python 3.8+. If you have an older version:
- macOS: `brew install python@3.11`
- Ubuntu/Debian: `sudo apt update && sudo apt install python3.11 python3.11-venv`
- Then use: `python3.11 -m pip install tokenpak`

**Step 2: Upgrade pip**
```bash
python3 -m pip install --upgrade pip
```

**Step 3: Use a virtual environment**
```bash
python3 -m venv tokenpak-env
source tokenpak-env/bin/activate
pip install tokenpak
```
This isolates TokenPak from your system Python.

**Step 4: Check for conflicting packages**
```bash
pip list | grep -E "yaml|requests|httpx"
```
If you see multiple versions of the same package, your environment is corrupted. Start fresh:
```bash
python3 -m venv tokenpak-clean
source tokenpak-clean/bin/activate
pip install tokenpak
```

**Step 5: Install with verbose output**
```bash
pip install tokenpak -vvv
```
This shows exactly where the installation fails.

---

## 8. High Latency — Slow Requests

**Problem:** Requests take 5+ seconds even for simple queries  
**User Report:** "The proxy is slow"

### Cause
- You're hitting the provider (low cache hit rate)
- TokenPak is doing expensive validation or processing
- Network latency to the provider is high
- Your client has a slow connection

### Fix

**Step 1: Measure latency breakdown**
```bash
curl -w "
Namelookup: %{time_namelookup}
Connect: %{time_connect}
AppConnect: %{time_appconnect}
Pretransfer: %{time_pretransfer}
Redirect: %{time_redirect}
Starttransfer: %{time_starttransfer}
Total: %{time_total}
\n" http://localhost:8766/api/request
```
This tells you where the time is spent.

**Step 2: Check cache hit rate**
```bash
curl http://localhost:8766/metrics | grep cache_hit_rate
```
If it's below 70%, most requests are hitting the provider. Nothing you can do about network latency to the provider, but you can:
- Increase cache TTL
- Increase batch sizes
- Use compression

**Step 3: Enable compression**
```bash
grep "compression:" ~/.tokenpak/config.yaml
```
Ensure compression is enabled:
```yaml
compression:
  enabled: true
  level: 6
```

**Step 4: Check provider latency directly**
```bash
time curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: YOUR_KEY" \
  -H "content-type: application/json" \
  -d '{"model": "claude-3-haiku", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]}'
```
If the provider itself is slow (>2s), that's not a TokenPak issue.

**Step 5: Profile TokenPak overhead**
```bash
tokenpak serve --profile
# Then run a request
curl http://localhost:8766/api/request
# Check the log for timing breakdowns
tail ~/.tokenpak/logs/tokenpak.log | grep "timing\|duration"
```

---

## 9. Cost Data Missing or Shows Zero

**Problem:** Dashboard shows `$0.00 spent` or cost metrics are empty  
**Symptom:** Requests work, but cost tracking isn't

### Cause
- Token counting is not enabled
- Provider doesn't support token counting (unlikely)
- Model is not in the token pricing database
- Telemetry is not being written

### Fix

**Step 1: Check if token counting is enabled**
```bash
grep -E "token_counting|track_tokens|cost_tracking" ~/.tokenpak/config.yaml
```
Should see:
```yaml
cost_tracking:
  enabled: true
```
If not found or false, enable it and restart.

**Step 2: Verify telemetry is writing**
```bash
ls -lh ~/.tokenpak/telemetry.db
```
If the file hasn't been modified recently (`date` shows old time), telemetry isn't working.

**Step 3: Check for errors in logs**
```bash
grep -i "cost\|token\|telemetry" ~/.tokenpak/logs/tokenpak.log
```
Look for error messages like "unknown model" or "failed to count tokens".

**Step 4: Verify model is known**
```bash
tokenpak models list
```
Check if your model (e.g., "claude-3-sonnet") is in the list. If not:
```bash
tokenpak models add --model="claude-3-sonnet" --input-tokens=8000 --output-tokens=8000
```

**Step 5: Check token pricing**
```bash
grep -A 10 "claude-3-sonnet" ~/.tokenpak/pricing.yaml
```
Should show:
```yaml
claude-3-sonnet:
  input_cost_per_1m: 3.00
  output_cost_per_1m: 15.00
```
If missing or zero, costs won't be calculated.

---

## 10. Logs Not Showing or Wrong Log Level

**Problem:** Debug logs don't appear, or you're not seeing expected messages  
**Error:** Silent failures or insufficient info to diagnose

### Cause
- Log level is set too high (only showing errors, not warnings/debug)
- Log file path is wrong or not writable
- Logs are going to stdout but you're not capturing them

### Fix

**Step 1: Check log configuration**
```bash
grep -A 5 "logging:" ~/.tokenpak/config.yaml
```
Should look like:
```yaml
logging:
  level: "DEBUG"  # or INFO, WARNING, ERROR
  file: "~/.tokenpak/logs/tokenpak.log"
  max_size_mb: 100
  max_backups: 5
```

**Step 2: Set log level to DEBUG**
Edit `~/.tokenpak/config.yaml`:
```yaml
logging:
  level: "DEBUG"
```
Then restart:
```bash
tokenpak serve
```

**Step 3: Verify log file exists and is writable**
```bash
touch ~/.tokenpak/logs/test.log && rm ~/.tokenpak/logs/test.log
echo $?
```
If you get "permission denied", fix permissions:
```bash
mkdir -p ~/.tokenpak/logs
chmod 755 ~/.tokenpak/logs
```

**Step 4: Check logs in real-time**
```bash
tail -f ~/.tokenpak/logs/tokenpak.log
```
Then run a request in another terminal. You should see detailed logs.

**Step 5: Enable stdout logging**
If you prefer logs to console instead of file:
```yaml
logging:
  level: "DEBUG"
  output: "stdout"
```
Then restart and logs will appear in your terminal.

---

## 11. Cache Not Working — Hit Rate is Low

**Problem:** Cache hit rate below 50% even for repeated requests  
**Symptom:** Every identical request costs money and takes time

### Cause
- Cache is disabled
- TTL is too short (entries expire immediately)
- Cache key doesn't match (different params treated as different queries)
- Cache storage is corrupt

### Fix

**Step 1: Check if cache is enabled**
```bash
grep "cache:" -A 5 ~/.tokenpak/config.yaml
```
Should see:
```yaml
cache:
  enabled: true
  ttl_seconds: 3600
```

**Step 2: Increase TTL**
```yaml
cache:
  ttl_seconds: 86400  # 24 hours instead of 1 hour
```

**Step 3: Check cache hit rate**
```bash
curl http://localhost:8766/metrics | grep cache
```
Sample same request twice:
```bash
curl -X POST http://localhost:8766/api/request \
  -d '{"model": "claude-3-sonnet", "messages": [{"role": "user", "content": "what is 2+2?"}]}'
# Run again with exact same payload
curl -X POST http://localhost:8766/api/request \
  -d '{"model": "claude-3-sonnet", "messages": [{"role": "user", "content": "what is 2+2?"}]}'
```
Check metrics again — hit rate should increase.

**Step 4: Clear and rebuild cache**
```bash
tokenpak cache clear
# Then send some requests to warm up the cache
```

**Step 5: Verify cache storage location**
```bash
grep "cache_dir:" ~/.tokenpak/config.yaml
```
Default is `~/.tokenpak/cache`. Check it exists:
```bash
ls -lh ~/.tokenpak/cache/
```
If the directory is full or corrupted, clear it:
```bash
rm -rf ~/.tokenpak/cache/*
```

---

## Getting More Help

**If you've tried the above and still stuck:**

1. **Check existing issues** on GitHub:
   ```
   https://github.com/suewu/tokenpak/issues
   ```

2. **File a bug report** with:
   - Your exact error message (paste the full output)
   - The steps you took to reproduce
   - Your config (sanitize API keys!)
   - Output of: `tokenpak --version && python3 --version && uname -a`

3. **Enable debug mode and collect logs**:
   ```bash
   tokenpak serve --debug 2>&1 | tee debug-session.log
   # Run the failing request/command
   # Attach debug-session.log to your issue
   ```

4. **Check error codes** in `docs/error-codes.md` for more details on specific 400/500 errors.

---

**Last Updated:** 2026-03-17  
**For:** TokenPak OSS v1.0
