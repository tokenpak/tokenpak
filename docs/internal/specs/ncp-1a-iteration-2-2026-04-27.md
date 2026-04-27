# NCP-1A iteration 2 — concurrent-TokenPak degradation observed

**Date**: 2026-04-27
**Status**: 🟡 **superseded by iteration-3** — see banner below
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Standard proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - Iteration 1 (1v1 baseline + ABCD plan): `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md`
  - NCP-3 diagnostic plan: `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`
  - **Iteration 3 (2 TP retry + 1 native healthy)**: `docs/internal/specs/ncp-1a-iteration-3-2026-04-27.md`

> ⚠️ **Iter-3 update (2026-04-27)**: stronger evidence captured — 2 TP sessions retried while 1 native session ran healthily *at the same time*. Generic account quota ruled out as sole cause. Hypothesis priority promoted: H2 / H9b / H4 / H9a-c. See iteration-3 doc for the cumulative ranking and the receiving form for the pending harness output.

> ⚠️ **Iter-4 update (2026-04-27)**: retry localized to **post-tool-result continuation** phase. Five new diagnostic dimensions specified; existing telemetry insufficient. Recommended next phase: **NCP-3I** (in-proxy instrumentation). See `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md`.

> **Headline:** the multi-concurrent-TokenPak regime reproduces the product issue. **Two TokenPak Claude Code sessions running concurrently degrade**, while a single TokenPak session beside a single native session is parity (iter-1). A previous run showed TokenPak displaying "Retrying in 20s" while native completed normally. The 1v1 → 2-on-2-TP comparison alone is enough to narrow the investigation; we proceed to NCP-3 diagnostic without waiting for C/D.

---

## 1. Iteration 2 results table

| Test | Variant | N | Stagger | Result | Source |
|---|---|---:|---|---|---|
| **A** | 1 TP + 1 native, run side-by-side | 1+1 | none | **parity** (TP ~1m09s ≈ native ~1m07s, 0 retries) | iter-1 doc + operator confirmation 2026-04-27 |
| **B** | 2 TP sessions, no native | 2 | none | **degraded** — retry/delay behavior; "much slower than a single native session on the same prompt" | operator screenshots + manual observation 2026-04-27 |
| **C** | 2 native sessions, no TP | 2 | none | **pending** — not yet captured | — |
| **D** | 3 TP sessions, staggered 20 s | 3 | 20 s | **pending** — not yet captured | — |

**Supporting evidence (anecdotal but corroborating):**

- An earlier run showed TokenPak surfacing "**Retrying in 20s**" while a parallel native session completed normally. (Operator-reported, 2026-04-27.)
- Latest screenshots support the **single-lane / funneling** interpretation: concurrent TokenPak sessions appear to interfere with each other, while native does not show comparable degradation under the smaller comparison.

The directive instructs: do **not** fabricate C/D. Mark them pending and proceed.

---

## 2. What A + B alone establish

### 2.1 What is NOW known (post-iter-2)

1. **The product issue is concurrency-shaped on the TokenPak side.** Single-session is parity; concurrent-TokenPak degrades. This eliminates the possibility that the degradation is constant overhead on every TokenPak request (which would have shown at N=1).
2. **The degradation is TokenPak-specific (probable, pending C).** A native CLI completed normally beside a degraded TokenPak run. We don't yet have the C result (2 native sessions concurrently) to fully rule out an Anthropic-side concurrency limit, but the side-by-side observation is consistent with a TokenPak-specific cause.
3. **TokenPak's own retry layer is firing.** The "Retrying in 20s" message is rendered by the TokenPak path, not by the native CLI. Per H4 (retry amplification under concurrency), this is direct evidence the proxy is retrying — not just passing through native CLI retries.

### 2.2 What is NOT yet settled

