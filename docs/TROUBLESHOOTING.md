# TokenPak Deployment Troubleshooting Guide

Use this guide to quickly diagnose and resolve common TokenPak deployment issues in local, container, and cloud environments.

---

## 1) Startup Issues

### 1.1 Port already in use

**Symptoms**
- Startup fails with bind error
- Health endpoint never comes up

**Example errors**
```text
OSError: [Errno 98] Address already in use
Error: listen EADDRINUSE: address already in use 0.0.0.0:8787
```

**Diagnostics**
```bash
# Check what process is holding the port
sudo lsof -iTCP:8787 -sTCP:LISTEN -n -P

# Alternative
ss -ltnp | grep :8787
```

**Fixes**
1. Stop the conflicting process.
2. Change TokenPak port (`PORT` env var or deployment config).
3. Restart and verify:
```bash
curl -fsS http://127.0.0.1:8787/health
```

---

### 1.2 Config file not found

**Symptoms**
- Service exits immediately
- Defaults are loaded unintentionally

**Example errors**
```text
FileNotFoundError: config/tokenpak.yml
ERROR: failed to load configuration from /app/config/tokenpak.yml
```

**Diagnostics**
```bash
# Verify mount/path
ls -lah /app/config

# Verify runtime env
printenv | grep -E 'TOKENPAK|CONFIG|PORT|ENV'
```

**Fixes**
1. Confirm correct config path in startup command.
2. Ensure ConfigMap/volume mount is attached (K8s/ECS task def/Cloud Run secret mount).
3. Add a startup assertion for required keys before serving traffic.

---

### 1.3 Missing dependencies

**Symptoms**
- Crash during import or boot
- Container starts then exits repeatedly

**Example errors**
```text
ModuleNotFoundError: No module named 'brotli'
ImportError: libzstd.so.1: cannot open shared object file
```

**Diagnostics**
```bash
# In container/shell
python -V
pip freeze | grep -E 'brotli|zstandard|uvicorn|fastapi'

# Validate image build layers
docker image inspect tokenpak:latest --format '{{.Id}}'
```

**Fixes**
1. Rebuild image with clean cache:
```bash
docker build --no-cache -t tokenpak:latest .
```
2. Install missing package/version pin in `requirements.txt`.
3. Re-deploy and watch logs for first successful startup.

---

### 1.4 Permission errors

**Symptoms**
- Cannot write cache/temp/log files
- Service works locally, fails in container/cloud

**Example errors**
```text
PermissionError: [Errno 13] Permission denied: '/var/log/tokenpak.log'
EACCES: permission denied, mkdir '/app/cache'
```

**Diagnostics**
```bash
id
ls -lah /app /app/cache /var/log
```

**Fixes**
1. Ensure runtime user owns writable directories.
2. In Dockerfile, create/chown write paths during build.
3. In orchestrator, set security context or writable volume.

---

## 2) Health & Performance

### 2.1 High latency

**Symptoms**
- P95/P99 response times spike
- Client timeout/retry rates increase

**Checks**
```bash
# Service health
curl -sS http://127.0.0.1:8787/health | jq

# Quick endpoint timing
curl -s -o /dev/null -w 'dns=%{time_namelookup} connect=%{time_connect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  http://127.0.0.1:8787/health
```

**Likely causes**
- Upstream model/API latency
- Cold starts / underprovisioned CPU
- Serialization/compression overhead

**Fixes**
1. Scale replicas or CPU.
2. Warm instances before traffic cutover.
3. Enable response caching for repeat prompts.
4. Reduce payload size and disable heavy debug traces in prod.

---

### 2.2 Memory leaks

**Symptoms**
- RSS climbs continuously
- OOMKilled events / restarts

**Checks**
```bash
# Process memory over time
ps -o pid,ppid,%mem,rss,vsz,cmd -p $(pgrep -f 'proxy.py|tokenpak')

# Container memory stats
docker stats --no-stream
```

**Fixes**
1. Confirm object/cache eviction policy is active.
2. Limit max in-memory cache size (env/config).
3. Capture heap profile in staging and identify retained objects.
4. Roll with shorter instance lifetime until root cause is fixed.

---

### 2.3 CPU spikes

**Symptoms**
- Request queueing
- Autoscaler churn

**Checks**
```bash
top -H -p $(pgrep -f 'proxy.py|tokenpak' | head -1)

# If available
pidstat -u -p $(pgrep -f 'proxy.py|tokenpak' | tr '\n' ',') 1 5
```

**Fixes**
1. Reduce compression level for hot paths.
2. Tune worker count for available vCPU.
3. Offload expensive preprocessing to async/background stage.

---

### 2.4 Connection timeouts

**Symptoms**
- 504/408 from gateway/load balancer
- Client retries succeed intermittently

