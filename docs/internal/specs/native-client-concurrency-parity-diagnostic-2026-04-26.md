# Native Client Concurrency Parity — diagnostic plan (NCP-0)

**Date**: 2026-04-26
**Status**: 🟡 **diagnostic only** — no runtime behavior changes proposed
**Workstream**: NCP (Native Client Concurrency Parity)
**Authors**: Sue (diagnostic) / Kevin (review)
**Companion standard proposal**: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`

> **Premise (from the directive):** TokenPak Companion Claude Code appears to hit upstream rate-limit / retry behavior with **fewer concurrent sessions** than native Claude Code TUI direct, on the same account. NCP-0 is the **measurement-and-evidence** phase. No knobs are flipped. No code is changed. The goal is to **find the cause** before proposing a fix.

---

## 0. Reading guide

Structured against the directive's seven inspection categories, plus a hypothesis matrix and a test plan.

| § | Question |
|---|---|
| 1 | What did the code-path inspection find? |
| 2 | Hypothesis matrix — what could be amplifying rate-limit pressure? |
| 3 | Evidence we already have (from PI-x landings + standing memory) |
| 4 | Evidence still needed — and how to gather it |
| 5 | Recommended A/B test methodology |
| 6 | Recommended unit + integration tests (when implementation begins) |
| 7 | Reproducibility checklist |
| 8 | Out of scope (what NCP-0 explicitly will not do) |

---

## 1. Code-path inspection summary

The diagnostic explored the seven directive categories against the current `main` branch (post-PI-3 closeout, commit `c2786269737`). Each finding cites file:line.

### 1.1 Retry behavior

**Finding (UNRESOLVED hypothesis evidence):** The proxy does NOT honour `Retry-After` headers from Anthropic. The failover engine uses a fixed 2 s wait + provider switch; there is no parsing of the upstream's `Retry-After` value.

- `tokenpak/proxy/failover_engine.py:8,42,98–100` — `RATE_LIMIT_WAIT_SECONDS = 2.0`, `MAX_RETRY_SAME_PROVIDER = 1`. Hard-coded, not adaptive.
- `tokenpak/proxy/handlers/rate_limit.py:19–73` — `RateLimitBackoff` class exists (with `retry_after` parameter support) but is **not** integrated into the request path. It is a utility, not a wired layer.
- No code path consumes the upstream `Retry-After` header before issuing a retry.

**Severity:** Medium. Could amplify pressure if the failover engine retries before Anthropic's quoted backoff window.

### 1.2 Token amplification (companion-added context)

**Finding (PRIMARY hypothesis):** The companion pre-send hook prepends vault search results + capsule context to **every** Claude Code request. This happens **client-side** (before the proxy), so the proxy never sees the unamplified baseline.

- `tokenpak/companion/hooks/pre_send.py:217–254` — `_query_vault_context(prompt, budget_chars)` runs `BlockStore.search(prompt, top_k=5)` and concatenates up to `budget_chars` chars.
- `tokenpak/companion/hooks/pre_send.py:196–214` — `_load_active_capsule(session_id)` reads `~/.tokenpak/companion/capsules/{session_id}.md` or `active.md`.
- `tokenpak/companion/hooks/pre_send.py:471–481` — emission as `hookSpecificOutput.additionalContext` which Claude Code's TUI prepends to the user message.
- Disable knobs: `TOKENPAK_COMPANION_ENABLED=0`, `TOKENPAK_COMPANION_ENRICH=0`.
- No per-request char / token logging in the proxy for the companion-added prefix.

**Severity:** High. The native Claude Code TUI sends a plain user prompt; the TokenPak-fronted version sends `prompt + capsule + vault_context`. Estimated **+100–500 tokens per request** (precise number depends on vault hit count + capsule length + companion budget).

### 1.3 Cache disruption

**Finding (PRIMARY hypothesis):** Companion-prepended dynamic content (vault results) varies per request and is positioned **before** the user prompt. Anthropic's prompt-prefix cache keys on the stable leading bytes, so vault results that change request-to-request invalidate the cache **every request**.

- `tokenpak/proxy/prompt_builder.py:194–204,317–440` — `apply_stable_cache_control` correctly inserts cache_control markers on stable system / tool blocks for non-byte-preserved adapters.
- `tokenpak/proxy/prompt_builder.py:70–98` — `_VOLATILE_PATTERNS` correctly classifies `<vault_context>` and `<TokenPak…>` as volatile.
- `tokenpak/proxy/adapters/anthropic_adapter.py:13–23` — AnthropicAdapter declares `tip.byte-preserved-passthrough`. The proxy CANNOT insert cache_control markers for Claude Code (PI-1 eligibility rule (g) hard-blocks).
- Companion-side context injection runs **pre-bytes**, sidestepping the byte-preservation rule but ALSO sidestepping the cache-boundary protection.

**Severity:** High. Even modest token amplification compounds when every request is a cache miss vs. native CLI which gets cache hits on the user's system prompt.

### 1.4 Companion-side model calls

**Finding (HYPOTHESIS RULED OUT):** The companion subsystem makes **zero** upstream API calls. All operations are pure local computation.

- `tokenpak/companion/mcp/tools.py:70–97` — `handle_estimate_tokens` and `handle_check_budget` are stubs returning hard-coded zeros.
- `tokenpak/companion/hooks/pre_send.py:121–193` — `_journal_write_savings` writes to local SQLite only.
- `tokenpak/companion/intent_injection.py:147–171` — `_check_privacy_guardrail` runs local regex.
- `tokenpak/companion/capsules/builder.py:1–150` — `build_from_messages` is heuristic regex extraction.
- `tokenpak/companion/hooks/pre_send.py:232–254` — vault search is local BM25 against `BlockStore`.

**Severity:** N/A — this hypothesis is closed. The companion does NOT make extra background API calls.

### 1.5 Concurrency model

**Finding (LOW-impact hypothesis):** Multiple process-wide locks exist but none demonstrably serialize the request path. The connection pool's per-netloc lock is the most likely contributor under high concurrency.

- `tokenpak/proxy/connection_pool.py:178–228` — single `threading.Lock` protecting the `_clients` dict. Default `max_connections=20` per provider via `TOKENPAK_POOL_MAX_CONNECTIONS`.
- `tokenpak/proxy/monitor.py:42–64` — bounded `Queue(maxsize=1000)` + `_DB_LOCK`. Off-path background drainer.
- `tokenpak/proxy/intent_prompt_patch_telemetry.py:101,132–162,193–212,219–243` — `IntentPatchStore._LOCK` (process-wide). PI-3 just landed; off-path for the request.
- `tokenpak/proxy/server.py:154–156,179–180,609,640,645,650` — `GracefulShutdown._lock`, `ProxyServer._session_lock`, `_last_lock`, `_compression_lock`.
- `tokenpak/proxy/server_async.py` — async server uses `asyncio.Semaphore(max_concurrency)` + `httpx.AsyncClient(max_connections=HTTPX_POOL_SIZE)`.

**Severity:** Low. The locks are short-held (dict lookups, queue puts). The connection pool's `max_connections=20` could become a bottleneck only at very high session counts, and httpx itself is thread-safe.

### 1.6 Credential routing

**Finding (PRIMARY hypothesis):** The proxy synthesizes **one stable `X-Claude-Code-Session-Id`** per proxy process and reuses it for every Claude Code request that flows through. Native Claude Code CLI rotates the session-id **per invocation**. Anthropic's billing pool routes on `X-Claude-Code-Session-Id`; a single id accumulating quota debt is plausibly the cause of earlier rate-limit hits.

- `tokenpak/services/routing_service/credential_injector.py:257–439` — `ClaudeCodeCredentialProvider` injection plan.
- `tokenpak/services/routing_service/credential_injector.py:232–244` — `_get_proxy_session_id()` generates **one UUID per proxy process** and reuses it under a module-level lock.
- `tokenpak/services/routing_service/credential_injector.py:388–396` — comment confirms the session-id is required for billing-pool routing AND that the proxy synthesizes a single id (vs CLI which sends per-invocation).
- OAuth token: same `~/.claude/.credentials.json` as native CLI.
- Beta header: `claude-code-20250219,oauth-2025-04-20` (matches CLI).
- User-Agent: `claude-cli/<version> (external, cli)` (matches CLI fingerprint).

**Severity:** High. Unique to TokenPak vs native CLI. Per-process session-id collapse means a proxy that runs for 8 hours of intermittent Claude Code work charges all that work to a single session-id, while a native CLI user opens N short-lived processes with N rotating ids.

### 1.7 Feature fail-safe / passthrough

**Finding (gap):** No code path detects upstream rate-limit pressure and disables companion features adaptively.

- `tokenpak/proxy/passthrough.py:1–84` — credential-forwarding utility only. Does not gate features.
- `tokenpak/proxy/degradation.py:61–150` — `DegradationTracker` records compression / failover events but does NOT disable features.
- `tokenpak/companion/hooks/pre_send.py:356–360,433–435` — `TOKENPAK_COMPANION_ENABLED` / `TOKENPAK_COMPANION_ENRICH` are static env-vars, not adaptive.
- No file under `tokenpak/services/routing_service/` reads 429 responses and adjusts companion behavior.

**Severity:** Medium. Once rate-limit pressure starts, nothing reduces TokenPak's contribution. The user has no automatic relief; they must manually unset the env-var or kill the proxy.

---

## 2. Hypothesis matrix

Ranked by likely contribution to the observed rate-limit-amplification effect. Each hypothesis pairs with the evidence we have, the evidence we still need, and the test that would settle it.

| ID | Hypothesis | Likely impact | Evidence we have | Evidence we still need | Settling test |
|---|---|---:|---|---|---|
| **H1** | **Cache prefix disruption.** Companion-prepended dynamic vault content invalidates Anthropic's prompt-prefix cache, so every request is a cache miss while the native CLI gets cache hits. | **HIGH** | `_VOLATILE_PATTERNS` classifies `<vault_context>` as volatile (proxy-side); companion runs pre-bytes; `cache_creation_tokens` likely high while `cache_read_tokens` likely low for TokenPak-fronted traffic. | Per-request `usage.cache_creation_input_tokens` vs `cache_read_input_tokens` for native vs TokenPak-fronted, same prompt, same session. | A/B test §5.1: identical prompt sequence, native vs TokenPak, compare cache hit ratio in `usage` field. |
| **H2** | **Session-id collapse.** Proxy sends one `X-Claude-Code-Session-Id` for hours; Anthropic attributes rate-limit consumption to that single session. Native CLI rotates per invocation. | **HIGH** | Code at `credential_injector.py:232–244` generates one UUID per proxy process. Comments at `:388–396` document the cause. | Anthropic's actual rate-limit-attribution dimension. (Likely undocumented; can be inferred from response headers.) | Test §5.2: spawn 5 concurrent CLIs through proxy (one session-id) vs 5 native CLIs (5 session-ids); compare 429 onset. |
| **H3** | **Token amplification (additive).** Vault + capsule prepend adds ~100–500 tokens per request, directly increasing `input_tokens` quota debt. | **MEDIUM** | `_query_vault_context(prompt, budget_chars)` and `_load_active_capsule` measured at the source; not currently logged. | Per-request char counts for the companion-added prefix vs the user prompt. | Test §5.3: emit a one-line trace for every pre-send hook call with `companion_added_chars`; compare against native baseline. |
| **H4** | **Retry-After ignored.** Proxy retries before Anthropic's quoted backoff window, multiplying 429s. | **MEDIUM** | `failover_engine.py:42` hard-codes 2 s; no `Retry-After` parser in the request path. | A captured `Retry-After: N` response from Anthropic showing the proxy retrying before deadline. | Test §5.4: introspect `tokenpak/proxy/server.py` request-failure path; assert Retry-After is parsed (currently isn't). |
| **H5** | **Failover storm.** Failover engine retries on the same provider then switches; under transient pressure this could double-count requests. | **LOW** | `MAX_RETRY_SAME_PROVIDER = 1` (one retry, then switch); not unbounded. | Whether the 2 s retry actually fires on 429 or only on 5xx. | Test §5.5: trace through `failover_engine.py` for a 429 input; assert retry count. |
| **H6** | **Connection pool bottleneck.** Single `_lock` per netloc serializes client lookups under high concurrency. | **LOW** | `connection_pool.py:178–228` has one lock; default `max_connections=20`. | A load test showing client lookup latency rises under concurrency. | Test §5.6: 50 concurrent requests through the proxy; measure `_get_client` lock acquire time. |
| **H7** | **SQLite write contention.** `IntentPatchStore._LOCK` + `Monitor` queue could backpressure if writes block. | **LOW** | Both writes are off-path (telemetry + intent patches), not in the request critical path. | Confirmation that monitor writes are non-blocking. | Test §5.7: pause monitor drain; verify request latency is unchanged. |
| **H8** | **Companion-side model calls.** Hooks / summarizers / pruners make extra Claude API calls behind the user's back. | **RULED OUT** | All companion code paths are local (vault search, capsule load, journal writes, regex). MCP tools are stubs. | — | Closed. |

**Probable dominant factors:** **H1 + H2** are the strongest candidates. H3 (token amplification) is real but may explain only the magnitude, not the timing. H4 is a secondary amplifier. H6 / H7 are unlikely to be primary causes.

---

## 3. Evidence we already have

### 3.1 From standing memory + prior incidents

- **2026-04-24** Codex / OAuth bucket isolation incident (memory `project_tokenpak_codex_wired`): `X-Claude-Code-Session-Id` is required for Anthropic billing-pool routing. Proxy v1.3.17 fixed an earlier bug by injecting it. The current implementation collapses to one id per proxy process — fixing one bucket bug created a different one (H2).
- **2026-04-13** byte-fidelity proxy architecture (memory `project_tokenpak_claude_code_proxy`): the proxy is forced into byte-preserved passthrough for Claude Code. This is *why* cache-boundary insertion can't happen at the proxy level (and *why* the companion exists — to do the work pre-bytes).
- **PI-1 eligibility rule (g)** (`tokenpak/proxy/intent_prompt_patch.py:476–480`): byte-preserved adapters block patches except when `target = companion_context`. PI-3 honours this. The same rule will need extension for cache-prefix-preserving companion injection (H1's fix in NCP-2).
- **2026-04-08** TokenPak Claude Code integration initiative (memory `project_tokenpak_claude_code_integration`): the workstream that built the current Claude Code path. Six profiles + auto-detection + multi-provider routing all landed without parity instrumentation.

### 3.2 From the diagnostic exploration (§1)

Concrete file:line evidence for every hypothesis above, captured at `main` commit `c2786269737`. The evidence is reproducible — re-running the same `grep` / `Read` queries on a fresh checkout yields the same locations.

---

## 4. Evidence still needed

For each primary hypothesis, the next concrete data point we need before NCP-1 implementation begins:

| Hypothesis | Evidence we need | How to get it |
|---|---|---|
| **H1** (cache disruption) | `usage.cache_creation_input_tokens` and `cache_read_input_tokens` from Anthropic responses, on identical prompt sequences, native vs TokenPak-fronted. | Run the §5.1 A/B test; capture the `usage` block from streaming + non-streaming responses; compute `cache_read / (cache_read + cache_creation)` ratio. |
| **H2** (session-id collapse) | The 429 onset point (request count) for: (a) one TokenPak-fronted process running N concurrent sessions; (b) N native CLI processes each running 1 session. | Run the §5.2 A/B test; watch request count when the first 429 fires. |
| **H3** (token amplification) | Per-request `companion_added_chars` for a typical work session. | Add a one-line `logger.debug` print in `pre_send.py` (NCP-1 deliverable; do NOT add it in NCP-0). For NCP-0, infer from a manual trace. |
| **H4** (Retry-After) | A captured `Retry-After: N` response and the next request's send timestamp. | Run §5.4 — saturate the upstream until a 429 fires; trace the proxy retry timing. |

---

## 5. Recommended A/B test methodology

### 5.1 Cache hit ratio test (settles H1)

**Goal:** Measure the cache-read / cache-creation ratio under identical workload.

**Setup:**
- Same Anthropic OAuth token.
- Same model (`claude-3-5-sonnet-20241022` or current default).
- Same conversation seed (one fixed system prompt + one fixed user message).
- Same wall-clock time window.

**Variant A — native:**
- Spawn `claude` CLI directly. No TokenPak in the path.
- Issue 10 sequential identical prompts in one session.
- Capture each response's `usage` field.

**Variant B — TokenPak-fronted:**
- Same `claude` CLI, but with the TokenPak proxy in `ANTHROPIC_BASE_URL`.
- Companion enabled (default).
- Issue the same 10 sequential identical prompts.
- Capture each response's `usage` field.

**Metric:** `sum(cache_read_input_tokens) / sum(cache_read_input_tokens + cache_creation_input_tokens)`.

**Hypothesis test:** If H1 is correct, Variant A's ratio approaches 0.9+ after the first request; Variant B's ratio stays near 0.0 because every request has different vault content.

### 5.2 Session-id rotation test (settles H2)

**Goal:** Measure 429 onset under N concurrent sessions when session-id is collapsed vs rotated.

**Setup:** Same model, same OAuth, same prompt template (1 KB prompt, 5 KB expected output).

**Variant A — N native CLIs:**
- N parallel `claude` CLI invocations, each its own process.
- Each invocation generates its own `X-Claude-Code-Session-Id`.
- Loop: each session sends a request every 5 seconds for 10 minutes.
- Record: request count when the first 429 fires per session.

**Variant B — N TokenPak-fronted CLIs:**
- N parallel `claude` CLIs all pointed at the **same proxy process**.
- Proxy synthesizes one shared `X-Claude-Code-Session-Id`.
- Same loop / same timing.
- Record: request count when the first 429 fires (per session, but they share the bucket).

**Hypothesis test:** If H2 is correct, Variant B hits 429 at request count `≈ R/N` where R is Variant A's per-session 429 onset. If session-id is NOT the rate-limit dimension, Variant A and B fire at the same total request count.

### 5.3 Token-amplification trace (informs H3)

**Goal:** Measure the typical companion-added-chars distribution.

**Setup:** Run a normal day's worth of Claude Code work through the proxy, with an instrumentation patch (NCP-1 deliverable; not in NCP-0) that emits one log line per pre-send hook call:

```
tokenpak.companion.pre_send: prompt_chars=N capsule_chars=M vault_chars=V intent_chars=I total_added=M+V+I
```

Aggregate over 1 day; compute mean / median / p95 / p99 of `total_added`.

**Hypothesis test:** If `total_added` p95 > 500 chars (~125 tokens), H3 is a real contributor and worth fixing. If p95 < 100 chars, H3 is negligible.

### 5.4 Retry-After honour test (settles H4)

**Goal:** Verify whether the proxy waits `Retry-After` before retrying.

**Setup:**
- Saturate the upstream until 429 fires (any reasonable load gen).
- Capture the 429 response headers (`Retry-After: <N>`).
- Note the timestamp.
- Capture the next outbound request from the proxy (via `tokenpak/proxy/server.py` request log).
- Compute: `next_request_timestamp - 429_response_timestamp`.

**Hypothesis test:** If the delta is `< Retry-After` value, the proxy is ignoring the header. (Code-path inspection at `failover_engine.py:42` already shows it uses a fixed 2 s; this test merely confirms in production.)

### 5.5 Failover storm test (settles H5)

**Goal:** Verify whether the proxy retries the same provider on 429 (and how many times).

**Setup:** Inject a synthetic 429 from a test fixture pointed at the proxy. Trace the proxy's outbound request sequence.

**Hypothesis test:** If `MAX_RETRY_SAME_PROVIDER = 1` is honoured, exactly two outbound requests fire (original + 1 retry on the same provider), then the proxy switches. If more retries fire, H5 is a contributor.

### 5.6 Connection pool contention test (settles H6)

**Goal:** Measure `_get_client` lock acquire latency under concurrency.

**Setup:** 50 concurrent requests through the proxy. Instrument `connection_pool.py:_get_client` with timing (NCP-1 deliverable).

**Hypothesis test:** If p99 lock acquire time > 5 ms under 50 concurrent requests, the pool is a bottleneck. If p99 < 1 ms, H6 is negligible.

### 5.7 SQLite contention test (settles H7)

**Goal:** Verify monitor / intent-patch writes don't block the request path.

**Setup:** Saturate writes to `monitor.db` (e.g. by running 100 requests in 1 second); measure request latency over the same window.

**Hypothesis test:** If request latency increases proportional to monitor write rate, H7 is a contributor. If latency is independent, H7 is closed.

---

## 6. Recommended unit + integration tests (when implementation begins)

NCP-0 is diagnostic-only; this section is forward-looking — what tests should land **with** each NCP-x implementation phase.

### 6.1 NCP-1 (measurement)

- `tests/test_parity_metrics_phase_ncp_1.py`:
  - Every metric in standard #24 §3 is emitted on every request (mock upstream).
  - `tokenpak doctor --parity` renders without crashing on empty telemetry.
  - `tokenpak doctor --parity --json` parses + has the expected schema.
  - Privacy contract: no raw prompt content in any metric value.

### 6.2 NCP-2 (cache prefix preservation)

- `tests/test_parity_cache_prefix_phase_ncp_2.py`:
  - Companion injects vault content **after** the cache boundary on cache-aware adapters.
  - For `tip.byte-preserved-passthrough` adapters, the companion-added prefix is wrapped in a cache-key-stable boundary.
  - Two consecutive requests with the same user prompt + different vault hits MUST yield the same cached prefix bytes.
  - Privacy: vault content still doesn't leak through the cache key.

### 6.3 NCP-3 (session-id rotation)

- `tests/test_parity_session_id_phase_ncp_3.py`:
  - Default rotation policy emits a fresh session-id per CLI invocation when invocation boundary can be detected.
  - Where invocation boundary cannot be detected, rotation fires every K requests or T seconds (defaults TBD).
  - Existing OpenClaw-shape callers (which must keep one stable id for billing) opt out via explicit config.
  - The native-CLI rotation cadence is documented in the test as the parity target.

### 6.4 NCP-4 (fail-safe circuit breaker)

- `tests/test_parity_fail_safe_phase_ncp_4.py`:
  - Circuit breaker trips on 3 × 429 in 60 s.
  - Tripped state disables vault / capsule / intent injection for 120 s.
  - User prompt still passes through unchanged.
  - Recovery after 120 s of clear traffic.
  - Hysteresis: a single 429 during recovery does NOT re-trip immediately.
  - Trace marker `tokenpak-trace: parity-degraded` present on degraded requests.

### 6.5 NCP-5 (Retry-After honour)

- `tests/test_parity_retry_after_phase_ncp_5.py`:
  - Upstream `Retry-After: N` parsed and stored on the trace.
  - No retry fires before `<original_send_ts> + N` seconds.
  - Failover-engine retry path honours the header.
  - 429 → 200 silent retry NEVER happens (the client always sees the 429).
  - `anthropic-ratelimit-*` headers forwarded unchanged.

### 6.6 NCP-6 (parity report CLI + dashboard)

- `tests/test_parity_report_phase_ncp_6.py`:
  - `tokenpak parity report --window Nd` renders without crash.
  - `--json` parses + matches schema.
  - Dashboard panel renders without console errors.
  - Empty telemetry yields a clean "no data" state.

---

## 7. Reproducibility checklist

Before NCP-1 implementation begins, the operator running the diagnostic SHOULD be able to:

- [ ] Run §5.1 (cache hit ratio) and capture results for native vs TokenPak-fronted.
- [ ] Run §5.2 (session-id collapse) and capture 429 onset.
- [ ] Manually inspect `pre_send.py` log output to estimate H3 magnitude.
- [ ] Confirm via `failover_engine.py` source that H4 is real.
- [ ] Re-run the diagnostic exploration on a fresh `main` checkout and reproduce the §1 file:line citations.

The standing rule is: **no implementation lands until at least H1 and H2 are settled by the §5 tests.** This avoids fixing a hypothesis that turns out not to be the dominant cause.

---

## 8. Out of scope for NCP-0

To keep the diagnostic phase tight:

- ❌ **Implementing measurement.** No `tokenpak doctor --parity` CLI in NCP-0; that's NCP-1.
- ❌ **Implementing fail-safe.** No circuit breaker in NCP-0; that's NCP-4.
- ❌ **Changing companion behavior.** Vault / capsule / intent injection unchanged.
- ❌ **Changing proxy behavior.** Failover engine, connection pool, retry layer unchanged.
- ❌ **Changing credential routing.** Session-id collapse remains; NCP-3 is the fix.
- ❌ **Adding new env-vars.** No new toggles introduced in NCP-0.
- ❌ **Modifying tests.** No new test files in NCP-0.
- ❌ **Touching dashboard / web surfaces.** UI is NCP-6.
- ❌ **Other interactive clients (Codex, Cursor).** NCP-0 scopes Claude Code only; the standard generalizes.

NCP-0 is **strictly documentation + evidence**. The branch landing this diagnostic plan + the standard proposal MUST contain zero changes under `tokenpak/` or `tests/`.

---

## 9. Cross-references

- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — companion standard proposal (the invariants + measurement contract this diagnostic measures against)
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — capability-gated middleware activation (the pattern NCP-x will extend with `tip.parity.native-client-v1`)
- `tokenpak/companion/hooks/pre_send.py` — primary I-1 / I-2 surface
- `tokenpak/proxy/adapters/anthropic_adapter.py` — byte-preserved-passthrough declaration
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — the I-3 surface
- `tokenpak/proxy/failover_engine.py` — the I-4 surface (Retry-After)
- `tokenpak/proxy/connection_pool.py` — the H6 surface (concurrency)
- `tokenpak/proxy/intent_prompt_patch_telemetry.py` — the H7 surface (SQLite write locks; PI-3 just landed)

---

## 10. Acceptance criteria

NCP-0 is **complete** when:

- [x] The standard proposal document exists at `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`.
- [x] This diagnostic plan exists at `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`.
- [x] The hypothesis matrix (§2) covers the seven directive inspection categories.
- [x] Recommended A/B tests (§5) are concrete and runnable.
- [x] Recommended unit tests (§6) align with the standard's measurement contract.
- [x] No runtime / classifier / routing behavior changes proposed.
- [ ] CI green on the closeout PR.

After NCP-0 is closed, the next step is the operator running the §5.1 + §5.2 tests and reporting back. NCP-1 implementation does not begin until those results are in.