1. **Cause-discrimination within the H9 family.** Test D (staggered starts) is the H2 vs H9b disambiguator. Without it, we can't yet say whether the concurrency failure is purely session-id collapse (H2) or specifically OAuth-refresh thundering herd (H9b) or some other shared-lane mechanism (H9a/H9c/H9d).
2. **Anthropic-side concurrency limit (C).** Until C is run, we cannot fully eliminate the possibility that 2 concurrent CLI sessions ALSO degrade native — just less visibly. C would close that loop.

---

## 3. Updated hypothesis priority (iter-2)

The directive's revised priority order, applied to the matrix:

| Rank | Hypothesis | Status post-iter-2 |
|---:|---|---|
| **1** | **H2** session/session-id/lane collapse | **HIGH** — strongest candidate; matches "single lane / funneling" observation; the proxy's process-stable `X-Claude-Code-Session-Id` is exactly what a "single lane" looks like to Anthropic |
| **2** | **H9** TokenPak shared-lane contention | **HIGH** — sub-rank: H9b OAuth refresh lane > H9a pool lock > H9c rotation lock > H9d telemetry lane |
| **3** | **H4** retry amplification under concurrency | **HIGH (confirmed evidence)** — "Retrying in 20s" came from the TokenPak side, not the CLI |
| **4** | H5 / H7 — proxy queueing / shared lock / SQLite contention | LOW–MEDIUM |
| **5** | H1 / H3 — cache / context overhead | SECONDARY (demoted iter-1, unchanged iter-2) |

Note: H8 (companion-side model calls) remains **ruled out** from NCP-0.

---

## 4. Why we proceed to NCP-3 diagnostic now (not waiting for C/D)

The directive explicitly authorizes proceeding because A + B already justify narrowing the investigation:

- **A** establishes TokenPak per-request overhead is bounded (parity at N=1).
- **B** establishes TokenPak concurrency-shape is degraded.
- C and D would refine the cause-of-cause (which sub-mechanism within shared-lane), but they don't change the conclusion that TokenPak shared-lane behavior is the load-bearing fault.

Therefore the next NCP work is the **NCP-3 diagnostic phase** — characterizing the TokenPak-side shared-lane behavior with measurement-only instrumentation. The plan + initial findings live in `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`.

---

## 5. Status snapshot

| Item | State |
|---|---|
| A (1v1 parity) | ✅ recorded (iter-1 + iter-2 confirms) |
| B (2 TP concurrent) | ✅ recorded — **degraded** |
| C (2 native concurrent) | ⏸️ pending operator |
| D (3 TP staggered) | ⏸️ pending operator |
| Anecdotal "Retrying in 20s" evidence | ✅ recorded |
| Hypothesis priority (iter-2) | ✅ updated — H2 / H9 / H4 confirmed top three |
| NCP-3 diagnostic plan | ✅ landed (companion doc) |
| C/D execution | ⏸️ pending operator (still useful for cause-of-cause discrimination) |
| Behavior fixes | ⛔ frozen per directive |

After NCP-3 diagnostic produces its first trace report, the synthesis decision (in the NCP-3 doc §6) will route to one of:

- **NCP-3A** — session-id / lane preservation fix (if H2 dominant)
- **NCP-4** — retry amplification fix (if H4 dominant)
- **NCP-9** — OAuth refresh lane fix (if H9b dominant)
- **NCP-1C** — more operator data if still inconclusive

---

## 6. Cross-references

- `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md` — A/B/C/D plan + 1v1 baseline (iter-1)
- `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md` — NCP-3 diagnostic plan + trace methodology (iter-2 successor)
- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — Standard #24 (NCP-1R revision); I-0 / I-3 / I-6 invariants
- `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md` — NCP-0 hypothesis matrix + H9 added in iter-1
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — H2 / H9b / H9c surface
- `tokenpak/proxy/connection_pool.py` — H9a surface
- `tokenpak/proxy/failover_engine.py` — H4 surface
- `tokenpak/proxy/intent_prompt_patch_telemetry.py` + `tokenpak/proxy/monitor.py` — H9d surface
