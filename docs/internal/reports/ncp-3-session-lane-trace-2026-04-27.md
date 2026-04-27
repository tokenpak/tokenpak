# NCP-3 — Session-Lane Preservation diagnostic plan + initial trace

**Date**: 2026-04-27
**Status**: 🟡 **diagnostic-only** — measurement / instrumentation plan; no behavior fixes proposed
**Workstream**: NCP (Native Client Concurrency Parity) → NCP-3 (Session-Lane Preservation)
**Authors**: Sue (diagnostic) / Kevin (review + scope)
**Companion docs**:
  - Standard proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - NCP-1A iteration 2 (operator evidence): `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md`
  - Iteration 1 (1v1 baseline + ABCD plan): `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md`

> **Goal:** characterize **whether multiple concurrent `tokenpak claude` sessions share, collapse, serialize, retry-amplify, or contend through a single lane** — and identify which specific TokenPak-side mechanism is the load-bearing cause. This is a **diagnostic/instrumentation phase**. Behavior fixes are explicitly **out of scope** until separately approved (per the directive).

> **What's already known** (post-iter-2): single TP session beside single native is parity; 2 TP sessions concurrently degrade with a TP-side "Retrying in 20s" message visible. The fault lives in the TokenPak shared-lane behavior. NCP-3 narrows which lane.

> ⚠️ **Iter-3 strengthening (2026-04-27)**: 2 concurrent TP sessions retried while 1 native session ran healthily *at the same time*. Generic account quota ruled out as sole cause. See `docs/internal/specs/ncp-1a-iteration-3-2026-04-27.md`. The harness output Kevin runs against this NCP-3 plan §5 routes through the §6 decision tree as documented; iter-3 sharpens the priority ranking but does not change the decision tree itself.

---

## 0. Reading guide

| § | Question |
|---|---|
| 1 | What concrete TokenPak-side surfaces could collapse / serialize / contend? |
| 2 | Which fields are already observable in existing telemetry? |
| 3 | Which fields require new instrumentation? Where? |
| 4 | The trace methodology (what the operator runs to capture lane behavior) |
| 5 | Initial inspection harness (read-only) — what runs today against existing telemetry |
| 6 | Synthesis decision tree (how to interpret the trace) |
| 7 | Out of scope for NCP-3 diagnostic |
| 8 | Recommended next phase routing |
| 9 | Cross-references |

---

## 1. TokenPak surfaces that could collapse, serialize, or contend

Concrete file-level inventory of every "could-be-shared lane" between concurrent `tokenpak claude` sessions hitting the same proxy process. Each row is a candidate for the lane that explains the iter-2 degradation.

### 1.1 Authentication / session-identity lanes

| # | Surface | File:line | Native CLI equivalent | Concurrency-collapse risk |
|---|---|---|---|---|
| 1 | **Proxy-synthesized session-id** | `tokenpak/services/routing_service/credential_injector.py::_get_proxy_session_id` (~232–244) | Each native CLI invocation rotates its own `X-Claude-Code-Session-Id` | **HIGH** — module-level UUID, one per proxy process. N concurrent CLIs → 1 wire-side session-id. **This is the H2 / iter-1 §2.2 H9c surface.** |
| 2 | **OAuth credential read path** | `ClaudeCodeCredentialProvider._load` reads `~/.claude/.credentials.json` | Each native CLI maintains its own credential cache | **HIGH (suspected H9b)** — when access token approaches expiry, refresh is a SHARED event; N concurrent requests through the proxy may serialize on it (thundering herd). The native CLI has no equivalent shared waiter pool. |
| 3 | **Caller-supplied credential lookup** | `tokenpak/proxy/passthrough.py` | N/A | LOW — passthrough is stateless per-request; not a shared lane. |

### 1.2 Network / dispatch lanes

