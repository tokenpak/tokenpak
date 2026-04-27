# NCP-1A iteration 3 — 2-TP retrying while 1-native healthy

**Date**: 2026-04-27
**Status**: 🟡 **superseded by iteration-4** — see banner below
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Iteration 1 (1v1 baseline): `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md`
  - Iteration 2 (2-TP concurrent degraded): `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md`
  - NCP-3 diagnostic plan: `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`
  - Standard #24 proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - **Iteration 4 (post-tool-result retry localization)**: `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md`

> ⚠️ **Iter-4 update (2026-04-27)**: retry message localized to **post-tool-result model continuation** (not initial dispatch). Five new diagnostic dimensions added; existing telemetry insufficient; recommended next phase is **NCP-3I** (in-proxy instrumentation). H4 promoted to #1; H3 (token amplification) promoted MEDIUM → MEDIUM-HIGH for the post-tool-result phase specifically.

> **Headline:** 2 concurrent TokenPak Claude Code sessions visibly retried while 1 native Claude Code session ran healthily *at the same time*. This is the strongest evidence to date that the failure mode is **TokenPak-internal**, not generic Anthropic account saturation. Promotes H2 / H9b / H4 / H9a-c. Demotes generic-quota and cache-prefix-only explanations.

---

## 1. Operator-supplied result

| Slot | Variant | Behavior |
|---|---|---|
| Left | TokenPak Claude Code | retrying |
| Middle | TokenPak Claude Code | retrying |
| Right | Native Claude Code (no TokenPak in path) | normal |

**Conditions:**
- All three sessions ran concurrently in side-by-side terminals.
- Same prompt family / same repo / same Claude Code version (`claude-cli/2.1.119`) / same visible model.
- Same Claude Code OAuth subscription account.
- Same wall-clock window.

**Why this matters:**

The right-side native session is the control. It was on the same OAuth subscription seat as the two left/middle TokenPak sessions, hitting the same Anthropic billing pool, doing the same work — and it stayed healthy. **Therefore the bucket itself is not exhausted.** The retry behavior is happening inside the TokenPak Claude Code path, not on the upstream Anthropic side.

This is materially stronger than the iteration-2 evidence (2 TP concurrent degraded, no parallel native control). It directly addresses the iteration-1 §3.2 "C=degrades → not a TokenPak issue" branch: the native side is healthy, so the issue **is** TokenPak-specific.

---

## 2. Updated hypothesis priority (per directive)

### 2.1 Promoted

1. **H2** — session / session-lane collapse
2. **H9b** — shared OAuth refresh lane / shared Claude Code credential lane
3. **H4** — retry amplification under concurrent TokenPak sessions
4. **H9a / H9c** — pool lock or rotation lock
5. **H9d / SQLite telemetry lane** — lower priority unless trace shows lock/write contention

### 2.2 Demoted

- **Generic account quota** as sole cause — ruled out by the parallel-healthy native control
- **Pure prompt / context overhead** as sole cause — already demoted in iter-1; iter-3 reinforces (overhead would have hit the 1v1 baseline too)
- **Cache prefix disruption (H1)** as primary cause — kept available as secondary; only re-promote if the harness trace specifically shows cache_creation dominating cache_read

### 2.3 Cumulative ranking after iter-3

| Rank | Hypothesis | Status post-iter-3 |
|---:|---|---|
| **1** | **H2** session/session-id/lane collapse | HIGH — strongest fit; the proxy's stable per-process `X-Claude-Code-Session-Id` is the structural shape of "single lane with concurrent waiters" |
| **2** | **H9b** OAuth refresh lane | HIGH — top H9 sub-suspect; the `ClaudeCodeCredentialProvider._load` path is the only shared credential surface; thundering-herd pattern fits the symptom |
| **3** | **H4** retry amplification | HIGH (visually corroborated again — both TP sessions show retry messages) |
| **4** | **H9a / H9c** pool lock / rotation lock | MEDIUM — secondary shared-lane mechanisms |
| **5** | **H9d** SQLite telemetry lane | LOW — only re-promote on direct evidence |
| **6** | H1 / H3 cache & token overhead | SECONDARY — kept for completeness |
| **7** | H8 companion-side model calls | RULED OUT (unchanged from NCP-0) |

---

## 3. Implicit answers to the iteration-1 A/B/C/D matrix

The iter-3 setup answered three of the four A/B/C/D questions with strong signal:

| Test | Original goal | iter-3 implicit answer |
|---|---|---|
| **A** (1 TP + 1 native) | Parity check | ✅ parity (iter-1; iter-3 doesn't change this) |
| **B** (2 TP concurrent) | TP-only concurrency check | ✅ degraded (iter-2; iter-3 confirms the 2-TP retrying behavior reproduces) |
| **C** (2 native concurrent) | Anthropic-side concurrency control | ✅ **indirectly settled** — 1 native session running concurrently with 2 TP sessions stayed healthy, which is consistent with native concurrency being fine within the Anthropic bucket. Still useful to run a clean 2-native-only test for the formal record, but the conclusion is robust. |
| **D** (3 TP staggered 20s) | H2 vs H9b disambiguator | ⏸️ **still pending** — needed to discriminate which TP-internal mechanism dominates |

The remaining unknown is **D**, which routes between NCP-3A (session-id rotation; H2) and NCP-9 (OAuth refresh lane; H9b). Until D runs OR the harness trace settles it, the next-phase choice is in the H2 ∪ H9b candidate set, not pinned to either.

---

## 4. Pending data — harness output

Kevin will provide:

```bash
scripts/inspect_session_lanes.py --window-minutes 30
```

The harness covers eight diagnostic dimensions over `tp_events` + `tp_usage` (NCP-3 plan §5). When the output lands, three of those dimensions discriminate H2 from H9b without needing test D:

- **Dim 1 — session-collapse verdict.** `collapsed` = one wire-side session_id observed across many requests. Direct H2 confirmation.
- **Dim 2 — time-clustering verdict.** `serialized_or_throttled` = inter-request gaps comparable to or larger than per-request duration. Tells us *whether* the proxy is funneling, regardless of session-id.
- **Dim 8 — interleave score.** `serialized` (score < 0.25) = consecutive `tp_events` rows belong to the same session. Combined with dim 1, this distinguishes "one session-id, many concurrent requests" (H2 on session attribution) from "one session-id, requests serialized through a shared lane" (H9 on processing).

Once the harness output lands, the §6 decision tree from `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md` routes:

- Q1=YES (collapsed), Q3=YES (retries) → H2 confirmed; pending D for full discrimination, but **NCP-3A** (session-id rotation per CLI invocation) becomes the leading candidate
- Q1=YES, dim 2=serialized + dim 8=serialized → strong H9 signal even without dim 4 token disparity → **NCP-9** (refresh lane fix) candidate
- Q4=YES (provider != tokenpak-claude-code) → I-0 violation; the run is invalid

---

## 5. Synthesis posture (what happens when the harness output arrives)

A new short doc — `ncp-1a-iteration-3-trace-<TIMESTAMP>.md` — will be written as a **receiving form** for the harness output and the routing decision. It will:

1. Embed the verbatim harness output (markdown form).
2. Walk through the §6 decision-tree questions Q1–Q6 against the harness data.
3. Map the answers to one of:
   - **NCP-3A** — session-id / lane preservation fix (if H2 dominant)
   - **NCP-9** — OAuth refresh lane fix (if H9b dominant)
   - **NCP-4** — retry parity fix (folded into the Q1/Q2 winner if Q3 supports)
   - **NCP-3I** — in-proxy instrumentation (if dim 1 / dim 2 / dim 8 are not decisive)
   - **NCP-1C** — more operator data (test D) if still inconclusive
4. Pause for explicit Kevin approval before any implementation phase begins.

The acceptance gate from the directive is binding: **no fix lands until the trace clearly identifies the dominant mechanism AND the next phase is explicitly approved.**

---

## 6. Held throughout

Per the standing NCP-3-diagnostic constraints (unchanged):

- ❌ No routing changes
- ❌ No retry behavior changes
- ❌ No cache placement changes
- ❌ No prompt mutation changes
- ❌ No provider / model changes
- ❌ No auth behavior changes
- ❌ No production behavior changes
- ❌ No new SQLite columns / tables (deferred to NCP-3I if approved)
- ❌ No imports of dispatch / credential-injector primitives in any new code

This iter-3 PR is **docs-only** — recording evidence, not building anything.

---

## 7. Status snapshot

| Item | State |
|---|---|
| Iter-3 evidence (2 TP retry + 1 native healthy concurrently) | ✅ recorded |
| Hypothesis priority post-iter-3 | ✅ updated per directive |
| Generic account quota explanation | ✅ ruled out (1 native healthy in parallel) |
| Test A (parity) | ✅ confirmed (iter-1 + iter-3) |
| Test B (2 TP degraded) | ✅ confirmed (iter-2 + iter-3) |
| Test C (2 native concurrent) | ✅ indirectly settled — native concurrency is fine within the bucket |
| Test D (3 TP staggered 20 s) | ⏸️ pending — H2 vs H9b disambiguator |
| Harness output (`inspect_session_lanes.py --window-minutes 30`) | ⏸️ pending — operator providing |
| Next-phase decision (NCP-3A / NCP-9 / NCP-4 / NCP-3I / NCP-1C) | ⏸️ pending harness output + explicit approval |
| Code changes | ⛔ frozen per directive |

---

## 8. Cross-references

- `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md` — 1v1 baseline + ABCD plan
- `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md` — 2-TP-only degraded
- `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md` — NCP-3 diagnostic plan + decision tree §6
- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — Standard #24, invariants I-0 / I-3 / I-6
- `scripts/inspect_session_lanes.py` — harness the operator is running
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — H2 / H9b / H9c surface
- `tokenpak/proxy/connection_pool.py` — H9a surface
- `tokenpak/proxy/failover_engine.py` — H4 surface
