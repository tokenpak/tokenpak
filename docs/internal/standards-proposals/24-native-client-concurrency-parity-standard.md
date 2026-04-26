# Standard #24 (proposal) — Native Client Concurrency Parity

**Status**: 🟡 **proposal** — awaiting Kevin ratification
**Date**: 2026-04-26
**Workstream**: NCP (Native Client Concurrency Parity) diagnostic
**Authors**: Sue (draft) / Kevin (review)
**Canonical home (when ratified)**: `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/24-native-client-concurrency-parity-standard.md`
**Companion diagnostic plan**: `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`

> This is a **proposal**. Until ratified, no code path is required to honour it. Once ratified, the standard is promoted to the vault and binds every TokenPak surface that fronts an interactive client (Claude Code, Codex, Cursor, etc.).

---

## 0. Premise

When TokenPak fronts an interactive client (Claude Code Companion through TokenPak; future: Codex, Cursor, etc.), users observe upstream rate-limit behavior **earlier** than they would when running the same client natively. This standard codifies the invariants that prevent TokenPak from becoming the **rate-limit amplifier** in any user's stack.

The principle: *a TokenPak-fronted client must reach upstream rate limits at the same wall-clock rate as the same client running natively against the same account, under the same workload.* When parity cannot be achieved, the deviation must be **documented**, **opt-in**, and **measurable**.

This standard does NOT prescribe specific implementations. It pins the **invariants**, the **measurement surfaces**, and the **fail-safe contract** that any TokenPak surface fronting a native interactive client must satisfy.

---

## 1. Definitions

- **Native client baseline**: the same client (e.g. `claude` CLI) running directly against the upstream provider, without TokenPak in the path.
- **TokenPak-fronted equivalent**: the same client running through TokenPak — Companion, proxy, or any combination.
- **Rate-limit budget**: the upstream provider's per-account / per-session / per-IP quota that throttles request rate or token throughput.
- **Quota debt**: the cumulative consumption a request charges against the budget, including (a) input tokens, (b) output tokens, (c) request count, (d) any provider-specific dimension.
- **Cache prefix**: the leading bytes of a request that match a previously-issued request and are eligible for the upstream provider's prompt-prefix cache.
- **Session bucket**: the rate-limit attribution dimension keyed by the provider's session-id-equivalent header (e.g. `X-Claude-Code-Session-Id`).
- **Concurrency parity**: the property that N concurrent native sessions and N concurrent TokenPak-fronted sessions, doing the same work, hit upstream rate limits at the same time.

---

## 2. Invariants

The five invariants every TokenPak surface fronting a native client MUST honour. Numbered for citation in PRs and bug reports.

### Invariant I-1 — Token amplification is bounded and disclosed

For every request flowing through a TokenPak surface:

- The total input-token amplification (companion-added context + vault injection + capsule injection + intent guidance + any other proxy-side or companion-side prepend / append) **MUST be measured** and **MUST be disclosed** to the operator on demand.
- The amplification ratio (TokenPak input tokens / native input tokens) **MUST NOT exceed 1.5×** under normal operation, with a hard cap of **2.0×** before the surface declares itself "amplifying" and degrades.
- The disclosure surface MUST be `tokenpak doctor --parity` (or equivalent) and MUST report the rolling-window amplification ratio per surface (companion / vault / capsule / intent).

### Invariant I-2 — Cache prefix preservation

For every adapter that declares `tip.byte-preserved-passthrough`:

- TokenPak surfaces MUST NOT insert dynamic content into the **stable cached prefix** of the request (system block / leading message). Dynamic content (vault search results, capsule loads, intent guidance) MUST be appended **after** the cache boundary, OR be wrapped in a stable cache-key boundary that the provider honours.
- Companion-side context injection that runs **pre-bytes** (before the proxy) is exempt from the proxy's byte-preservation guarantee but is **still subject to I-1**.
- The cache hit ratio for TokenPak-fronted requests MUST be measurable per surface.

### Invariant I-3 — Session bucket parity

When fronting a client whose upstream provider attributes rate limits per session-id (or equivalent dimension):