**Checks**
```bash
# Test from app host and from outside network path
curl -v --max-time 15 http://127.0.0.1:8787/health

# Check LB idle/request timeout settings (platform-specific)
```

**Fixes**
1. Increase upstream timeout to exceed worst-case processing time.
2. Add client-side exponential backoff.
3. Set sane keepalive settings between LB and service.

---

## 3) Compression Issues

### 3.1 Blocks not compressing

**Symptoms**
- Responses always uncompressed
- Compression stage appears skipped

**Checks**
```bash
# Confirm compression flags are enabled
printenv | grep -E 'TOKENPAK_(COMPRESSION|CACHE|DEBUG)'

# Verify response headers
curl -i http://127.0.0.1:8787/health | grep -i 'content-encoding\|x-tokenpak'
```

**Fixes**
1. Enable compression feature flag.
2. Ensure minimum block size threshold is not too high.
3. Verify route middleware includes compression stage.

---

### 3.2 Compression ratio is low

**Symptoms**
- Large outputs with little size reduction

**Checks**
```bash
# Compare original vs compressed payload lengths from logs/metrics
# Example jq extraction if JSON logs are available:
cat /var/log/tokenpak.jsonl | jq '.orig_bytes, .comp_bytes' | head
```

**Fixes**
1. Increase compression level (with CPU tradeoff).
2. Use dictionary/training if payloads share recurring structures.
3. Exclude already-compressed payload types from redundant compression.

---

### 3.3 Cache misses too high

**Symptoms**
- Hit rate drops suddenly
- Repeated prompts not reusing cached output

**Checks**
```bash
# Example metrics endpoint
curl -sS http://127.0.0.1:8787/metrics | grep -E 'cache_hit|cache_miss|compression_ratio'
```

**Fixes**
1. Normalize cache keys (whitespace/system metadata noise).
2. Increase TTL for stable workloads.
3. Ensure cache backend persistence is configured correctly.

---

## 4) Logging & Diagnostics

### 4.1 Enable debug logging

```bash
# Example env toggle
export TOKENPAK_LOG_LEVEL=debug
export TOKENPAK_DEBUG_COMPRESSION=1
```

Restart service and confirm startup logs include debug level.

### 4.2 View request logs

```bash
# Docker
docker logs -f <container_name>

# systemd
journalctl -u tokenpak -f -n 200
```

### 4.3 Trace compression decisions

Capture fields per request:
- request_id
- route/model
- original_bytes
- compressed_bytes
- ratio
- cache_hit
- compression_reason (e.g., "below_threshold", "already_compressed")

### 4.4 Performance profiling

```bash
# Python profile snapshot (example)
python -m cProfile -o /tmp/tokenpak.prof proxy.py

# Inspect hottest functions
python - <<'PY'
import pstats
p = pstats.Stats('/tmp/tokenpak.prof')
p.sort_stats('cumtime').print_stats(30)
PY
```

---

## 5) Cloud Deployments

### 5.1 GCP Cloud Run errors

**Common issues**
- Container not listening on expected `PORT`
- Startup timeout due to slow init

**Checks**
```bash
gcloud run services logs read tokenpak --region us-central1 --limit 200
```

**Fixes**
1. Ensure app binds to `0.0.0.0:$PORT`.
2. Increase CPU for startup-heavy workloads.
3. Set min instances > 0 to reduce cold starts.

---

### 5.2 AWS ECS task failures

**Common issues**
- Task exits due to bad env/secret
- Health check path mismatch

**Checks**
```bash
aws ecs describe-tasks --cluster <cluster> --tasks <task-arn>
aws logs tail /ecs/tokenpak --follow
```

**Fixes**
1. Validate task definition env vars and secret ARNs.
2. Match container health check path/port to service config.
3. Increase task memory if OOM observed.

---

### 5.3 Azure Container Instances

**Common issues**
- Image pull/auth errors
- DNS/network resolution failures

**Checks**
```bash
az container logs --resource-group <rg> --name tokenpak
az container show --resource-group <rg> --name tokenpak --query "instanceView.events"
```

**Fixes**
1. Verify ACR credentials/managed identity access.
2. Confirm VNet/subnet and outbound egress rules.
3. Recreate instance after correcting env and secrets.

---

## 6) Quick Triage Runbook (10-minute flow)

1. **Is it up?** `curl /health`
2. **If down:** check startup logs + port/config/dependency/permission issues.
3. **If slow:** inspect latency + CPU/memory + upstream timings.
4. **If compression poor:** verify flags, thresholds, ratios, cache hit rate.
5. **If cloud-only:** use provider logs/events and fix runtime env/network mismatches.
6. **After fix:** validate health, run smoke test, and capture root cause + prevention note.

---

## 7) OpenClaw Integration Issues (2026-03-12 Session)

