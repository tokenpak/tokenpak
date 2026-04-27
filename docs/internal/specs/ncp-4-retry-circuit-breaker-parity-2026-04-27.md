# NCP-4 — retry / circuit-breaker parity (spec)

**Date:** 2026-04-27
**Status:** 📝 **scoped, awaiting approval** — spec only, no code changes
**Workstream:** NCP-4 (retry / circuit-breaker parity for Claude Code)
**Authors:** Sue (scope) / Kevin (review + go-ahead)
**Tracker:** to be opened on approval (linked from #74)
**Related issues:** [tokenpak/tokenpak#74](https://github.com/tokenpak/tokenpak/issues/74) (NCP-3A-streaming-connect — Phase 1 done, Phase 2 rejected; #74 stays open as diagnostic record)
**Companion docs:**
- Phase 1 implementation: PR #80 merged 2026-04-27 21:43:05Z, commit `6b8e18f9b6`
- Phase 2 spec + regression record (M2 + 2B): `docs/internal/specs/issue-74-streaming-connect-phase-2-2026-04-27.md`
- Issue-#73 / PR #78 (`tp_parity_trace` canonicality) — wire-side completion ledger
- Standards §7.1 amendment (vault commit `8dc2d8f`) — wire-side completion canonicality clause
- Current breaker implementation: `tokenpak/proxy/circuit_breaker.py` (per-provider, `failure_threshold=5`, `recovery_timeout=60.0`, `window_seconds=60.0`, no error-class differentiation)

> **Goal:** determine why upstream abort surges cause `circuit_breaker_open:anthropic` cascades and user-visible Claude Code retry storms during concurrent `tokenpak claude` workloads, and propose targeted mitigations to the proxy's retry / circuit-breaker behavior. **Spec only — no implementation in this lane.**

---

## 1. Problem statement

Concurrent `tokenpak claude` sessions still surface visible TUI retries even after #74 Phase 1 (observability) and #78 (canonicality fix). The original 32-trace `RemoteProtocolError` cohort is real, but the harm is amplified by the proxy's circuit-breaker behavior:

- A burst of upstream `RemoteProtocolError` aborts (typical: 3 simultaneous identical-hash aborts within ~20 ms — the H8 footprint where one upstream connection-level event fans out across N multiplexed streams) trips the proxy breaker after 5 failures in the rolling 60-s window.
- Once tripped, the breaker rejects the next 60 seconds of streaming traffic to `api.anthropic.com` with `circuit_breaker_open:anthropic` (a `request_rejected` event in `tp_parity_trace`).
- Claude Code's TUI sees those rejects and retries — the user-visible "Retrying in 20s" loop. This adds load and can interact with the existing fresh-connection-burst hypothesis to deepen the regression.

We need to tune proxy retry/breaker behavior so that:
- A short transient burst of pre-first-byte protocol errors does NOT trip the breaker into a 60-s freeze
- A real sustained outage still trips quickly
- The TokenPak proxy and Claude Code client do NOT compete on retry policy
- The fix is observability-friendly and does not introduce a new behavior class that #75 / Phase 2 / NCP-3A already excluded

---

## 2. Evidence (from PR #80 baselines and Phase 2 verification runs)

| Source | When | `traces_with_handler_entry` | `clean_wire_completion` (`stream_complete`) | `terminal_abort` | `upstream_protocol_error` aborts | `request_rejected` (all `circuit_breaker_open:anthropic`) | Verdict |
|---|---|---|---|---|---|---|---|
| Phase 1 baseline | 21:37Z (post-#80) | 149 | 86 | 8 | 5 | **0** | breaker did NOT trip |
| Phase 2 M2 (rejected) | 22:19Z | 83 | 45 | 12 | 12 | **13** | breaker tripped + cascade |
| Phase 2B (rejected) | 22:30Z | 101 | 63 | 12 | 12 | **13** | breaker tripped + cascade (identical to M2) |
| Issue #74 original (NCP-3I-v3) | 18:42Z | 187 | 86 (clean), 32 (`stream_abort`) | 32 | 31 | (not tracked at the time) | known cohort that motivated #74 |

**Key observations:**

1. **Phase 1 had 5 `upstream_protocol_error` aborts and 0 breaker trips.** The breaker's `failure_threshold=5` means a burst of 5 in the 60-s window would trip; this run sat AT the threshold and the breaker stayed CLOSED — the trip is sensitive to a single additional failure. The breaker is therefore *just barely below* the cliff in normal operation.
2. **M2 and 2B both produced exactly 13 cascading rejects.** The numerics are deterministic-looking: 12 failures triggers the trip on failure #5; the remaining 7+ would-be in-flight calls plus the next ~6 invoked-during-OPEN streams hit the OPEN circuit and become `request_rejected`. 12 + 13 = 25 is consistent with the workload's true demand.
3. **`upstream_protocol_error` is the dominant abort class** — 12 of 12 in both failed runs and 5 of 8 in Phase 1. The breaker treats all failure classes equally, so protocol errors are weighted 1:1 with HTTP 5xx, timeout, etc., even though their semantics are very different (transient vs sustained).
4. **M6 (limited retry-once at the proxy) is parked** — it would amplify under H8 (fresh-connection bursts harm the upstream). The breaker is therefore the only proxy-side knob available without a transport change.

---

## 3. Candidate root causes

| # | Hypothesis | Evidence supporting | Evidence against / open |
|---|---|---|---|
| 1 | **Breaker trips too aggressively on transient pre-first-byte protocol bursts** | Phase 1 sat at threshold (5/5); M2/2B exceeded slightly (12) and tripped the cascade. Real Anthropic outages (sustained) would produce many more, so the threshold is sensitive in the wrong region. | Without before-and-after on a real outage, can't fully confirm threshold is *too* low — but the cliff at 5 is empirically problematic. |
| 2 | **Breaker classifies provider/transport errors too broadly** — `RemoteProtocolError` (transient connection-level), `ReadTimeout` (medium-term), 5xx (sustained), 429 (rate limit) all weighted 1:1 | `record_failure(provider)` is called from a single `except Exception` block in `_proxy_to_inner` with no class differentiation. The breaker config has only `failure_threshold` (a single number) — no per-class thresholds. | Per-class differentiation adds complexity; need to confirm the right buckets. |
| 3 | **Breaker threshold is global per-provider instead of per-session/per-lane** — one user with one bad session can degrade all concurrent users | A single Cali workload's burst trips the breaker; subsequent Trix / Sue / Kevin invocations to the same provider get `circuit_breaker_open:anthropic` even if their request is healthy | Per-session may be hard to define (session_id is unstable in some cases); per-lane (HTTP/2 stream lane) requires the underlying httpx scope. |
| 4 | **60-s recovery is too slow for Claude Code interactive workloads** | After a 60-s OPEN, even a single successful probe doesn't immediately reflect to the user; Claude Code's "Retrying in 20s" loop continues and may give up. | Faster recovery may also mean faster re-trips during real outages (oscillation). |
| 5 | **TokenPak proxy + Claude Code client retry loops compound** — both retry on transport errors, multiplying load on the upstream | Claude Code TUI's retry messages are user-visible; proxy's `record_failure` increments per attempt. Probable but not directly measured. | We don't control Claude Code's retry policy; needs to be inferred from TUI logs / behavior. |
| 6 | **Aborts before first byte poison unrelated concurrent sessions** — a `before_headers` abort in stream A causes the breaker to fast-fail stream B even though B was healthy | Per-provider breaker scope = single provider counter for ALL sessions. | Per-session isolation could fix this without touching threshold. |

**Strongest combined hypothesis:** root cause is **(1) + (2) + (3)** in combination — threshold is empirically just below the regression line (1), all error classes are weighted equally (2), and the per-provider scope means one bursty stream poisons everything (3).

---

## 4. Candidate mitigations

| # | Mitigation | What it changes | Tradeoff / risk |
|---|---|---|---|
| **B1** | **Per-error-class thresholds** — separate counters and thresholds for `RemoteProtocolError` / `LocalProtocolError` (transient transport), HTTP 5xx (sustained), HTTP 429 (rate limit), timeouts | One `CircuitBreakerConfig` field becomes a small dict (`{"protocol_error": 10, "http_5xx": 3, "http_429": 5, "timeout": 5}`); `record_failure(class_name)` increments the matching counter. | Bigger config surface; need to decide thresholds per class. **Recommended primary** — directly addresses (1) + (2). |
| **B2** | **Don't trip on pre-first-byte protocol errors unless repeated within a tighter window** — special-case `before_headers` + `after_headers_before_first_byte` aborts: counter only increments when we see N≥3 within 5 s rather than 5 within 60 s | Adds `abort_phase` awareness to the failure path (already classified by Phase 1). | Couples breaker logic to abort-phase classifier; small additional surface. Combinable with B1. |
| **B3** | **Per-session / per-lane breaker bucket** — switch from per-provider to per-(provider, session_id) or per-(provider, trace_id) | New keying in `CircuitBreakerRegistry`; each session gets its own breaker. | Higher memory footprint; cardinality limited by Claude Code session count. **Risk:** breaker cardinality explosion under per-trace_id (every trace gets its own counter). Per-session is the safer scope. |
| **B4** | **Shorter reset window for streaming OAuth path** — `recovery_timeout=10–15s` instead of 60s for the Claude Code OAuth provider, leaving 60s for non-OAuth providers | Per-provider config override (`{"anthropic": {"recovery_timeout": 10}}`); other providers unchanged. | Risk of oscillation (breaker re-trips faster). Combinable with B1. |
| **B5** | **Advisory mode for Claude Code path** — breaker observes failures and emits telemetry but does NOT fast-fail; surface upstream errors directly to the client | New `CircuitState.ADVISORY` state OR a per-provider `enabled=False` override. | Removes proxy-side back-pressure entirely on this path; if Anthropic genuinely outages, every stream tries the upstream and Claude Code TUI sees raw provider errors. **Acceptable for Claude Code** since the client already retries on its own; the breaker was mostly redundant for that path. |
| **B6** | **Surface upstream error to Claude Code without proxy-side circuit amplification** — when breaker is OPEN, return the LAST observed upstream error code (e.g., `503` from the provider) instead of the breaker's own `503` envelope | Changes the response body shape on `request_rejected` events. | Risk: clients that have been parsing the proxy's specific reject envelope (e.g., the embedded `circuit_breaker_open:<provider>` notes string) will see different content. **Acceptable** since `notes` is in the event row, not the HTTP body. |
| **B7** | **No proxy retry unless explicitly proven safe** — keep the existing zero-retry posture on the proxy side; do NOT introduce M6 (limited retry-once on protocol errors) under any condition until and unless H8 is disproven | Status-quo + explicit anti-pattern note in the spec. | None — preserves the user's standing constraint. **Adopted unconditionally** in Phase 1 implementation; this just records the rule. |
| **B8** | **Lower the global threshold to be far above the typical regression** — e.g., `failure_threshold=15` instead of 5 — so 12-burst-failures don't trip even without per-class differentiation | One config dial change. | Crude — pushes the cliff farther but doesn't address the per-class semantic mismatch. Still vulnerable when a real outage produces 15+ failures, which is exactly when the breaker SHOULD trip. **Not recommended** standalone. |

**Out of approved candidate set:** broad cross-request retry, automatic retry-with-backoff, proxy-side hedging, proxy-side request multiplexing, transport-pool changes (#74 Phase 2 is closed) — all explicitly excluded.

---

## 5. Recommended path (for spec sign-off, not implementation)

**Primary: B1 (per-error-class thresholds) + B5 (advisory mode for Claude Code path), staged.**

### 5.1 Minimum-risk first stage — B5 only (advisory mode for Claude Code)

The smallest, least-controversial change: make the Claude Code OAuth path (`api.anthropic.com` accessed via the OAuth/subscription provider in `services/routing_service`) **advisory-only** for the breaker. The breaker still observes failures and emits telemetry; it does NOT fast-fail, so streams reach the upstream and either succeed or fail naturally.

Why advisory-mode is safe for Claude Code specifically:
- Claude Code TUI already has its own retry loop — the proxy breaker doubles up needlessly
- Anthropic's edge produces fast errors when overloaded (RemoteProtocolError, 503, 429); the client sees those directly
- No proxy-side fast-fail = no `circuit_breaker_open:anthropic` cascade = no amplified visible-retry storm
- Reversible via env flag (`TOKENPAK_CB_ADVISORY_FOR_CLAUDE_CODE=0` to re-enable)
- Other providers (`openai`, `google`, `azure`, etc.) keep their current breaker behavior unchanged

**Verification gate for B5 stage:** zero new `circuit_breaker_open:anthropic` events in the 3-concurrent post-fix workload. If observed, advisory mode is broken or there's a second code path tripping the breaker that we missed.

### 5.2 Second-stage refinement — B1 (per-error-class thresholds) for non-Claude-Code paths

Once B5 is shipped and stable, refine the breaker for OTHER providers with per-class thresholds. Initial proposed defaults:

| Class | Threshold (5s window) | Threshold (60s window) | Reasoning |
|---|---|---|---|
| HTTP 5xx | 3 | 5 | sustained-outage signal — keep aggressive |
| HTTP 429 | 5 | 10 | rate-limit signal — slightly more permissive (transient) |
| Timeouts | 3 | 5 | sustained signal |
| `RemoteProtocolError` / `LocalProtocolError` (pre-first-byte) | 10 | 20 | transient transport class — let several through before tripping |
| `RemoteProtocolError` (mid-stream) | 3 | 5 | mid-stream is a real signal of upstream instability |
| Other exception | 3 | 5 | conservative default |

These are starting points; final values determined empirically from a follow-on workload after the implementation lands.

### 5.3 Items deferred / not included in this primary path

- **B2** (special pre-first-byte tighter window) — folded into B1's class differentiation
- **B3** (per-session / per-lane bucket) — held; B1 + B5 should suffice. Revisit if per-class differentiation alone doesn't close the cascade.
- **B4** (shorter reset window for streaming OAuth) — held; B5 supersedes it for Claude Code.
- **B6** (surface upstream errors directly) — held; B5 makes it less urgent (no fast-fail wrapping). Revisit if other-provider B1 ships and reveals a need.
- **B8** (raise global threshold) — explicitly NOT recommended — it's a band-aid that doesn't address the underlying per-class semantics.
- **M6 retry-once** — explicitly remains rejected under the H8-supported ranking.

---

## 6. Safety constraints (binding)

NCP-4 implementation MUST preserve all of:

- ✅ No broad retry amplification — proxy retry stays at zero for Claude Code path; B5 advisory mode adds no new retries
- ✅ No retry after `bytes_to_client > 0` — preserved (no proxy retry to begin with)
- ✅ No auth / provider / model / routing / prompt changes
- ✅ Preserves Claude Code OAuth/subscription path — auth flow, OAuth bearer, anthropic-beta headers all untouched
- ✅ Preserves SSE framing — no buffering / rewriting
- ✅ No transport pool experiments — pool surface stays at main config; #74 Phase 2 is closed
- ✅ No #75 work
- ✅ `tp_events` untouched (per #79 canonicality)
- ✅ `tp_parity_trace` event constants and `LIFECYCLE_ORDER` unchanged (B1 may add new `notes` content but no schema migration)
- ✅ Existing `parity_trace.py` emit points unchanged

---

## 7. Measurement plan (for future implementation)

### 7.1 Workload

3-concurrent × 3-call `tokenpak claude -p` workload — same shape as PR #80 verification, deterministic and cheap.

```bash
for s in 1 2 3; do
  ( for i in 1 2 3; do
      timeout 45 tokenpak claude -p "Reply with exactly two words: stream$s call$i"
    done ) &
done
wait

TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 scripts/inspect_session_lanes.py --window-minutes 30 \
  --output tests/baselines/ncp-3-trace/${TS}-ncp4-postfix-3tp.md
python3 scripts/inspect_session_lanes.py --window-minutes 30 --json \
  --output tests/baselines/ncp-3-trace/${TS}-ncp4-postfix-3tp.json
```

### 7.2 Metrics to compare

| Metric | Phase 1 baseline (21:37Z) | Phase 2B failed (22:30Z) | NCP-4 target (B5 advisory only) |
|---|---|---|---|
| `traces_with_handler_entry` | 149 | 101 | comparable (workload variance) |
| `traces_with_clean_wire_completion` | 86 | 63 | **higher** (fewer false fast-fails) |
| `traces_with_terminal_abort` | 8 | 12 | **comparable or higher** (B5 doesn't reduce upstream aborts; it just doesn't compound them) |
| `stream_abort_phase_distribution.upstream_protocol_error` | 5 | 12 | **comparable** — B5 does not address this class directly; it only stops the breaker cascade |
| `traces_with_terminal_fast_fail` (`request_rejected`) — `circuit_breaker_open:anthropic` subset | 0 | 13 | **target: 0** — primary success criterion for B5 |
| Other `request_rejected` (auth, validator) | 0 | 0 | **must remain 0** |
| `bytes_from_upstream` / `bytes_to_client` | 86 traces with bytes > 0 | 63 traces with bytes > 0 | **comparable or higher** |
| Median request duration | not captured | not captured | track post-fix; may change as more streams complete (vs being fast-failed) |
| Visible TUI retries (anecdotal `Retrying in 20s` count) | observed pre-#77 | observed during M2/2B regression | **fewer** (no `circuit_breaker_open` rejections to retry) |
| `json_parse_error_seen` rows | 0 | 0 | **must remain at zero** |

### 7.3 Verification gates (binding for implementation PR)

- ⚠️ Cannot ship if `circuit_breaker_open:anthropic` cascade increases or remains > 0 under B5
- ⚠️ Cannot ship if total `traces_with_terminal_abort` increases (B5 should be neutral on aborts; if it makes them worse, something else is wrong)
- ⚠️ Cannot ship if `traces_with_clean_wire_completion` decreases below the Phase 2B level
- ⚠️ Cannot ship if any `json_parse_error_seen` row appears
- ⚠️ Cannot ship if `traces_with_terminal_fast_fail` (any class) increases
- ⚠️ Cannot ship if the `request_rejected` event distribution shows new sub-classes that weren't observable before

---

## 8. Acceptance criteria for NCP-4 implementation

When NCP-4 ships:

- [ ] `circuit_breaker_open:anthropic` cascade reduced to zero in equivalent 3×3 workload
- [ ] No increase in `stream_abort` count
- [ ] No increase in `upstream_protocol_error` count (this PR doesn't address that — H8 stays open)
- [ ] No JSON parse errors
- [ ] No retry amplification (proxy still doesn't retry; client retry is observable but unchanged)
- [ ] No auth/provider/model/routing/prompt edits
- [ ] CI green (all required-status checks)
- [ ] Post-fix baseline committed at `tests/baselines/ncp-3-trace/<TS>-ncp4-postfix-3tp.{md,json}`
- [ ] `tokenpak/proxy/circuit_breaker.py` change is contained: B5 stage adds an `enabled_per_provider` config dict (or similar minimal surface) with `anthropic: advisory`; B1 stage adds per-class threshold dict
- [ ] New unit tests under `tests/proxy/test_circuit_breaker_advisory.py` covering: advisory mode does not fast-fail; advisory mode still records failures for telemetry; non-advisory providers still trip normally; reversibility env flag works
- [ ] Reversibility env flags exposed: `TOKENPAK_CB_ADVISORY_PROVIDERS=anthropic` (comma-separated; empty disables advisory mode entirely)

---

## 9. Out of scope (do not start)

Per Kevin's standing direction (2026-04-27 evening):

- **#75 NCP-3I-v4** (14 upstream_attempt_start orphans) — explicitly held
- **#74 Phase 2** — closed (pool experiments halted)
- **M6 (limited retry-once)** — parked indefinitely; H8-supported ranking says it would amplify
- **Transport pool changes** — pool surface stays at main config
- **NCP-3A streaming-connect Phase 2** — closed
- **Provider/model routing changes** — out of scope
- **Auth behavior changes** — out of scope
- **Prompt mutation changes** — out of scope
- **Cache behavior changes** — out of scope
- **Tokenpak-status metric surface** — out of scope (no new metrics in this lane)

---

## 10. Files NCP-4 implementation would touch (if approved)

| File | Change | Δ LOC estimate |
|---|---|---|
| `docs/internal/specs/ncp-4-retry-circuit-breaker-parity-2026-04-27.md` | This doc, finalized as the NCP-4 spec | this file |
| `tokenpak/proxy/circuit_breaker.py` | **B5 stage:** add `advisory_providers: frozenset` to `CircuitBreakerConfig`; `CircuitBreaker.allow_request()` returns True (with telemetry-only effect) when the provider is in the advisory set. **B1 stage (later):** restructure `failure_threshold` from a single int to a `Dict[str, int]` keyed by error class (`protocol_error_pre_first_byte`, `protocol_error_mid_stream`, `http_5xx`, `http_429`, `timeout`, `other`); `record_failure(class_name)` updates the matching counter. | ~80 (B5) + ~120 (B1) |
| `tokenpak/proxy/server.py` | At the existing `record_failure` call sites, pass an `error_class` argument derived from the exception type (B1 stage only). For B5 the call sites are unchanged. | ~10 (B1 only) |
| `tests/proxy/test_circuit_breaker_advisory.py` | New tests: B5 advisory mode; reversibility env flag; non-advisory providers unaffected. | ~120 |
| `tests/proxy/test_circuit_breaker_per_class_threshold.py` | New tests (B1 stage): per-class thresholds; class-misclassification fallback; threshold dict env-var parsing. | ~150 |

**Files NOT touched:**
- `tokenpak/proxy/connection_pool.py` — pool surface stays at main
- `tokenpak/proxy/parity_trace.py` — schema, events, emit points unchanged
- `tokenpak/services/routing_service/**` — routing untouched
- `tokenpak/services/auth_service/**`, `agent/auth/**` — auth untouched
- `tokenpak/compression/**`, `vault/**`, `companion/**` — prompt-mutation untouched
- `tokenpak/cache/**` — cache untouched
- `tp_events` — untouched (#79 canonicality)

---

## 11. Open questions for Kevin

1. **B5 alone (Phase A) vs B5 + B1 in one PR (combined Phase A+B).** Recommendation: **Phase A first** (B5 only). Smaller blast radius; data from B5 alone may close enough of the cascade that B1 is unnecessary. If B5 alone fails to close the cascade — unlikely but possible if there's another fast-fail surface — revisit B1.
2. **Advisory-set scope.** Should advisory mode be `anthropic` only, or also `openai` + others when accessed via OAuth/subscription paths? Recommendation: **`anthropic` only** for Phase A (the actual #74 cohort surface). Other OAuth providers (Codex/OpenAI subscription) come in if/when they show similar cascade patterns.
3. **Reversibility flag default.** `TOKENPAK_CB_ADVISORY_PROVIDERS=anthropic` (default-on for Anthropic) vs default-off-with-opt-in. Recommendation: **default-on** with the env-var available as escape hatch. The cascade is deterministic in the data; default-on is justified.
4. **Should the `request_rejected` event survive B5?** When advisory mode is in effect and the upstream returns its own `5xx` / network error, the client sees that directly — no proxy-side `request_rejected` is emitted. Telemetry-wise: `tp_parity_trace` will show `stream_abort` (with the original error class) but no `request_rejected` for advisory-mode failures. This is the *intended* outcome and the cleanest signal.
5. **Per-provider config surface shape.** Single env var (`TOKENPAK_CB_ADVISORY_PROVIDERS=anthropic,openai`) vs structured TOML / JSON file. Recommendation: **env var (comma-separated)** for Phase A — same pattern as `TOKENPAK_HTTP2`, `TOKENPAK_STREAM_KEEPALIVE`, etc. Promote to structured config only if the matrix grows beyond ~3 providers.
6. **Issue tracker shape.** Open a new issue `NCP-4: retry / circuit-breaker parity`, link to it from #74, and use it as the tracker for this lane? Or use a new issue per phase (NCP-4-A for B5, NCP-4-B for B1)? Recommendation: **single NCP-4 issue with phase-tagged PRs** — same pattern as #74's Phase 1 / Phase 2.

---

## 12. Estimated effort

- **NCP-4 Phase A (B5 advisory mode for `anthropic`):** ~2 hours — small breaker-config change + tests + workload rerun + PR cycle
- **NCP-4 Phase B (B1 per-class thresholds):** ~3–4 hours — config restructure + per-call-site error_class wiring + tests + tuning
- **Combined if both ship together:** ~5–6 hours

This puts the full NCP-4 lane comfortably in 1–2 sessions.