| # | Surface | File:line | Native CLI equivalent | Concurrency-collapse risk |
|---|---|---|---|---|
| 4 | **HTTP connection pool** | `tokenpak/proxy/connection_pool.py:178–228` — single `threading.Lock` over `_clients` dict | Each native CLI has its own httpx client | **MEDIUM (H9a)** — lock acquired on every `_get_client()` call. Default `max_connections=20` per netloc. Bounded but contended at high concurrency. |
| 5 | **Async server semaphore** | `tokenpak/proxy/server_async.py` — `asyncio.Semaphore(max_concurrency)` | N/A | LOW — only present on the async path; threaded path (default for many deployments) doesn't use it. |
| 6 | **Failover engine retry** | `tokenpak/proxy/failover_engine.py:8,42,98–100` — `RATE_LIMIT_WAIT_SECONDS = 2.0`, `MAX_RETRY_SAME_PROVIDER = 1` | Each native CLI handles its own retry | **HIGH (H4)** — when a 429 fires, the engine retries on the same provider after a fixed 2 s. If the user is *also* configured to retry on the CLI side, retries multiply. Combined with H2 (one session-id), 429s fire faster AND the proxy retries them. |

### 1.3 Storage / write lanes

| # | Surface | File:line | Native CLI equivalent | Concurrency-collapse risk |
|---|---|---|---|---|
| 7 | **Monitor SQLite write queue** | `tokenpak/proxy/monitor.py:42–64` — `Queue(maxsize=1000)` + bg thread + `_DB_LOCK` + `_DB_QUEUE_LOCK` | N/A (no telemetry on native) | **LOW (H9d.1)** — off-path background drainer; bounded queue. Could backpressure if saturated, but bounded by N requests / second × small-write-cost. |
| 8 | **IntentPatchStore lock** | `tokenpak/proxy/intent_prompt_patch_telemetry.py:101,132–162,193–212,219–243` — process-wide `_LOCK` | N/A | **LOW (H9d.2)** — PI-3 just landed; in production today the write rate is zero unless prompt_intervention is enabled. |
| 9 | **Companion journal SQLite** | `tokenpak/companion/journal/` — direct `sqlite3.connect` per write | N/A | **LOW (H9d.3)** — best-effort writes; unlikely to be a hot lane. |

### 1.4 Companion-side lanes

| # | Surface | File:line | Native CLI equivalent | Concurrency-collapse risk |
|---|---|---|---|---|
| 10 | **Companion pre-send hook** | `tokenpak/companion/hooks/pre_send.py:217–254,196–214,471–481` | N/A | LOW — runs in the CLI's own process per invocation; not shared across sessions. |
| 11 | **Vault BlockStore** | `tokenpak/companion/hooks/pre_send.py:232–254` — `BlockStore.search(prompt, top_k=5)` | N/A | LOW — local read; per-invocation scope. |

---

## 2. What's already observable (existing telemetry)

The existing `~/.tokenpak/telemetry.db` schema already captures roughly half of what NCP-3 needs:

| Field requested by directive | Source available today | How |
|---|---|---|
| `tokenpak_session_id` (proxy-synthesized) | `tp_events.session_id` | Direct column |
| `claude_code_session_id` (CLI-side) | ❌ not captured | The CLI synthesizes its own and the proxy may overwrite. Would need new logging. |
| `process_id` | ❌ not captured | Each proxy worker thread serves multiple requests; no per-request PID column today |
| `parent_process_id` | ❌ not captured | Same |
| `request_id` | `tp_events.request_id` | Direct column |
| `lane_id` | ❌ not captured | Lanes (pool client, queue position, etc.) aren't named today |
| `credential_provider` | `tp_events.provider` | E.g. `tokenpak-claude-code` |
| `auth_plane` | derived from `provider` | OAuth/subscription if `provider='tokenpak-claude-code'` |
| `credential_class` | derived from `provider` | Same |
| `provider` label | `tp_events.provider` | Direct |
| `request_start_at` | `tp_events.ts` | Direct |
| `request_end_at` | derivable: `tp_events.ts + duration_ms` | Indirect |
| `upstream_start_at` | ❌ not captured | Would need a new column or a wider span/event scheme |
| `upstream_end_at` | ❌ not captured | Same |
| `retry_count` | `tp_events.error_class='retry'` count | Lower bound only — current schema doesn't tag every retry |
| `retry_after_seen` | ❌ not captured (per H4) | New |
| `retry_owner` (CLI vs proxy) | ❌ not captured | Would need a label on the retry event |
| `queue_enter_at` / `queue_exit_at` | ❌ not captured | Would need new instrumentation in `connection_pool` and `Monitor` queue |
| `lock_wait_ms` | ❌ not captured | New |
| `sqlite_write_ms` | ❌ not captured | New |

