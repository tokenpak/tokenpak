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

## 7) Standard Health Commands

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
