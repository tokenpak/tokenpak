# NCP-1A iteration 1 — 1v1 baseline + concurrency priority shift

**Date**: 2026-04-27
**Status**: 🟡 **superseded by iteration-2** — see banner below
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Standard proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - Diagnostic plan: `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`
  - Primary protocol: `docs/internal/specs/ncp-1r-oauth-parity-protocol-2026-04-26.md`
  - **Iteration 2 (2-TP-concurrent degraded)**: `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md`
  - **NCP-3 diagnostic plan**: `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`

> ⚠️ **Iter-2 update (2026-04-27)**: A=parity, **B=degraded** (operator-confirmed); C/D pending. The investigation has narrowed enough to proceed to NCP-3 diagnostic without waiting for C/D. See iteration-2 doc for the updated evidence and NCP-3 plan for the next runbook.

> **Headline:** the originally-suspected per-request overhead hypotheses (H1 / H3) are demoted; the originally-suspected concurrency hypotheses (H2 / H4 + new shared-lane category) are promoted. Operator confirms 1 TokenPak Claude Code session beside 1 native session reaches **wall-clock parity** (~3% difference) with **no retry messages**. The product issue therefore lives in the multi-concurrent-TokenPak regime, not in single-request overhead.

---

## 1. Result captured

| Variant | Wall-clock | Retry messages | Notes |
|---|---|---|---|
| TokenPak Claude Code (1 session) | **~1m09s** | none | Same workload as variant B in §5.1 of the protocol |
| Native Claude Code (1 session) | **~1m07s** | none | Same workload, no proxy in path |
| **Delta** | **+2 s (~3%)** | **0** | Within §4.5 concurrency-parity target (≥ 0.8× — actually 0.97×) |