**About half the directive's fields can settle the H2 / H4 questions from existing telemetry alone.** The remaining fields are needed to discriminate H9 sub-mechanisms (H9a vs H9b vs H9c vs H9d) — that's a follow-up instrumentation phase if needed.

---

## 3. New instrumentation — design only, NOT implemented in this PR

**Scope decision (this PR)**: NCP-3 ships the diagnostic *plan* + a read-only inspection harness over **existing telemetry only**. New in-proxy instrumentation is deferred to a follow-up `NCP-3I` PR if the H2 / H4 surfaces alone prove insufficient. This keeps NCP-3 strictly within the directive's "no production behavior changes" rule.

When `NCP-3I` is approved (separate ratification), the additions would be:

### 3.1 Proposed `tp_events` columns (additive ALTER TABLE; same pattern as PI-3)

| Column | Purpose |
|---|---|
| `process_id` | Which proxy worker handled this request |
| `parent_process_id` | The CLI invocation's PID, if forwarded by `tokenpak claude` launcher |
| `lane_id` | Generated label for the (pool-client + queue-position) tuple |
| `credential_class` | OAuth-subscription / api-key / cloud-provider |
| `auth_plane` | Same as Standard #24 §1.5 |
| `upstream_start_ms` | `time.monotonic()` at the moment of `httpx.send` |
| `upstream_end_ms` | Same at response complete |
| `retry_after_seconds` | Parsed from upstream `Retry-After` header on 429 |
| `retry_owner` | `cli` / `proxy_failover` / `proxy_other` |
| `queue_enter_ms` / `queue_exit_ms` | For any queued lane (Monitor queue, pool wait queue) |
| `lock_wait_ms` | Aggregate time spent waiting on shared locks (pool + IntentPatchStore + DB) |
| `sqlite_write_ms` | Time the request spent waiting for a SQLite write |
| `oauth_refresh_owner` | Which trace_id triggered the credential refresh (NULL if no refresh in this request's lifetime) |
| `oauth_refresh_wait_ms` | Time this request waited for a concurrent refresh to complete |

### 3.2 Where the writes would live (when NCP-3I lands)

- `tokenpak/proxy/server.py` — request handler stamps the per-request fields
- `tokenpak/proxy/connection_pool.py` — wraps `_get_client` lock acquire with `lock_wait_ms` measurement
- `tokenpak/services/routing_service/credential_injector.py` — wraps OAuth refresh path with `oauth_refresh_owner` + `oauth_refresh_wait_ms`
- `tokenpak/proxy/failover_engine.py` — labels retry events with `retry_owner='proxy_failover'`

All writes are **off-path** (`try/except: pass`), additive to existing schema, and gated behind a `TOKENPAK_PARITY_TRACE_ENABLED` env-var (default `false`) so they introduce zero behavior change in production until the operator opts in.

---

## 4. Trace methodology (what the operator runs)

### 4.1 Pre-trace setup

1. Verify the iter-2 protocol fix is in effect: `tokenpak claude` launcher used (not `claude` with `ANTHROPIC_BASE_URL` override). See NCP-1R protocol §4.1.
2. Capture TokenPak version: `tokenpak --version`
3. Capture Claude CLI version: `claude --version`
4. Verify OAuth/subscription auth plane: per NCP-1R §2.1.

### 4.2 Workload (matches iter-2 test B)

```bash
# Open 2 terminals.
# Terminal 1:
tokenpak claude
# Run a representative prompt sequence (matched between sessions).

# Terminal 2 (start within 1–2s of terminal 1):
tokenpak claude
# Run the same prompt sequence.

# Wait for both sessions to exit OR for one to surface a retry/error.
# Note wall-clock start + end + any error text per session.
```

### 4.3 Capture

```bash
# After both sessions complete, inspect the lane behavior.
scripts/inspect_session_lanes.py \
    --window-minutes 30 \
    --output tests/baselines/ncp-3-trace/$(date -u +%Y%m%dT%H%M%SZ).md
```

The harness reads existing `tp_events` + `tp_usage` and produces a markdown report covering §6 of this doc.

### 4.4 Test variations the operator should consider

| Variant | Goal |
|---|---|
| **Test B repeat** (2 TP, no stagger) | Reproduce iter-2 finding under instrumentation |
| **Test D** (3 TP, 20 s stagger) | If parity → H9b (OAuth refresh herd); if degraded → H2 still dominant |
| **Test C** (2 native, no TP) | Anthropic-side concurrency limit control |
| **OAuth-fresh test** | Force a fresh OAuth login (`rm ~/.claude/.credentials.json && claude` to re-login) before the test, so the access token has its full TTL. If degradation persists on a fresh token, OAuth refresh is NOT the cause. |

---

## 5. Read-only inspection harness (`scripts/inspect_session_lanes.py`)

This script ships with NCP-3 (this PR). It is **purely read-only** over existing telemetry — no new tables, no new columns, no behavior changes. The script:

1. Reads `tp_events` + `tp_usage` for the requested time window.
2. Filters to Claude Code traffic (`provider~claude-code`).
3. Reports on the eight diagnostic dimensions:
   1. Distinct `session_id` count vs distinct `request_id` count (the H2 ratio)
   2. Time-clustering — are concurrent requests starting within a small window or serialized?
   3. Status distribution (200 / 429 / 5xx / other)
   4. Duration percentiles per session_id (does one session_id's requests have higher tail latency than another?)
   5. Provider-slug audit (any `anthropic` rows = I-0 violation)
   6. Retry-event count (lower bound — `error_class='retry'`)
   7. Token-usage averages (cache hit ratio, input-token mean) for sanity vs iter-1
   8. Cross-session interleaving — did request N from session X land between N-1 and N+1 of session Y, or did the proxy serialize them?

Output: markdown report (default) or JSON (`--json`). Exits non-zero only on hard errors (no telemetry.db, unreadable schema). Verdict-style "supported / not supported" lines for the H2 hypothesis.

---

## 6. Synthesis decision tree

After running the §4 trace, the operator inspects the §5 harness output and answers six questions. Each set of answers routes to one of the candidate next phases.

```
Q1: Did 2 concurrent TP sessions share the same wire-side session_id? (per harness §5.1)
    YES  → H2 confirmed at lane level → continue to Q2
    NO   → unexpected; rerun with confirmed iter-2 conditions

Q2: Did test D (3 TP staggered 20s) show parity?
    YES  → H9b (OAuth refresh lane) is the dominant H9 sub-mechanism
        → recommend NCP-9 (refresh lane fix; per-CLI credential cache scoped to invocation)
    NO   → H2 (session collapse) is dominant regardless of timing
        → recommend NCP-3A (session-id rotation per CLI invocation)
    PENDING → record as "needs D test"; recommend running test D before deciding

Q3: Did the harness report retry_count > 0 on the TP side while native showed none?
    YES  → H4 (retry amplification) is corroborated; goes alongside whichever Q2 answer
        → recommend folding NCP-4 (retry parity fix) into the Q2-chosen phase

Q4: Did any tp_events row show provider='anthropic' (NOT 'tokenpak-claude-code')?
    YES  → I-0 violation; the run is invalid; rerun with launcher fix per NCP-1R §4.1
    NO   → continue

Q5: Did p99 duration on the TP side exceed p99 on the (parity-baseline) native side by > 1.5×?
    YES  → consistent with shared-lane wait; supports H9
    NO   → cause is more "single point of failure" than "shared queue"

Q6: Was the harness output ambiguous on H2 (Q1 = NO or session_id distribution unclear)?
    YES  → recommend NCP-3I (in-proxy instrumentation) to add the §3.1 columns
    NO   → harness output suffices; pick from Q2/Q3 outcomes
```

### 6.1 Routing to next NCP phase

Each branch maps to one of:

| Branch | Recommended next phase |
|---|---|
| Q2=YES (D parity), Q3=YES | **NCP-9** OAuth refresh lane fix (and NCP-4 retry parity in the same arc) |
| Q2=NO (D degrades), Q3=YES | **NCP-3A** session-id rotation (and NCP-4 retry parity in the same arc) |
| Q2=PENDING, Q5=YES | **NCP-1C** — rerun iter-2 with test D + on-token-refresh observation; do not implement yet |
| Q4=YES | **bug-fix only** — re-run with launcher correction |
| Q6=YES | **NCP-3I** — in-proxy instrumentation phase per §3 |
| All Q ambiguous | **NCP-1C** — more operator data |

---

## 7. Out of scope for NCP-3 diagnostic

Per the directive, this phase explicitly does NOT change:

- ❌ Routing behavior
- ❌ Retry behavior (failover engine constants unchanged)
- ❌ Cache placement
- ❌ Prompt mutation (vault, capsule, intent guidance)
- ❌ Provider / model selection
- ❌ Auth behavior (credential injection, OAuth refresh logic)
- ❌ Production behavior — `NCP-3I` instrumentation is gated behind opt-in env-var if/when ratified

NCP-3 is **a diagnostic plan + a read-only inspection harness over existing telemetry.** No code path that alters proxy / companion behavior is touched.

---

## 8. Recommended next phase routing (after first trace lands)

Once the operator runs §4 and the harness produces a report, route per §6:

| Outcome | Next phase | Type |
|---|---|---|
| H2 dominant (Q1=YES, Q2=NO) | **NCP-3A** session-id / lane preservation | implementation |
| H9b dominant (Q1=YES, Q2=YES) | **NCP-9** OAuth refresh lane | implementation |
| H4 corroborated alongside | fold **NCP-4** retry parity into the chosen arc | implementation |
| Inconclusive even after harness | **NCP-1C** — more operator data | docs / operator |
| Need lane-wait timings to discriminate | **NCP-3I** — in-proxy instrumentation | code (measurement-only) |

Each recommended phase requires its own ratification cycle. NCP-3 does NOT pre-authorize any of them.

---

## 9. Cross-references

- `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md` — 1v1 baseline + A/B/C/D plan
- `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md` — operator evidence triggering this NCP-3 diagnostic
- `docs/internal/specs/ncp-1r-oauth-parity-protocol-2026-04-26.md` — the OAuth/subscription parity protocol
- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — Standard #24 + §4.5 concurrency parity target
- `scripts/inspect_session_lanes.py` — read-only harness (this PR)
- `scripts/capture_parity_baseline.py` / `scripts/diff_parity_baselines.py` — NCP-1 baselines
- `tokenpak/services/routing_service/credential_injector.py` — H2 / H9b / H9c surface
- `tokenpak/proxy/connection_pool.py` — H9a surface
- `tokenpak/proxy/failover_engine.py` — H4 surface
- `tokenpak/proxy/intent_prompt_patch_telemetry.py` + `tokenpak/proxy/monitor.py` — H9d surface
