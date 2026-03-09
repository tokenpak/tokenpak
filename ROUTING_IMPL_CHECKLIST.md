# TokenPak Routing Implementation Checklist
**Prepared:** 2026-03-07  
**By:** Trix  
**Status:** Pre-implementation prep — awaiting Kevin approval (3/9 8AM)

---

## Pre-Implementation (Prep) ✅ COMPLETE

- [x] Routing config location identified
- [x] Proxy route registration method understood
- [x] Model aliases confirmed available
- [x] Kevin's routing plan reviewed (Cali's doc)
- [x] Implementation checklist created

---

## Routing Infrastructure Summary

### Rule Store
- **File:** `~/.tokenpak/routes.yaml`
- **Current state:** Empty (`[]`) — no rules yet
- **Format:** YAML list of rule objects

### Route Registration Methods
Two ways to add routes:

**Method A — CLI (preferred, validates input):**
```bash
tokenpak route add \
  --model "*sonnet*" \
  --prefix "Read HEARTBEAT.md" \
  --target tokenpak-anthropic/claude-haiku-4-5 \
  --priority 10 \
  --description "Route heartbeats to haiku"
```

**Method B — Direct YAML edit (`~/.tokenpak/routes.yaml`):**
```yaml
- id: <uuid>
  pattern:
    model: "*sonnet*"        # glob match on model name
    prefix: "Read HEARTBEAT.md"   # prompt must START with this
    min_tokens: 8000         # token floor (AND logic with other fields)
    max_tokens: 50000        # token ceiling
  target: tokenpak-anthropic/claude-haiku-4-5
  priority: 10               # lower = higher priority
  enabled: true
  created_at: "2026-03-09T00:00:00+00:00"
  description: "..."
```

**Key engine files:**
- `~/Projects/tokenpak/tokenpak/routing/rules.py` — `RouteStore`, `RouteEngine`, `DEFAULT_ROUTES_PATH`
- `~/Projects/tokenpak/tokenpak/agent/proxy/router.py` — provider routing (Anthropic/OpenAI/Google), `MODEL_COSTS`
- `~/Projects/tokenpak/tokenpak/agent/cli/commands/route.py` — CLI commands

### Model Aliases Confirmed Available
All target models exist in `MODEL_COSTS` and are registered in the proxy:

| Model | Cost (input/output per MTok) | Confirmed |
|-------|------------------------------|-----------|
| `claude-haiku-4-5` | $0.80 / $4.00 | ✅ |
| `claude-haiku-4-6` | not in cost table yet | ⚠️ check |
| `claude-sonnet-4-5` | $3.00 / $15.00 | ✅ |
| `claude-sonnet-4-6` | $3.00 / $15.00 | ✅ |
| `claude-opus-4-5` | $15.00 / $75.00 | ✅ |
| `claude-opus-4-6` | $15.00 / $75.00 | ✅ |

OpenClaw model alias format for targets: `tokenpak-anthropic/claude-haiku-4-5`

---

## Implementation Tasks (Execute on 3/9 PM after Kevin approval)

### Task 1: Heartbeat → Haiku Route
```bash
tokenpak route add \
  --prefix "Read HEARTBEAT.md" \
  --target "tokenpak-anthropic/claude-haiku-4-5" \
  --priority 10 \
  --description "Route heartbeat polls to haiku (79% cache hit, 1773ms latency)"
```
> **Note from Cali's analysis:** Also check if openclaw.json already routes Sue's cron sessions to haiku. Current check shows no dedicated heartbeat model override — routing rule needed.

### Task 2: Long-Context → Sonnet-4-5 Route
```bash
tokenpak route add \
  --min-tokens 8000 \
  --model "*sonnet-4-6*" \
  --target "tokenpak-anthropic/claude-sonnet-4-5" \
  --priority 20 \
  --description "Route long-context (8k+ tokens) to sonnet-4-5 (43.8% vs 13.1% compression)"
```

### Task 3: Reduce Opus Usage (Process change, not a routing rule)
Add guidance to `AGENTS.md` / `SOUL.md`:
- Opus reserved for: architecture planning, complex multi-file reasoning, explicit deep analysis
- Do NOT use opus for: heartbeats, standard coding, long-context compression

### Task 4: Rate Limit Backoff Tuning
File: `~/Projects/tokenpak/tokenpak/handlers/rate_limit.py`

Current params → Proposed:
```python
# Find RateLimitBackoffSync defaults and change:
max_retries: int = 4  →  6
base_wait: float = 1.0  →  2.0
max_wait: float = 60.0  →  120.0
```

---

## Validation (After each route is added)

- [ ] All 4 changes applied
- [ ] Heartbeat route: `tokenpak route test --model "claude-sonnet-4-6" --prompt "Read HEARTBEAT.md if it exists"`  → should match rule, target haiku
- [ ] Long-context route: `tokenpak route test --model "claude-sonnet-4-6" --tokens 10000` → should match, target sonnet-4-5
- [ ] Rate limit: confirm handler params updated, restart proxy
- [ ] Telemetry: monitor.db collecting new sessions

```bash
# Quick validation commands:
tokenpak route list
tokenpak route test --model "claude-sonnet-4-6" --prompt "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly."
tokenpak route test --model "claude-sonnet-4-6" --tokens 10000
```

---

## Post-Deployment Monitoring

- [ ] Monitor error logs 2h post-deployment
- [ ] Check rate limit failure rate (target: <20% vs current 58.4%)
- [ ] Verify cache hit rate maintained or improved
- [ ] Verify compression ratio maintained or improved
- [ ] Re-run telemetry report in 7 days

---

## Expected Impact (From Cali's Analysis)

| Change | Monthly Savings |
|--------|----------------|
| Heartbeat → haiku | ~$28/month |
| Long-context → sonnet-4-5 | ~$10/month |
| Backoff tuning | ~$15/month |
| Opus discipline | ~$80/month |
| **Total potential** | **~$133/month** |

Current proven savings: ~$80/month → With optimizations: **~$213/month**

---

## Timeline

| When | What |
|------|------|
| ✅ 3/7 PM | Prep complete (this checklist) |
| 3/8 PM | Cali's routing plan delivered (already done) |
| 3/9 8 AM | Kevin reviews + approves |
| 3/9 12 PM | Trix implements (est. 1-2 hours) |
| 3/9 3 PM | All routes deployed + tested |
| 3/10 | Telemetry collection begins |
| 3/10 PM | Validation + comparison report |