- TokenPak surfaces MUST NOT collapse multiple native invocations onto a **single** session-id by default. The session-id MUST either: (a) be passed through from the native client unchanged, or (b) be rotated on a per-invocation / per-time-window basis.
- A long-lived proxy process that synthesizes one session-id and reuses it for hours / days **violates** this invariant.
- Where session-id pass-through is impossible (the native client doesn't emit one), the surface MUST rotate at a frequency that approximates the native client's natural rotation cadence.

### Invariant I-4 — Provider rate-limit signals honoured

For every upstream response that carries a rate-limit signal:

- `Retry-After` headers MUST be parsed, stored on the request trace, and honoured by any retry layer (proxy-internal failover OR companion-side retry).
- `anthropic-ratelimit-*` (and equivalent provider headers) MUST be forwarded to the client unchanged.
- TokenPak surfaces MUST NOT issue a retry that arrives at the upstream **earlier** than the `Retry-After` deadline.
- TokenPak surfaces MUST NOT mask rate-limit signals from the client (no swallowing 429 → 200 by silent retry).

### Invariant I-5 — Feature fail-safe

When upstream rate-limit headroom is **low** (heuristic: ≥ N 429s in the last W seconds, or `anthropic-ratelimit-tokens-remaining` below threshold):

- TokenPak surfaces MUST be able to **degrade gracefully** by disabling optional companion features (vault injection, capsule injection, intent guidance, pre-send hook enrichment) on subsequent requests.
- The degradation MUST be observable in `tokenpak doctor --parity` and in the request trace.
- The degradation MUST NOT alter the request's user-visible content (the user prompt MUST still go through unchanged); only TokenPak-added enrichment is dropped.
- After H seconds of clear traffic, the surface MAY re-enable features (hysteresis).

---

## 3. Measurement contract

Every TokenPak surface fronting a native client MUST report the following metrics per request (logged to telemetry, surfaced via `tokenpak doctor --parity`):

| Metric | Type | Source |
|---|---|---|
| `request_count` | counter | proxy + companion |
| `retry_count` | counter | proxy retry layer |
| `429_count` | counter | proxy response classification |
| `5xx_count` | counter | proxy response classification |
| `latency_ms` | histogram | proxy outer wrap |
| `time_to_first_token_ms` | histogram | proxy stream layer |
| `input_tokens` | gauge per req | upstream `usage` field |
| `output_tokens` | gauge per req | upstream `usage` field |
| `cache_creation_tokens` | gauge per req | upstream `usage` field |
| `cache_read_tokens` | gauge per req | upstream `usage` field |
| `companion_added_chars` | gauge per req | companion pre-send hook |
| `companion_added_tokens_est` | gauge per req | companion pre-send hook |
| `vault_injection_chars` | gauge per req | vault retrieval |
| `capsule_injection_chars` | gauge per req | capsule loader |
| `intent_guidance_chars` | gauge per req | PI-3 application library |
| `hook_triggered_calls` | counter | hook dispatcher |
| `extra_background_calls` | counter | non-user-initiated upstream calls (MUST be zero for native-parity surfaces) |
| `retry_after_seconds` | gauge per 429 | upstream response header |
| `ratelimit_tokens_remaining` | gauge per req | upstream response header |
| `ratelimit_requests_remaining` | gauge per req | upstream response header |
| `session_id` | label | injected session-id at the wire |
| `session_id_rotations_per_hour` | gauge | session-id-rotation logic |

The metrics MUST be available as both:

- A live snapshot via `tokenpak doctor --parity [--json]`.
- A windowed report via `tokenpak parity report --window Nd [--json]` (exact CLI surface to be designed in NCP-1+).

---

## 4. Fail-safe contract (I-5 expanded)

The fail-safe behavior MUST be:

1. **Detection**: a circuit breaker that trips when **either** (a) the proxy has seen ≥ 3 429s in the last 60 seconds for a given upstream, OR (b) the most-recent response carried `anthropic-ratelimit-tokens-remaining` below 10% of the per-minute cap.
2. **Action on trip**: subsequent requests through the same TokenPak surface within the next **120 seconds** SHOULD have these features disabled:
   - Vault injection (`<vault_context>` block omitted)
   - Capsule injection
   - Intent guidance (PI-3 `inject_guidance` mode)
   - Companion pre-send hook enrichment
3. **What MUST NOT be dropped**:
   - The user's prompt (always passes through verbatim)
   - Credential injection (the request still needs to authenticate)
   - Required adapter capability headers (the request still needs to route)
4. **Recovery**: after **120 seconds** without a 429, the breaker resets and features re-enable.
5. **Disclosure**: every degraded request MUST carry a trace marker (e.g. `tokenpak-trace: parity-degraded`) so the operator can inspect via `tokenpak doctor`.

The exact thresholds (3 / 60 / 120 / 10%) are defaults; the surface MAY expose them as host-configurable knobs in `~/.tokenpak/policy.yaml` under a new `parity_fail_safe` block.

---

## 5. What this standard does NOT mandate

To avoid scope creep:

- ❌ **Specific implementation** of session-id rotation. Rotate per-invocation, per-time-window, or per-N-requests — the standard pins the **outcome** (no quota debt accumulation on a single id), not the mechanism.
- ❌ **Hard ceiling on companion features**. Vault, capsules, intent guidance can ALL stay enabled at full tilt as long as I-1 (amplification bounded), I-2 (cache prefix preserved), and I-5 (fail-safe wired) hold.
- ❌ **Breaking changes to existing config**. Today's `TOKENPAK_COMPANION_ENRICH = 0` env-var path stays valid. The standard adds a new parity-aware fail-safe; it does not deprecate the existing knobs.
- ❌ **Proxy-side mutation of byte-preserved adapters** to satisfy I-2. The companion runs pre-bytes; that's where I-2 is enforced. The proxy's byte-preservation guarantee is preserved by construction.
- ❌ **Rate-limit prediction** (e.g. "decline this request because we expect a 429"). The standard pins **reactive** fail-safe; predictive admission control is a future ratification.

---

## 6. How a surface conforms

A TokenPak surface (companion / proxy / future Codex companion / future Cursor companion) is "Native Client Parity conformant" when:

1. It declares conformance via a manifest entry (e.g. `tip.parity.native-client-v1` capability).
2. Every metric in §3 is emitted on every request (or a documented subset for surfaces that don't see every dimension).
3. The fail-safe contract in §4 is wired and tested under load.
4. The five invariants in §2 are pinned by tests in CI.
5. The host-facing operator surface (`tokenpak doctor --parity` or equivalent) renders the conformance state.

A surface that **cannot** satisfy a specific invariant (e.g. a future BedrockAdapter that has no equivalent of `Retry-After`) MUST document the deviation explicitly in its standard manifest, and the surface declares "partial-parity" instead of "parity".

---

## 7. Standards adjacency

Existing standards this builds on:

- **`23-provider-adapter-standard.md` §4.3** — capability-gated middleware activation. The new `tip.parity.native-client-v1` capability follows the same pattern.
- **`01-architecture-standard.md` §5.1** — byte-fidelity rule. I-2 (cache prefix preservation) is the parity-side cousin of byte-fidelity.
- **`02-code-standard.md`** — code style for any new modules implementing parity surfaces.
- **`09-audit-rubric.md`** — the audit checklist gains a "native-client-parity" row at the next ratification.

This standard does NOT replace or supersede any existing standard. It adds a new dimension that overlays the existing 23.

---

## 8. Roadmap (non-normative)

Once this standard is ratified, the implementation work falls into the NCP series:

| Phase | Scope |
|---|---|
| **NCP-1** | Measurement implementation. Wire every metric in §3. `tokenpak doctor --parity`. No behavior change. |
| **NCP-2** | Cache-prefix-preserving companion injection. Fix the I-2 deviation that the diagnostic identified (vault content varying per request → cache miss every request). |
| **NCP-3** | Session-id rotation strategy. Fix the I-3 deviation (proxy collapses many invocations onto one id). |
| **NCP-4** | Fail-safe circuit breaker. Wire I-5. |
| **NCP-5** | `Retry-After` honour layer. Fix the I-4 deviation. |
| **NCP-6** | `tokenpak parity report` CLI + dashboard panel. |

Each NCP-x requires its own ratification cycle; this standard does not pre-authorize any of them.

---

## 9. Acceptance check (for a future ratification)

The standard is ratified when:

- [ ] Kevin signs off on the five invariants (§2) and the fail-safe contract (§4).
- [ ] A copy lands at `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/24-native-client-concurrency-parity-standard.md` (canonical home).
- [ ] The `09-audit-rubric.md` standard gains a new row referencing this one.
- [ ] The diagnostic report (`docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`) is referenced from the §1 of this standard.
- [ ] An NCP-1 directive (or equivalent) opens the implementation work.

Until those five gates close, this document remains a **proposal**. No surface is required to honour it.

---

## 10. Cross-references

- `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md` — companion diagnostic plan + hypothesis matrix + recommended tests
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — adapter capability standard this builds on
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/09-audit-rubric.md` — audit checklist (will gain a parity row at ratification)
- `tokenpak/proxy/adapters/anthropic_adapter.py` — current AnthropicAdapter (declares `tip.byte-preserved-passthrough`; would also declare `tip.parity.native-client-v1` once NCP-1 lands)
- `tokenpak/companion/hooks/pre_send.py` — companion pre-send enrichment (the primary I-1 / I-2 surface to instrument)
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — the I-3 surface (session-id collapse)