**Conditions** (operator-supplied, not from telemetry):
- Concurrent run, side-by-side terminals
- Same Claude Code OAuth subscription account
- Same model (Kevin's default)
- Workload identity not strictly pinned (no fixed prompt sequence checked in), but the two runs were at the same time on the same task class

**Implications:**

1. **Per-request overhead is bounded.** The 3% delta is well within the I-1 disclosure target (1.5× cap on amplification). Cache prefix disruption (H1) and token amplification (H3), even if they fire on every request, do not produce user-visible degradation at N=1 concurrency.
2. **Retry amplification (H4) is not a constant background contributor.** No retry messages on either side at N=1.
3. **The product issue is concurrency-shaped.** Whatever causes earlier rate-limit / retry behavior in the user's normal usage is **multiple concurrent TokenPak sessions**, not single-request overhead.

---

## 2. Updated hypothesis priority

Kevin's directive 2026-04-27 promotes concurrency hypotheses and demotes overhead hypotheses. The NCP-0 diagnostic plan §2 hypothesis matrix is updated as follows:

### 2.1 New ranking

| Rank | Hypothesis | Updated impact | Rationale (post-1v1) |
|---:|---|---|---|
| **1** | **H2** session/session-id/lane collapse | **HIGH** | Already HIGH in NCP-0; the 1v1 parity result strengthens this — at N=1 the collapse doesn't matter (one CLI ⇄ one session-id), but at N>1 the proxy collapses N CLIs onto one session-id, concentrating quota debt. |
| **2** | **H4** retry amplification under concurrent TokenPak sessions | **HIGH** *(promoted from MEDIUM)* | At N=1 no retries observed; the originally-feared "proxy retries on the same 429 the CLI is also retrying" cannot fire when there's no 429. Under concurrency, 429 likelihood rises *because* of H2 (session-id collapse), and *then* retry amplification compounds. Promoted because the failure mode is conditional on concurrency. |
| **3** | **H9** shared-lane contention *(new — was bundled into H6 in NCP-0)* | **HIGH** *(new category)* | Three sub-mechanisms TokenPak has that native CLI doesn't: (a) the ConnectionPool single `_lock` per netloc (H6 in NCP-0), (b) potential serialization through `IntentPatchStore._LOCK` / `Monitor` queue, (c) **shared OAuth refresh lane** — when N CLIs share one proxy-side session-id, OAuth token refresh on expiry is a single shared event with N concurrent waiters. Native CLI: each invocation has its own credential read with no shared refresh path. |
| **4** | **H1** cache prefix disruption | **MEDIUM** *(demoted from HIGH)* | Still real (vault-injection content varies per request), but at N=1 the wall-clock impact is bounded (~3%). Could matter under concurrency if cache-miss compounds with H2 / H9, but it's a **secondary** effect. |
| **5** | **H3** token amplification | **MEDIUM** *(unchanged from NCP-0)* | Same logic as H1 — additive, not multiplicative; doesn't explain concurrency-shaped failure. |
| 6 | H5 failover storm | LOW | Unchanged. |
| 7 | H6 connection pool lock | superseded by H9 | The pool lock is one of several shared-lane mechanisms now bundled in H9. |
| 8 | H7 SQLite write contention | LOW | Unchanged. |
| 9 | H8 companion-side model calls | RULED OUT | Unchanged — confirmed by NCP-0 code inspection; no extra upstream calls. |

### 2.2 H9 — shared-lane contention (new category)

H9 is the most important new hypothesis surfaced by the 1v1 result. Sub-mechanisms:

| Sub-hypothesis | Surface | Native CLI equivalent? | Why it matters under concurrency |
|---|---|---|---|
| **H9a** Connection pool lock | `tokenpak/proxy/connection_pool.py` — `threading.Lock` around `_clients` dict, default `max_connections=20` per netloc | None — native CLI uses its own per-process httpx client | Multiple proxy threads acquire the same lock on every `_get_client()` call. Bounded but contended. |
| **H9b** OAuth refresh lane | `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider._load` — reads `~/.claude/.credentials.json` per request, may refresh on expiry | Native CLI has its own per-process credential lifecycle | When the access token expires, ALL concurrent requests through the proxy contend on the same refresh path. Native CLI has no shared refresh — each invocation has its own credential cache. |
| **H9c** Session-id rotation lock | `tokenpak/services/routing_service/credential_injector.py:_get_proxy_session_id` — module-level UUID + lock | Native CLI rotates per-invocation, no shared lock | Currently the lock just protects one-time UUID generation; if NCP-3 introduces rotation, this becomes a hot path. |
| **H9d** Telemetry write lane | `Monitor` queue (max 1000) + `IntentPatchStore._LOCK` + `tp_events` writes | Native CLI has no proxy telemetry | Off-path by design (queued + drained), but if the queue saturates under concurrency, request latency could spike. |

**The dominant H9 sub-hypothesis is most likely H9b (OAuth refresh lane)** — when N concurrent CLIs share one proxy-side session-id (H2) and the OAuth token expires mid-session, the proxy serializes refresh while every concurrent request waits. That's the classic "thundering herd" pattern and matches the user-visible symptom (TokenPak hits retry/rate-limit behavior earlier under concurrency).

---

## 3. Next operator tests (A / B / C / D)

Designed to isolate H2, H4, and H9 from H1/H3 and from each other. **No code changes between runs** — same TokenPak version, same workload, same OAuth account.

| Test | Variant | N | Stagger | Goal |
|---|---|---:|---|---|
| **A** | TokenPak only | 2 | none (start together) | Lowest-N concurrency for TP; checks whether 2 TP sessions still parity-match or start showing degradation. Settles whether the issue requires N≥3. |
| **B** | TokenPak only | 3 | none | Bracket the threshold from above. If A is fine and B degrades, the threshold is between 2 and 3. |
| **C** | Native only | 2 | none | Control for "is it an Anthropic-side concurrency limit, not a TokenPak limit?" If 2 native sessions also degrade, the issue isn't TokenPak-specific. |
| **D** | TokenPak only | 3 | 20 s between starts | Tests H9b (shared OAuth refresh lane) hypothesis — if degradation requires near-simultaneous starts, refresh thundering-herd is implicated; if staggered also degrades, H2 (session-id collapse) is the dominant factor regardless of timing. |

### 3.1 Per-test capture protocol

For each of A / B / C / D, the operator runs the workload and records, **per session**:

1. Start wall-clock time.
2. End wall-clock time (or first disruption marker — retry message / rate-limit error / failed turn).
3. Number of successful turns.
4. Number of failed turns / retry messages.
5. Verbatim error text from the TUI on any failure.
6. Whether all sessions completed or any died early.

For TokenPak-side runs (A, B, D), additionally:

```bash
# After all sessions exit, capture the TokenPak baseline.
scripts/capture_parity_baseline.py \
    --label tokenpak \
    --window-days 1 \
    --note "iteration-2-test-<A|B|D>, N=<2|3>, stagger=<none|20s>" \
    --output tests/baselines/ncp-1a-iter-2/tokenpak-<A|B|D>-$(date -u +%Y%m%dT%H%M%SZ).json
```

For native-side run (C), the operator manually fills in observed counts (request count, error count, latency notes) into an empty native template — same approach as protocol §6.

### 3.2 What to look for

| Result pattern | Most likely cause | Recommended NCP phase |
|---|---|---|
| A=parity, B=parity, C=parity, D=parity | Issue does not reproduce in this run; need higher N or longer duration. | NCP-1A iter-3 with N=5 or 30-min duration. |
| **A=parity, B=degrades, C=parity, D=degrades** | **H2 dominant** — concurrency-collapse hits at N≥3, stagger doesn't help (same session-id). | **NCP-3 (session-id rotation)**. |
| **A=parity, B=degrades, C=parity, D=parity** | **H9b dominant** — staggered starts let OAuth refresh complete between them; thundering herd is the cause. | **NCP-4 (concurrency-aware refresh lane)** — could be a small per-CLI credential cache scoped to invocation. |
| A=parity, B=degrades, **C=degrades**, D=anything | Anthropic-side rate limit, not TokenPak-specific. | Re-evaluate; may not be a TokenPak parity issue at all. |
| A=parity, B=parity, **D=degrades** | Strange; suggests stagger introduces something (e.g. mid-flight token refresh). H9b probable. | NCP-4 OAuth-refresh investigation. |
| A=degrades | Issue reproduces at N=2; H2/H9 both candidates. | Run D variant with N=2 stagger to disambiguate. |

### 3.3 Operator quick-start

```bash
# Set up a results directory.
mkdir -p tests/baselines/ncp-1a-iter-2

# Test A — 2 TokenPak sessions.
# Open 2 terminals; in each:
tokenpak claude
# Run the same workload simultaneously. Note start time.
# When both finish (or first one disrupts), note end time + any error text.

# Capture the TokenPak baseline once both finish.
scripts/capture_parity_baseline.py \
    --label tokenpak --window-days 1 \
    --note "iter-2-test-A, N=2, no stagger" \
    --output tests/baselines/ncp-1a-iter-2/tokenpak-A-$(date -u +%Y%m%dT%H%M%SZ).json

# (Repeat for tests B / C / D with adjusted N and stagger.)
```

For test C (native-only), do NOT use TokenPak telemetry. Hand-fill the empty template:

```bash
scripts/capture_parity_baseline.py \
    --label native --window-days 1 \
    --note "iter-2-test-C, N=2, native only, hand-filled" \
    --output tests/baselines/ncp-1a-iter-2/native-C-$(date -u +%Y%m%dT%H%M%SZ).json
# Then edit the JSON to fill in observed counts/timings per protocol §6.
```

---

## 4. What's still on hold

Per Kevin's directive: **no fixes implemented.** Specifically frozen until A/B/C/D results land:

- ❌ Retry behavior (failover engine, RateLimitBackoff) unchanged
- ❌ Cache placement unchanged
- ❌ Session-id behavior (proxy-process-stable UUID) unchanged
- ❌ Credential injection unchanged
- ❌ Routing unchanged
- ❌ Proxy connection pool / lock granularity unchanged
- ❌ Failover engine unchanged
- ❌ Companion prompt mutation (vault, capsule, intent guidance) unchanged
- ❌ Provider-backlog work paused

After A/B/C/D, the synthesis decision per the §3.2 table determines:
- **NCP-3** if the dominant cause is session-id collapse (H2)
- **NCP-4** if the dominant cause is shared-lane / refresh-thundering-herd (H9b)
- **NCP-1A iter-3** with higher N if A/B/C/D all stay parity
- **NCP-1B instrumentation expansion** if the picture stays inconclusive

---

## 5. Cross-references

- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — Standard #24 (NCP-1R revision), invariants I-0 / I-3 / I-6 + §4.5 concurrency parity target
- `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md` — NCP-0 hypothesis matrix (H1–H8); this iteration adds H9 + reranks
- `docs/internal/specs/ncp-1r-oauth-parity-protocol-2026-04-26.md` — primary OAuth/subscription protocol the operator runs
- `tokenpak/proxy/connection_pool.py` — H9a surface
- `tokenpak/services/routing_service/credential_injector.py` — H9b + H9c surface
- `tokenpak/proxy/intent_prompt_patch_telemetry.py` + `tokenpak/proxy/monitor.py` — H9d surface

---

## 6. Status table

| Item | State |
|---|---|
| 1v1 baseline | ✅ captured (operator-supplied) — TokenPak ~1m09s, native ~1m07s, no retries |
| Hypothesis priority | ✅ updated (H2 / H4 / H9 promoted; H1 / H3 demoted) |
| New hypothesis H9 | ✅ defined (4 sub-mechanisms; H9b dominant suspect) |
| A/B/C/D test plan | ✅ specified (this doc §3) |
| A/B/C/D execution | ⏸️ pending operator |
| NCP-3 vs NCP-4 decision | ⏸️ pending A/B/C/D results |
| NCP-2 (cache prefix fix) | 🔵 deprioritized — H1 is now secondary |
| Code changes | ⛔ frozen per directive |

After A/B/C/D land, I'll synthesize a follow-up doc (`ncp-1a-iteration-2-<DATE>.md`) and the §3.2 table determines the next implementation phase.
