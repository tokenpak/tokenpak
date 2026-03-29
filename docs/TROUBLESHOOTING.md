---
title: Troubleshooting
---

# Troubleshooting

This guide covers the most common issues encountered when running TokenPak.
For conceptual explanations and FAQ-style answers, see [FAQ.md](FAQ.md).
For error codes and HTTP status references, see [Error Handling](error-handling.md).

---

## 1. Proxy Won't Start / Port 8766 Already in Use

**Symptom:**  
`Address already in use` or `OSError: [Errno 98]` when starting the proxy.

**Cause:**  
Another process (previous proxy instance, orphaned worker, or unrelated service) is bound to port 8766.

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
```

If this happens repeatedly after reboots, check `systemctl --user status tokenpak` for crash loops. See issue #8 (SIGTERM restart loops) below.

---

## 2. "API Key Missing" or Auth Errors

**Symptom:**  
`401 Unauthorized`, `Missing API key`, or provider returns auth error on every request.

**Cause:**  
`~/.openclaw/.env` is missing, has the wrong key name, or was not reloaded after update.

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

> See [FAQ.md — Security & Privacy](FAQ.md) for how API keys are managed across agents.

---

## 3. High Latency (Proxy Overhead vs. Upstream)

**Symptom:**  
Requests through TokenPak are noticeably slower than calling the provider directly.

**Cause:**  
Usually one of: vault index loading on first request, cache cold start, StageTrace overhead, or network routing (loopback + provider).

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

Expected proxy overhead: **2–15ms** per request on local hardware. Anything higher indicates a system issue.

---

## 4. Cache Hit Rate Unexpectedly Low

**Symptom:**  
`/stats` shows `cache_hit_rate: 0` or very low percentage despite sending repeated queries.

**Cause:**  
- Caching disabled in config
- Requests have non-deterministic fields (temperature > 0, timestamps in prompt, etc.)
- TTL too short
- Cache cleared by restart

**Fix:**
```bash
# Check cache config
grep -i cache ~/tokenpak/proxy.yaml

# Check current cache stats
curl -s http://localhost:8766/stats | python3 -m json.tool | grep -i cache
```

For caching to work, requests must be **byte-identical** (same model, same prompt, same parameters). If you're getting varied responses intentionally, low cache hit rate is expected.

> See [FAQ.md — How does caching work?](FAQ.md) for TTL and per-request override details.

---

## 5. `tokenpak: command not found` After Install

**Symptom:**  
`bash: tokenpak: command not found` despite installing via pip.

**Cause:**  
pip installed the binary into a path not in `$PATH`, or you're in a virtual environment where the CLI wasn't installed.

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
```

If using a venv, activate it first or use the full path:
```bash
~/myenv/bin/tokenpak
```

---

## 6. Vault Index Stale / Vault Context Not Loading

**Symptom:**  
Requests that should include vault context return responses with no vault awareness. `/stats` shows `vault_hits: 0`.

**Cause:**  
Vault index is out of date, pointing to a wrong path, or the rebuild hasn't run since vault content changed.

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

Index auto-reloads every 5 minutes (`VAULT_INDEX_RELOAD_INTERVAL=300s`). If you need immediate reload, restart the proxy:
```bash
systemctl --user restart tokenpak
```

---

## 7. StageTrace or Compression Stage Silently Skipped

**Symptom:**  
Expected compression or stage processing isn't happening. Requests pass through unmodified. No error in logs.

**Cause:**  
Stage is disabled in config, or the stage threshold wasn't met (e.g., prompt too short to compress), or a stage dependency failed silently.

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

## 8. SIGTERM Restart Loops (systemd)

**Symptom:**  
`systemctl --user status tokenpak` shows `(Result: signal)` and restarts repeatedly. Logs show `SIGTERM` or `Killed`.

**Cause:**  
Usually out-of-memory (OOM) kill from the kernel, or a misconfigured `ExecStop` command that sends SIGTERM before the process initializes.

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

---

## 9. Agent Proxy Drift — Running Old Version

**Symptom:**  
`check-proxy-drift.sh` reports a diff between vault canonical and a deployed agent's proxy. Or an agent behaves differently from others.

**Cause:**  
A sync was run when the agent was offline, or someone edited `~/tokenpak/proxy.py` directly on an agent machine instead of going through the vault workflow.

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

> ⚠️ **Never edit `~/tokenpak/proxy.py` directly on agent machines.** Always edit `~/tokenpak-dev/proxy.py` on staging, commit to vault, then deploy via `sync-tokenpak-proxy.sh`. Direct edits will be overwritten on next sync.

---

## 10. /stats Endpoint Slow (>100ms)

**Symptom:**  
`curl http://localhost:8766/stats` takes >100ms to respond. UI dashboards or monitoring feel sluggish.

**Cause:**  
Stats aggregation is computing over a large request history buffer, or the vault index scan is happening inline with the stats request.

**Fix:**
```bash
# Measure baseline
time curl -s http://localhost:8766/stats > /dev/null

# Check request history size in stats output
curl -s http://localhost:8766/stats | python3 -m json.tool | grep -i "total_requests\|history"
```

If request count is very high (>10k), the in-memory history buffer may be large. Restart the proxy to clear it (history is not persisted):
```bash
systemctl --user restart tokenpak
```

For persistent slow stats, reduce history buffer size in `proxy.yaml`:
```yaml
stats:
  history_limit: 1000   # Keep only last 1000 requests in memory
```

Expected `/stats` response time: **<20ms** on local hardware.

---

## Still Stuck?

1. Check [FAQ.md](FAQ.md) for conceptual explanations
2. Check [Error Handling](error-handling.md) for specific error codes
3. Run `journalctl --user -u tokenpak --no-pager -n 100` for recent logs
4. Open an issue: <https://github.com/kaywhy331/tokenpak/issues>
