# NCP-3A-streaming-connect — Phase 2 spec (issue #74)

**Date:** 2026-04-27 (M2 rejected; Phase 2B now active)
**Status:** ⚠️ **M2 REJECTED — verification regressed at 22:19Z** · ✅ **Phase 2B (narrow) approved + landing in this PR**
**Workstream:** NCP-3 (Session-Lane Preservation) → NCP-3A (Streaming) → NCP-3A-streaming-connect → **Phase 2 mitigation**
**Authors:** Sue (scope) / Kevin (review + go-ahead)
**Tracker:** [tokenpak/tokenpak#74](https://github.com/tokenpak/tokenpak/issues/74) — stays open through Phase 2 implementation per Kevin's directive
**Companion docs:**
- Phase 1 spec: `docs/internal/specs/ncp-3a-streaming-connect-2026-04-27.md`
- Phase 1 implementation: PR #80 merged 2026-04-27 21:43:05Z, commit `6b8e18f9b6`
- Issue #73 / #78 / #79 — wire-side completion canonicality (foundation for the measurement plan)
- `tokenpak/proxy/connection_pool.py` — current httpx pool implementation (the surface Phase 2 mitigations will modify)
- `tokenpak/proxy/server.py` lines 1857 (streaming dispatch via `pool.stream(...)`), 1968 (BrokenPipe → `client_disconnect`), 2389 (upstream-exception → `upstream_protocol_error`)

> **Goal:** design the mitigation plan for `stream_abort` traces classified as `upstream_protocol_error` during concurrent TokenPak Claude Code workloads. **Spec only — no implementation in this lane.**

---

## 1. Problem statement

Concurrent `tokenpak claude` sessions can produce `stream_abort` events. Phase 1's classifier shows the dominant phase is `upstream_protocol_error` (httpx `RemoteProtocolError` / `LocalProtocolError`) — 5/8 in the 21:35Z verification workload, 31/32 in the original NCP-3I-v3 trace.

The signature is consistent across runs:
- multiple traces abort within a small (~20 ms) window
- all carry the **same** `stream_exception_message_hash`
- `bytes_from_upstream == 0` and `bytes_to_client == 0`
- `upstream_status` is null in 26/32 cases (no headers seen) and 200 in 6/32 (headers seen, no body byte)

Phase 2 must materially reduce the `upstream_protocol_error` aborts during concurrent Claude Code workloads **without breaking native parity** (NCP-1 invariants), changing provider semantics, or introducing broad retry amplification.

---

## 2. Candidate root causes (ranked by current evidence)

| # | Hypothesis | Evidence pro | Evidence con / open |
|---|---|---|---|
| 1 | **HTTP/2 multiplex collapse** — multiple concurrent streams share a single TCP connection via HTTP/2 multiplexing (`h2 4.3.0` installed, `TOKENPAK_HTTP2=1` default). When the upstream sends `GOAWAY`, hits an idle timeout, or the connection is reset, **every multiplexed stream aborts simultaneously**. | Identical exception-message hash + ~20 ms tight window across multiple traces. Matches the multiplex-failure footprint exactly. `connection_pool.py` confirms one shared `httpx.Client` per netloc. | Need `nGoAway`/protocol-trace evidence to fully confirm; httpx doesn't surface H2 protocol details to the application. Phase 2 implementation should add an HTTP/1.1-fallback test arm to discriminate. |
| 2 | **Stale keep-alive on HTTP/1.1 fallback** — if HTTP/2 negotiation drops, fallback connections kept-alive for 30 s may be torn down server-side; client doesn't know until the next use, which fails with `RemoteProtocolError`. | Symptom matches generic stale-connection-reuse. `keepalive_expiry=30.0` is permissive given Anthropic's likely server-side window. | Less likely as the *primary* cause given the H2 multiplex evidence; more likely a contributory class in mixed-protocol environments. |
| 3 | **Connection reuse after upstream half-close** — Anthropic closes the response side of an HTTP/1.1 connection (e.g., after a 503 or rate-limit) but client retains it in pool; next request tries to reuse and protocol-errors. | Cannot be ruled out without per-trace upstream-status correlation. | Phase 1's `connection_closed_early` field captures part of this for the BrokenPipe path but not for upstream-side. |
| 4 | **Shared pool collapse under concurrent streams** — pool-wide eviction event (e.g., `max_connections=20` saturated, oldest connection forcibly closed mid-stream). | `max_connections=20` is comfortable for 3-concurrent; unlikely at our load. | Recheck under heavier concurrency; not the active hypothesis at 3-concurrent. |
| 5 | **Upstream stream lifecycle not isolated per request** — same ConnectionPool client instance services both billable token-burning streams AND throwaway probes/health checks. A failed probe could disturb in-flight streams in HTTP/2. | Possible but speculative; needs audit of all `pool.stream()` and `pool.request()` callers. | Out of scope for first mitigation; revisit if isolation alone doesn't close the cohort. |
| 6 | **Read timeout boundary during concurrent SSE streams** — `read_timeout=300 s` per request; under HTTP/2 multiplexing, the TCP-level read covers multiple streams, so one stream's slow chunk could starve another past its read deadline. | Tangential; Anthropic streams typically arrive at sub-second cadence. | Not the active hypothesis. |
| 7 | **Retry-owner mismatch between Claude Code client and TokenPak proxy** — Claude Code retries on RemoteProtocolError; TokenPak retries on RemoteProtocolError; both retrying simultaneously amplifies load and re-triggers H2 collapse. | Plausible amplifier *after* the initial collapse; not the root cause. | Phase 2 must NOT add proxy-side retry that competes with Claude Code's existing retry loop. The user's standing constraint covers this. |

**Strongest single hypothesis (original):** #1 (HTTP/2 multiplex collapse). Every observation is consistent with it; nothing rules it out. #2 and #3 are contributory but secondary.

> **Update 2026-04-27 22:19Z — H1 likely WRONG.** Phase 2 M2 verification (HTTP/1.1 + no keepalive) **regressed** on two of the four hard gates. See §2.5 for the regression record and the new H8 hypothesis.

---

## 2.5 Phase 2 M2 regression record (2026-04-27 22:19Z) — **M2 REJECTED**

The M2 implementation (streaming-only client with `http2=False`, `max_keepalive_connections=0`, `keepalive_expiry=0.0`) ran in the production proxy from 22:18:51Z to ~22:23Z, then was reverted. The 3-concurrent verification workload at 22:18:51Z–22:19:46Z produced:

| Metric | Phase 1 baseline (21:37Z) | Phase 2 M2 (22:19Z) | Δ | Spec gate (§6.3) |
|---|---|---|---|---|
| `traces_with_handler_entry` | 149 | 83 | -44% (workload variance) | n/a |
| `traces_with_clean_wire_completion` | 86 | 45 | -47% | n/a |
| `traces_with_terminal_abort` | 8 | **12** | **+50%** | ❌ **GATE FAIL** |
| `upstream_protocol_error` aborts | 5 | **12** | **+140%** | ❌ **TARGET MISSED** (was ≥80% reduction) |
| `traces_with_terminal_fast_fail` (`request_rejected`) | 0 | **13** | **+13** (all `circuit_breaker_open:anthropic`) | ❌ **GATE FAIL** |
| `traces_without_terminal_event` | 42 | 12 | -71% | n/a (orthogonal cohort) |

Two of the four hard verification gates failed. M2 cannot ship.

**M2 forensic artifacts preserved:**
- Stash entry `stash@{0}` on branch `feat/issue-74-phase-2-streaming-pool-isolation` ("phase 2 m2 work — regression observed, reverting running proxy")
- Verification baseline `tests/baselines/ncp-3-trace/20260427T221946Z-issue74p2-postfix-3tp.{md,json}`

**Implication for hypothesis ranking:** H1 (HTTP/2 multiplex collapse) is **likely wrong as stated**. Forcing HTTP/1.1 + per-stream fresh TCP made `upstream_protocol_error` aborts MORE frequent (5→12), not fewer. Two reframed hypotheses:

### H8 (new — supersedes H1's assumption that H2 was the harm) — *HTTP/2 multiplexing is protective; fresh-connection bursts trigger upstream connection-establishment limits*

**Argument:** under HTTP/2, multiple concurrent Claude Code streams share one TCP connection to `api.anthropic.com`, so the upstream sees a small, steady-rate connection footprint. M2 forced each stream to negotiate its own TCP+TLS handshake, presenting the upstream with a sudden burst of fresh connections. Anthropic's edge / load-balancer / WAF likely applies a connection-establishment rate limit per source IP that kicks in when the burst rate spikes. The result: more connection-level protocol errors, not fewer.

**Evidence consistent with H8:**
- M2 made `upstream_protocol_error` rate go up by 140%, not down
- The 3-simultaneous identical-hash signature observed in the original 32-trace cohort may itself be the *consequence* of one connection-level event (a single GOAWAY or shared-connection timeout) affecting all multiplexed streams — i.e. ONE upstream-side failure shows up in N traces. Looks like "multiplex collapse" but is closer to "shared aggregator that fans-in upstream events to N streams."
- 13 cascading `request_rejected` events (circuit-breaker-trip) confirm the proxy correctly responded to a *higher* upstream-failure rate, not a lower one.

**Implication for Phase 2:** the right lever is **not** to disable HTTP/2. The right lever is to test whether the *keepalive* component alone (independent of HTTP/2 multiplexing) is the harm.

### H9 (new) — *stale keepalive within HTTP/2 connection*

Even with HTTP/2 multiplexing, an idle TCP connection's underlying socket can be torn down server-side (Anthropic edge timeouts, NAT eviction, etc.) without the client knowing. The next stream attempted on that connection fails with `RemoteProtocolError`. This is the same H2 (stale keepalive) hypothesis from the original ranking, narrowed to be H2-multiplex-aware.

**Implication for Phase 2:** disable streaming-pool keepalive (no idle reuse), but keep HTTP/2 enabled (preserve the protective per-stream multiplexing inside a fresh connection's lifetime).

---

## 3. Candidate mitigations (with tradeoffs)

| Mitigation | What it changes | Targets hypothesis | Tradeoff / risk |
|---|---|---|---|
| **M1. Disable HTTP/2 for the streaming path only** | Add a streaming-only `httpx.Client` in `ConnectionPool` with `http2=False`; route `pool.stream(...)` to it for `api.anthropic.com`. Non-streaming traffic keeps HTTP/2. | #1 directly | Loses HTTP/2 multiplexing perf for streams (≤5 ms reuse savings). Acceptable for Claude Code where streams are minutes-long; perf loss is negligible. |
| **M2. Disable keepalive for the streaming path only** | Streaming-only client with `max_keepalive_connections=0` and `keepalive_expiry=0.0`. Each stream gets a fresh connection. | #1 (eliminates multiplex sharing across streams), #2, #3 | One TCP+TLS handshake per stream (~50–100 ms first-byte penalty). Acceptable for Claude Code request cadence. **Recommended primary mitigation** — see §4. |
| **M3. Per-stream isolated client (no pool at all for streams)** | Construct a fresh `httpx.Client` for every streaming request; close on stream end. | #1, #2, #3, #5 (full isolation) | Highest perf cost (~50–100 ms handshake every stream, no warm pool); strongest guarantee. |
| **M4. Lower `max_keepalive_connections`** | Drop default from 10 to 1 or 2 for the streaming pool. | #2, #4 | Half-measure compared to M2. Doesn't address H2 multiplex (#1). |
| **M5. Configure shorter `keepalive_expiry`** | Drop from 30 s to e.g. 5 s. | #2 | Half-measure. Doesn't address H2 multiplex (#1). Increases TLS handshake count without cleanly isolating streams. |
| **M6. Retry once on pre-first-byte `RemoteProtocolError` only** | Defensive retry: at the upstream-exception emit site, if `abort_phase ∈ {before_headers, after_headers_before_first_byte, upstream_protocol_error}` AND `bytes_to_client == 0`, re-issue the request once. | All upstream-side aborts (defensive) | **Risk: amplifies load** if M1/M2/M3 don't address root cause. **Risk: collides with Claude Code's own retry loop** (the retry-owner-mismatch concern, hypothesis #7). Should NOT be the primary mitigation. Acceptable as a *secondary* mitigation IFF strict limits hold (max 1, never after `bytes_to_client > 0`). |
| **M7. Drain/close broken streams defensively** | At the upstream-exception path, explicitly close the underlying httpx response/connection before re-raising, ensuring the broken connection is removed from the pool. | #2, #3, #5 | Marginal — httpx already invalidates connections that raise; this is defense-in-depth. |
| **M8. Circuit breaker tuning to not amplify retries** | Audit current circuit-breaker behavior to ensure it doesn't **add** retry pressure when the streaming path is healthy elsewhere. Existing tripped-state behavior already correctly fast-fails (per #77 `EVENT_REQUEST_REJECTED`). | #7 | Audit-only in Phase 2; do not change breaker semantics in this lane. |

**Out of approved candidate set:** broad cross-request retry, automatic backoff, server-side request hedging — all explicitly excluded by Kevin's safety constraints.

---

## 4. Recommended safe first mitigation

> ~~**Primary: M2 (streaming-pool isolation with keepalive disabled).**~~ — **REJECTED 2026-04-27 22:19Z after verification regression. See §2.5.**
>
> ~~**Secondary: M6 (limited retry-once).**~~ — Not reached; Phase 2 did not get past the primary mitigation.

### Phase 2B (active) — streaming-keepalive isolation only (HTTP/2 preserved)

Per Kevin's 2026-04-27 narrowed scope: test whether stale keepalive reuse alone is the cause, **without** disabling HTTP/2 multiplexing. The minimum experimental change targeting H9 (stale keepalive within HTTP/2 connection) without disturbing H8 (HTTP/2 is protective):

1. Extend `tokenpak/proxy/connection_pool.py` with a streaming-only client (parallel `_streaming_clients` map) constructed with:
   - **`http2=True`** (default — HTTP/2 stays enabled; this is the change vs M2)
   - `max_keepalive_connections=0` (no idle-reuse — eliminates stale-keepalive cohort)
   - `keepalive_expiry=0.0` (defensive, redundant with the above)
   - Same `max_connections=20`, TLS, timeouts as the request client
2. Route `ConnectionPool.stream(...)` to the streaming client.
3. Non-streaming traffic (`pool.request(...)`) is **untouched** — continues to use HTTP/2 + keepalive on the original `_clients` pool.
4. Reversibility: `TOKENPAK_STREAM_HTTP2` (default `1` — HTTP/2 enabled) and `TOKENPAK_STREAM_KEEPALIVE` (default `0` — keepalive disabled). Setting `TOKENPAK_STREAM_HTTP2=0` reproduces the M2 conditions for forensic comparison; setting `TOKENPAK_STREAM_KEEPALIVE=1` returns streaming to main's behavior.

**What's different vs M2 (the only change):**
- `streaming_http2: bool = True` (was `False` in M2)
- `from_env()` default for `TOKENPAK_STREAM_HTTP2` is `"1"` (was `"0"` in M2)

Everything else (the streaming-only client structure, `_get_streaming_client()`, `stream(...)` routing, `close()` cleanup, no behavior change to `request(...)`) is identical to the M2 implementation. The M2 stash at `stash@{0}` documents the narrower-mitigation diff if needed.

**Why this is the right narrowed primary (per Kevin):**
- **Targets H9 only.** Disables idle keepalive without disturbing H2 multiplexing.
- **Discriminates H8 vs H9.** If Phase 2B passes, H9 (stale keepalive within H2) was the cause. If 2B also regresses, H8 (HTTP/2 protective; fresh-connection bursts harmful) is the dominant cause and the proper next lane is NCP-4 retry/circuit-breaker behavior or upstream-pressure diagnostics — not further pool experimentation.
- **No retry, no breaker tuning, no auth/routing/provider/model/cache/prompt edits.**
- **Preserves SSE framing.** HTTP/2 streams remain native; only the connection's lifetime is shortened (no idle reuse).
- **Preserves Claude Code OAuth/subscription path.** Untouched.
- **Cleanly reversible.** Two env vars, default ship-on with escape hatches.

**Phase 2B regression rule (binding):** if the verification workload in §6 shows `upstream_protocol_error` increase OR `traces_with_terminal_abort` increase OR `request_rejected` increase, **STOP**. Revert. **Do NOT attempt further pool experiments.** Re-scope toward NCP-4 retry/circuit-breaker behavior or provider-side connection pressure diagnostics. M6 is NOT a fallback at this point — it would amplify the existing problem.

---

## 4.5 Phase 2B regression record (2026-04-27 22:30Z) — **PHASE 2B REJECTED · pool experiments halted**

The Phase 2B implementation (HTTP/2 enabled, keepalive disabled) ran in the production proxy from 22:29:41Z to ~22:31Z and was reverted. The 3-concurrent verification workload at 22:29:41Z–22:30:53Z produced:

| Metric | Phase 1 baseline | Phase 2 M2 | **Phase 2B** | M2 vs P1 | **2B vs P1** | 2B vs M2 |
|---|---|---|---|---|---|---|
| `traces_with_handler_entry` | 149 | 83 | 101 | -44% | -32% | +22% |
| `traces_with_clean_wire_completion` | 86 | 45 | 63 | -47% | -27% | +40% |
| `traces_with_terminal_abort` | 8 | 12 | **12** | +50% | **+50% — GATE FAIL** | identical |
| `upstream_protocol_error` aborts | 5 | 12 | **12** | +140% | **+140% — TARGET MISSED** | identical |
| `request_rejected` (`circuit_breaker_open:anthropic`) | 0 | 13 | **13** | +13 | **+13 — GATE FAIL** | identical |
| `traces_without_terminal_event` | 42 | 12 | 12 | -71% | -71% | identical |

**The failure profile is essentially identical to M2.** HTTP/2 vs HTTP/1.1 made no measurable difference. The common factor is **disabling keepalive on the streaming path**. Per the Phase 2B regression rule (§4): pool experiments are halted.

**Phase 2B forensic artifacts preserved:**
- Stash entry `stash@{0}` ("phase 2b work — regression observed (12 protocol_error + 13 cb_open, identical to M2); stopping pool experiments")
- Verification baseline `tests/baselines/ncp-3-trace/20260427T223053Z-issue74p2b-postfix-3tp.{md,json}`
- M2 stash retained at `stash@{1}` ("phase 2 m2 work — regression observed, reverting running proxy")

### Conclusion across both pool experiments

Two attempts with two different transport postures produced the **same elevated abort rate**. The conclusive finding: **the act of disabling keepalive on the streaming path itself is the harm.** Hypotheses ranked after both runs:

| Hypothesis | Status after 2B |
|---|---|
| H1 (HTTP/2 multiplex collapse) | **rejected** — disabling H2 didn't help |
| H2 / H9 (stale keepalive within H2) | **rejected** — disabling keepalive didn't help; in fact, made it worse |
| **H8 (HTTP/2 multiplexing is protective; fresh-connection bursts trigger upstream connection-establishment limits)** | **strongly supported** — the only common factor between the two failed runs is forcing fresh connections per stream. Burst rate ≈ 3 fresh TLS handshakes within ~1 s window. |

### Implications for next lane

**No further pool experiments.** The pool surface has been adequately tested in two configurations; both regressed in the same way. The right next lanes are either:

1. **NCP-4 — retry / circuit-breaker behavior parity.** The 13 `request_rejected` events under both M2 and 2B are the breaker correctly fast-failing after the abort surge. Tuning the breaker (less aggressive trip, faster reset, or per-class thresholds) might soften the cascade without addressing the root cause; or, leaving the breaker alone, the parity question is whether retries should be the proxy's responsibility vs. the Claude Code client's.

2. **Upstream-pressure diagnostics — characterize Anthropic's edge connection-establishment behavior.** Capture `httpx` event hooks at TLS-handshake / connection-establishment / GOAWAY frames and produce a baseline of provider-side limits. Could surface from existing `tp_parity_trace` rows by adding finer event types, or via httpx's `EventHooks` (read-only), or via a one-shot `tcpdump` / `mitmproxy` capture.

**M6 (limited retry-once) remains parked.** It would amplify the existing problem if pool isolation isn't the lever.

**Recommendation:** scope NCP-4 next, **not** further pool work. The current `request_rejected` cascading on M2/2B is a real finding even at main's pool config — it just happens to be invisible at main because the underlying abort rate is lower. NCP-4 is the right lane to investigate the proxy's retry-and-breaker behavior end-to-end. Provider-side diagnostics can be a parallel side-quest if Kevin wants more raw data.

---

## 5. Safety constraints (binding)

Phase 2 implementation **MUST** preserve all of:

- ✅ No provider/model routing changes — the routing layer (`services/routing_service/*`) is untouched
- ✅ No auth behavior changes — credential injection, OAuth flow, refresh path all untouched
- ✅ No prompt mutation changes — `compression/`, `vault/`, `companion/` capsule code all untouched
- ✅ No cache behavior changes — `cache/` module untouched; cache_origin telemetry untouched
- ✅ No broad retry amplification — only the narrow M6 case (if it lands), max 1, gated as in §4
- ✅ No retry after downstream bytes have been sent (`bytes_to_client > 0` is a hard fence)
- ✅ Preserves SSE framing — no buffering/rewriting of stream chunks
- ✅ Preserves Claude Code OAuth/subscription path — auth headers, anthropic-beta, OAuth bearer all untouched
- ✅ No new schema migrations on `tp_parity_trace` — Phase 1 already established the diagnostic columns
- ✅ Existing `tp_events` canonicality (per #79) preserved — proxy still does not write `tp_events`
- ✅ `parity_trace.py` event constants and `LIFECYCLE_ORDER` unchanged
- ✅ Existing native-parity invariants from NCP-1 preserved (no new headers, no new request mutations)

---

## 6. Measurement plan (before/after)

Run the same 3-concurrent `tokenpak claude` workload before and after Phase 2 lands. The Phase 1 baseline at `tests/baselines/ncp-3-trace/20260427T213703Z-issue74p1-postfix-3tp.{md,json}` is the **before** reference.

### 6.1 Workload

```bash
# 3 streams × 3 calls each, ~9 invocations, ~50–60 s wallclock
for s in 1 2 3; do
  ( for i in 1 2 3; do
      timeout 45 tokenpak claude -p "Reply with exactly two words: stream$s call$i"
    done ) &
done
wait

# Capture
TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 scripts/inspect_session_lanes.py --window-minutes 30 \
  --output tests/baselines/ncp-3-trace/${TS}-issue74p2-postfix-3tp.md
python3 scripts/inspect_session_lanes.py --window-minutes 30 --json \
  --output tests/baselines/ncp-3-trace/${TS}-issue74p2-postfix-3tp.json
```

### 6.2 Metrics to compare

| Metric | Phase 1 baseline (21:37Z) | Phase 2 target |
|---|---|---|
| `traces_with_handler_entry` | 149 | comparable (workload size dependent) |
| `traces_with_clean_wire_completion` (`stream_complete`) | 86 | **higher** (more cleanly streamed) |
| `traces_with_terminal_abort` (`stream_abort`) | 8 | **lower** |
| `stream_abort_phase_distribution.upstream_protocol_error` | 5 | **target: 0–1** |
| `stream_abort_phase_distribution.client_disconnect` | (1 outside window) | unchanged or lower |
| `stream_abort_phase_distribution.before_headers` | 0 | unchanged |
| `stream_abort_phase_distribution.after_headers_before_first_byte` | 0 | unchanged |
| `stream_abort_phase_distribution.mid_stream` | 0 | unchanged |
| `traces_without_terminal_event` (silent death) | 42 | unchanged or lower (orthogonal cohort) |
| `traces_with_terminal_fast_fail` (`request_rejected`) | 0 | **must remain 0 or comparable** (no regression) |
| Per-trace `bytes_from_upstream` / `bytes_to_client` | 86 traces with bytes > 0 | **comparable or higher byte success rate** |
| Median request duration | not captured pre-fix | track post-fix (TLS handshake adds ~50–100 ms first-byte) |
| Visible TUI retries (anecdotal `Retrying in 20s` count) | observed pre-#77 | **none expected post-fix** |
| JSON parse errors (`json_parse_error_seen` in `tp_parity_trace`) | not yet probed | **must remain at zero** (preserves SSE framing) |
| Pool reuse rate (`ConnectionPool.metrics().reuse_rate` for `api.anthropic.com`) | new measurement | streaming should drop to ~0 (per-stream fresh connection); non-streaming should keep its existing reuse rate |

### 6.3 Verification rules

- ⚠️ **Phase 2 cannot ship if `traces_with_terminal_abort` increases.**
- ⚠️ **Phase 2 cannot ship if `traces_with_terminal_fast_fail` increases.** (Would indicate the breaker tripped from a new failure class.)
- ⚠️ **Phase 2 cannot ship if `client_disconnect` increases.** (Would indicate the streaming path is now causing downstream issues.)
- ⚠️ **Phase 2 cannot ship if any `json_parse_error_seen` row appears.** (Would indicate SSE framing was disturbed.)
- ⚠️ **Phase 2 cannot ship if median request duration regresses by >250 ms.** (Some duration increase is expected from per-stream TLS handshake; >250 ms suggests connection-establishment is failing or pooling is broken.)

---

## 7. Acceptance criteria for Phase 2 implementation

When Phase 2 ships:

- [ ] `upstream_protocol_error` aborts **materially reduced** (target ≥80% reduction; baseline 5 → target ≤1 in the equivalent workload window)
- [ ] No increase in `request_rejected` (circuit breaker not amplified)
- [ ] No increase in `client_disconnect` (downstream path not disturbed)
- [ ] No `json_parse_error_seen` rows (SSE framing intact)
- [ ] No retry amplification visible in the telemetry (`EVENT_RETRY_BOUNDARY` count comparable or zero)
- [ ] No routing/auth/provider/model/prompt/cache changes — diff is contained to `tokenpak/proxy/connection_pool.py` (and possibly a minimal entry-point in `proxy/server.py` selecting the streaming client)
- [ ] All required-status CI checks green (`bandit`, `cli-docs-in-sync`, `headline-benchmark`, `self-conformance` 3.10/3.11/3.12, `Test` 3.10–3.13, `Lint`, `Import contracts`, `Repo Hygiene`)
- [ ] Post-fix trace baseline committed at `tests/baselines/ncp-3-trace/<TS>-issue74p2-postfix-3tp.{md,json}`
- [ ] Pool-reuse-rate metric (`ConnectionPool.metrics()`) shows the expected non-streaming reuse rate is preserved while streaming drops to ~0
- [ ] `parity_trace.py` event constants unchanged; `LIFECYCLE_ORDER` unchanged
- [ ] `tp_events` untouched (#79 canonicality preserved)
- [ ] Existing 44 `test_ncp3_inspect_session_lanes.py` and 45 `test_parity_trace_phase_ncp_3i.py` tests remain green; new pool-isolation tests added under `tests/proxy/test_connection_pool_streaming_isolation.py` (or similar)
- [ ] Reversibility flag exposed (`TOKENPAK_STREAM_HTTP2=1` and/or `TOKENPAK_STREAM_KEEPALIVE=1` env-var escape hatches) so the old behavior is one env var away if regression appears

---

## 8. Follow-up issue handling

- **Keep #74 open through Phase 2 implementation.** Phase 2 PR uses `Refs #74`, not `Closes #74`.
- **Close #74 only when Phase 2's acceptance criteria (§7) are all met** in a real workload — at that point the cohort is materially closed.
- **Do not start #75 yet.** Kevin's standing hold remains.
- **Do not start NCP-4 retry parity** unless Phase 2 implementation concludes that retry behavior is the correct lane (current evidence says it is **not** — pool isolation is the primary lever). If NCP-4 becomes relevant, it gets its own scoping cycle.
- **Do not bundle Phase 2 with any unrelated changes.** This PR is contained to transport pool plus tests plus baseline.

---

## 9. Files Phase 2 implementation would touch (if approved)

| File | Change | Δ LOC estimate |
|---|---|---|
| `docs/internal/specs/issue-74-streaming-connect-phase-2-2026-04-27.md` | This doc, finalized | this file |
| `tokenpak/proxy/connection_pool.py` | Add `_streaming_clients: Dict[str, httpx.Client]` and `_make_streaming_client()` factory; route `stream(...)` to it; expose env flags `TOKENPAK_STREAM_HTTP2` (default 0) and `TOKENPAK_STREAM_KEEPALIVE` (default 0) | ~80 |
| `tokenpak/proxy/server.py` | (minimal) — possibly nothing if pool selects internally; OR a one-line netloc check at line 1857 if conditional routing chosen there | 0–10 |
| `tests/proxy/test_connection_pool_streaming_isolation.py` | New test file: streaming uses streaming client; non-streaming uses default client; HTTP/2 flag honored; metrics are partitioned | ~150 |
| Optionally: `tests/test_parity_trace_phase_ncp_3i.py` | Add a parametrized integration test that simulates a multiplex collapse (mock `httpx.Client.stream` raising `RemoteProtocolError`) and confirms classification + (under primary mitigation) no other in-flight stream is affected | ~80 |

**Files NOT touched:**
- `services/routing_service/**` — routing untouched
- `services/auth_service/**`, `agent/auth/**` — auth untouched
- `compression/**`, `vault/**`, `companion/**` — prompt-mutation untouched
- `cache/**` — cache untouched
- `proxy/parity_trace.py` — schema, events, emit points unchanged
- `tp_events` — untouched (#79 canonicality)
- `tokenpak/proxy/server_async.py` — separate ASGI implementation; not the active proxy in production

---

## 10. Open questions for Kevin

1. **Conditional routing on netloc vs. always-on streaming client.** Recommendation: always-on for the streaming client (any `pool.stream(...)` call routes to it) — simpler and the perf cost is negligible. Alternative: gate by netloc (`api.anthropic.com` only). Either is workable; the always-on choice is one fewer code path.
2. **Reversibility flag default.** Recommendation: `TOKENPAK_STREAM_HTTP2=0` (HTTP/1.1) and `TOKENPAK_STREAM_KEEPALIVE=0` (no keepalive) as the *new defaults*; the env vars exist as escape hatches if Phase 2 regresses. Alternative: ship behind a feature flag defaulted off, opt-in for measurement. Recommendation: ship defaulted on — measurement is more reliable when the change is the steady state.
3. **Should M6 (limited retry) be specced now or held for a Phase 2.5 if needed?** Recommendation: hold. M2 alone is most likely sufficient; if not, a focused Phase 2.5 spec defines the retry surface with full constraints. Avoids over-engineering.
4. **Workload size for measurement.** Recommendation: same 3-concurrent × 3-call pattern as Phase 1 verification (deterministic, comparable, cheap). For higher confidence, a follow-on 5-concurrent × 5-call workload could be run after the initial baseline shows the expected drop.
5. **Pool-reuse-rate metric exposure.** Currently `ConnectionPool.metrics()` is module-internal. Should we expose it via `tokenpak status` for ongoing operational visibility? Recommendation: out of scope for Phase 2; consider as a separate dashboard/observability lane.

---

## 11. Estimated effort (Phase 2 implementation)

- ~3–4 hours: connection_pool.py changes + new test file + workload rerun + PR cycle
- ~1 hour additional if M6 (limited retry) ends up needed after measurement

This puts the full Phase 2 implementation comfortably in a single session, contingent on M2 alone closing the cohort.