### 7.1 Primary model reverting after restart

**Symptoms**
- Gateway config shows TokenPak routing (e.g., `tokenpak-anthropic/claude-sonnet-4-6`)
- After restart, reverts to direct provider (e.g., `anthropic/claude-haiku-4-5`)
- Manual config patches don't persist

**Root cause**
The `tokenpak-inject.sh` script (runs as ExecStartPre) was overwriting manually-set TokenPak primaries during startup interleave process.

**Diagnostics**
```bash
# Check primary model
python3 -c "import json; cfg=json.load(open('$HOME/.openclaw/openclaw.json')); print(f'Primary: {cfg[\"agents\"][\"defaults\"][\"model\"][\"primary\"]}')"

# Check if it reverts after restart
systemctl --user restart openclaw-gateway.service
sleep 5
python3 -c "import json; cfg=json.load(open('$HOME/.openclaw/openclaw.json')); print(f'After restart: {cfg[\"agents\"][\"defaults\"][\"model\"][\"primary\"]}')"
```

**Fixes**
1. **Root cause fix (permanent):** Modify `~/.local/bin/tokenpak-inject.sh` to preserve explicitly-set TokenPak primaries:
```python
# In the interleave() function, add early exit:
if primary and is_tp(primary.split("/", 1)[0]):
    # Primary is already tokenpak — don't change it
    return model_cfg, False
```

2. **Workaround (temporary):** Use gateway config API instead of direct file edits:
```bash
# Set via API (persists better than file edits)
python3 << 'PYEOF'
import json
from pathlib import Path
cfg = json.load(open(Path.home() / '.openclaw/openclaw.json'))
cfg['agents']['defaults']['model']['primary'] = 'tokenpak-anthropic/claude-sonnet-4-6'
cfg['agents']['defaults']['model']['fallbacks'] = [
    'tokenpak-anthropic/claude-haiku-4-5',
    'anthropic/claude-haiku-4-5'
]
json.dump(cfg, open(Path.home() / '.openclaw/openclaw.json', 'w'), indent=2)
PYEOF
systemctl --user restart openclaw-gateway.service
```

3. **Verify fix:**
```bash
# Primary should persist after restart
python3 -c "import json; cfg=json.load(open('$HOME/.openclaw/openclaw.json')); print('✅ PERSISTS' if 'tokenpak' in cfg['agents']['defaults']['model']['primary'] else '❌ REVERTED')"
```

---

### 7.2 Missing TokenPak source files (bytecode only)

**Symptoms**
- ImportError: cannot import name 'server' from 'tokenpak.agent.proxy'
- Only .pyc files exist in `~/vault/Projects/ocp-protocol/packages/pypi/tokenpak/`
- Tests pass from cache but fresh imports fail

**Root cause**
Distribution package created from compiled Python bytecode without committing source files to git. When Python tries fresh import (not from cache), it fails because .pyc references missing source.

**Diagnostics**
```bash
# Check file count mismatch
find ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak -name "*.py" | wc -l
find ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak -name "*.pyc" | wc -l

# Try import (may work if cached)
python3 -c "from tokenpak.telemetry.adapters import anthropic"

# Fresh import (will fail if source missing)
python3 << 'EOF'
import sys
sys.modules.pop('tokenpak', None)
from tokenpak.telemetry.adapters import anthropic
EOF
```

**Fixes**
1. **Copy source from main repo (recommended):**
```bash
# Backup broken package
mv ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak.broken

# Copy complete source
cp -r ~/tokenpak ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak

# Verify import
python3 -c "from tokenpak.agent.proxy import server; print('✅ Import works')"

# Commit
cd ~/vault && git add -A && git commit -m "trix: recover tokenpak source in ocp-protocol package"
```

2. **Decompile .pyc files (fallback):**
```bash
pip install uncompyle6
uncompyle6 -r ~/vault/Projects/ocp-protocol/packages/pypi/tokenpak/ -o ~/tokenpak-recovered/
# Copy recovered source back to vault
```

---

### 7.3 Agent missing module updates

**Symptoms**
- One machine (Sue) has import errors while others work
- New modules/stubs created on Trix don't exist on Sue
- Tests fail with ImportError for recently-added code

**Root cause**
Git pull/sync not updated on remote machines when new modules added to main repo.

**Diagnostics**
```bash
# Compare available modules
python3 -c "import tokenpak.agent.semantic.term_card_resolver; print('✅ Module found')" 2>&1 || echo "❌ Module missing"

# Check git status
cd ~/tokenpak && git status
cd ~/tokenpak && git log --oneline -5 | head
```

**Fixes**
1. **Pull latest code:**
```bash
cd ~/tokenpak && git pull origin master
```

2. **Verify update:**
```bash
python3 -c "from tokenpak.agent.semantic.term_card_resolver import TermCardResolver; print('✅ Updated')"
```

3. **If issues persist:** Check for merge conflicts or stale branches:
```bash
cd ~/tokenpak && git fetch --all && git status
```

---

### 7.4 Case sensitivity collision in queue directories

**Symptoms**
- Tasks in `~/vault/Agents/trix/queue/` (lowercase) never execute
- Same agent has `~/vault/Agents/Trix/queue/` (uppercase) with active tasks
- Heartbeat script ignores lowercase directories

**Root cause**
Old automation created lowercase queue dirs; heartbeat monitoring scripts expect uppercase (case-sensitive filesystem). Tasks stuck in orphaned lowercase dirs.

**Diagnostics**
```bash
# Find orphaned lowercase dirs
find ~/vault/Agents -type d -name queue | grep -E 'trix|cali' | sort
ls -la ~/vault/Agents/{Trix,trix,Cali,cali}/queue 2>&1
```

**Fixes**
1. **Identify orphaned tasks:**
```bash
find ~/vault/Agents/{trix,cali}/queue -name "*.md" 2>/dev/null
```

2. **Delete lowercase directories:**
```bash
rm -rf ~/vault/Agents/trix/queue ~/vault/Agents/cali/queue
cd ~/vault && git add -A && git commit -m "trix: cleanup — remove orphaned lowercase queue dirs"
```

3. **Verify only uppercase remain:**
```bash
ls -ld ~/vault/Agents/{Trix,Cali}/queue
```

---

### 7.5 Stale compiled bytecode in __pycache__

**Symptoms**
- Fresh module added but old .pyc still loads
- Import errors reference functions that don't exist in source
- `__pycache__` directories growing large

**Root cause**
Python caches compiled .pyc files; if source changes but cache isn't invalidated, stale bytecode loads.

**Diagnostics**
```bash
# Check __pycache__ size
find ~/tokenpak -type d -name __pycache__ | wc -l
du -sh ~/tokenpak/.

# Check if .pyc is newer than .py
stat ~/tokenpak/tokenpak/core.py | grep Modify
stat ~/tokenpak/tokenpak/__pycache__/core*.pyc | grep Modify
```

**Fixes**
1. **Clear Python cache:**
```bash
find ~/tokenpak -type d -name __pycache__ -exec rm -rf {} +
find ~/tokenpak -name "*.pyc" -delete
```

2. **Force recompile on next import:**
```bash
python3 -c "import tokenpak; print('✅ Recompiled')"
```

3. **Verify no stale references:**
```bash
python3 -c "import tokenpak.agent.proxy.server; print(server.__file__)"
```

---

### 7.6 Duplicate model aliases causing routing conflicts

**Symptoms**
- Multiple aliases point to same model
- Unclear which route takes precedence
- Model selection unpredictable under fallback conditions

**Root cause**
Manual config edits and deduplication logic left duplicate aliases across machines (e.g., `opus` as alias on both tokenpak and direct anthropic).

**Diagnostics**
```bash
# Find duplicate aliases
python3 << 'EOF'
import json
from pathlib import Path
cfg = json.load(open(Path.home() / '.openclaw/openclaw.json'))
models = cfg['agents']['defaults']['models']
aliases = {}
for model, spec in models.items():
    alias = spec.get('alias')
    if alias:
        if alias in aliases:
            print(f"❌ Duplicate: {alias} → {aliases[alias]} AND {model}")
        else:
            aliases[alias] = model
EOF
```

**Fixes**
1. **Consolidate to TokenPak versions (recommended):**
```python
# Remove aliases from direct providers; keep only on tokenpak-* versions
# Example:
# Remove: "anthropic/claude-opus-4-6": {"alias": "opus"}
# Keep:   "tokenpak-anthropic/claude-opus-4-6": {"alias": "opus"}
```

2. **Verify no duplicates remain:**
```bash
python3 -c "
import json
from pathlib import Path
cfg = json.load(open(Path.home() / '.openclaw/openclaw.json'))
models = cfg['agents']['defaults']['models']
aliases = {}
for m, s in models.items():
    a = s.get('alias')
    if a and a in aliases:
        print(f'❌ {a}: {aliases[a]} + {m}')
    elif a:
        aliases[a] = m
print(f'✅ {len(aliases)} unique aliases')
"
```

---

## 8) Standard Health Commands

```bash
# Health
curl -fsS http://127.0.0.1:8787/health

# Metrics (if enabled)
curl -fsS http://127.0.0.1:8787/metrics | head -n 50

# Container logs
docker logs --tail 200 <container_name>

# Process + sockets
ps aux | grep -E 'proxy.py|tokenpak' | grep -v grep
ss -ltnp | grep 8787
```

Keep this guide alongside deployment docs and update it after each production incident.
