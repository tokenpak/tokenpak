# NCP-3A-streaming-connect — Phase 1 spec (issue #74)

**Date:** 2026-04-27
**Status:** ✅ **Phase 1 approved + landing in this PR** — Kevin approved 2026-04-27 evening; observability-only; #74 stays open for Phase 2
**Workstream:** NCP-3 (Session-Lane Preservation) → NCP-3A (Streaming) → NCP-3A-streaming-connect (pre-first-byte aborts)
**Authors:** Sue (scope) / Kevin (review + go-ahead)
**Tracker:** [tokenpak/tokenpak#74](https://github.com/tokenpak/tokenpak/issues/74)
**Companion docs:**
- NCP-3I-v3 (pre-dispatch lifecycle hooks + stream_abort emit): PR #72 merged 2026-04-27
- NCP-3A-enrichment (terminal early-return events): PR #77 merged 2026-04-27
- Issue #73 / PR #78 (`tp_parity_trace` canonicality): merged 2026-04-27
- Standards §7.1 amendment (canonicality clause): vault commit `8dc2d8f` 2026-04-27

> **Goal:** scope #74 — the 32-trace `stream_abort` / `RemoteProtocolError` cohort surfaced by NCP-3I-v3 and reproduced (3/22 traces) in the post-#78 baseline at `tests/baselines/ncp-3-trace/20260427T210920Z-issue73-postfix-3tp.{md,json}`. Recommend a **two-phase split**: Phase 1 = observability-only (`abort_phase` classification — directly satisfies #74's "Distinguishable telemetry exists for pre-first-byte vs mid-stream vs post-completion abort" acceptance bullet), Phase 2 = behavior change (root-cause / mitigation — separate initiative because it touches retry / pool / upstream-coordination).

---

## 0. Symptom (post-#78 baseline)

The 2026-04-27T21:09:20Z 3-concurrent workload produced 3 `stream_abort` events:

| trace_id | ts (epoch) | exception | hash |
|---|---|---|---|
| `12a9b22a-…` | 1777324155.281 | `RemoteProtocolError` | `8e43b815…` |
| `b970f41e-…` | 1777324155.282 | `RemoteProtocolError` | `8e43b815…` |
| `aa9b5ebe-…` | 1777324155.302 | `RemoteProtocolError` | `8e43b815…` |

**All three are identical-hash, within ~20 ms.** That is a strong shared-state-failure signature — most likely httpx connection-pool reuse (Anthropic-side keepalive collapse) or a single TCP reset taking down multiple streams that share a pooled connection. The pattern matches hypothesis 1 from the #74 body ("httpx connection-pool reuse: stale keepalive connection collapsing on first use") and is consistent with the original 32-trace cohort's profile (`bytes_from_upstream=0`, mostly `upstream_status=null`).

**At today's emit site** (`tokenpak/proxy/server.py` line 2389, the upstream-exception path of `_proxy_to_inner`), the following fields are **not captured** for `stream_abort`:
- `bytes_from_upstream` (NULL — not in scope at the exception site)
- `bytes_to_client` (NULL)
- `upstream_status` (NULL — except the 6 cases in #74 where headers had arrived before the body abort)
- `connection_closed_early` (NULL — only set on the in-band BrokenPipe path inside the streaming with-block)

So the harness can see "stream_abort happened" but cannot today distinguish *when* — pre-headers, post-headers/pre-first-byte, mid-stream, or post-completion. This is exactly the gap #74's second acceptance bullet calls out.

---

## 1. Two-phase split

### Phase 1 — observability (recommended for this lane)

**Goal:** make the abort phase distinguishable in `tp_parity_trace` so the 32-trace cohort can be characterized without changing runtime behavior.

**What changes (observability-only):**
1. **`tokenpak/proxy/server.py`** — at the existing two `EVENT_STREAM_ABORT` emit sites (the in-band BrokenPipe path around line 1968 and the upstream-exception path around line 2389), add an `abort_phase` classifier in the `notes` column as `abort_phase=<value>` (no schema migration). Allowed values per Kevin 2026-04-27:
   - `before_headers` — exception fired before `resp.status_code` was bound (no response headers seen)
   - `after_headers_before_first_byte` — headers received (`resp.status_code` bound) but `bytes_from_upstream==0`
   - `mid_stream` — `bytes_from_upstream>0` (body partially streamed before abort)
   - `client_disconnect` — downstream client closed mid-stream (the existing BrokenPipe / ConnectionResetError path, line 1968)
   - `upstream_protocol_error` — exception class is `RemoteProtocolError` or `LocalProtocolError` (the #74 cohort signature; classifier wins over phase axis to keep the protocol-error bucket targetable)
   - `unknown` — emit-side fallback when none of the above can be determined; also used by the harness for legacy traces emitted before this PR landed
2. **`tokenpak/proxy/server.py`** — at the upstream-exception emit site, also capture whatever fields ARE in scope: `bytes_from_upstream` and `upstream_status` if the variables exist in the local frame (Python-side `getattr` defensively, no new captures from outside). The downstream BrokenPipe site already captures these.
3. **`scripts/inspect_session_lanes.py`** — `_dim_parity_trace_coverage` exposes a new sub-distribution `stream_abort_phase_distribution` parsed from the `notes` column for `event_type='stream_abort'` rows. Q-line addition to synthesis.
4. **`tests/test_ncp3_inspect_session_lanes.py`** — new tests: per-phase classification + synthesis-line presence + invariance to pre-existing aborts without the new notes prefix (legacy traces should be classified as `unknown`).

**Acceptance for Phase 1 (matches #74 acceptance bullet 2):**
- For each `stream_abort` event going forward, `notes` carries `abort_phase=<value>`
- The harness reports a distribution by phase
- The 3-abort signature in the post-#78 baseline is reproduced under the new emit but classified — likely all `pre_first_byte` or `pre_headers`

**What is NOT changed in Phase 1:**
- No schema migration (everything goes in the existing `notes` TEXT column)
- No retry logic, no pool config, no upstream-coordination
- No new HTTP behavior; no new headers; no new connection management
- `parity_trace.py` event constants, `LIFECYCLE_ORDER`, and `TERMINAL_EARLY_RETURN_EVENTS` are unchanged
- `tp_events` is not touched (per #79 canonicality)

### Phase 2 — behavior (separate initiative; do NOT bundle)

**Goal:** materially reduce the abort cohort (matches #74 acceptance bullet 1: "The 32-trace stream_abort cohort is materially reduced or root-caused").

**What it would touch (NOT observability-only):**
- httpx pool sizing / `Keep-Alive` settings under the `services/` dispatch layer
- Selective retry on `RemoteProtocolError` (limited cases) — provider-policy decision
- Pool warmup / probe-and-discard on stale keepalive
- Possibly `services/transport_pool/*` reframe

**Why Phase 2 is a separate initiative:**
- Retry / pool / upstream-coordination changes are explicitly *behavior* changes
- Per the #73 / #74-A pattern, the user's standing rule is observability-first; behavior changes get their own scoping cycle
- Phase 1 produces the data needed to *target* Phase 2 — running Phase 2 before Phase 1 is "fix without diagnosis"

**Out of scope for THIS issue's scope doc.** Phase 2 will be opened as its own initiative once Phase 1 has produced 1+ workload's worth of phased abort data. Filename for that future spec: `docs/internal/specs/ncp-3a-streaming-connect-phase-2-mitigation-<date>.md`.

---

## 2. Out of scope (do not start)

Per Kevin's standing direction:
- **#75 NCP-3I-v4** (14 upstream_attempt_start orphans) — explicitly held until #74 is scoped (this doc), and likely until Phase 1 ships
- NCP-4 / NCP-9 — not in this lane
- streaming-connect Phase 2 (root-cause / mitigation) — separate initiative
- Any retry / routing / cache / auth / provider / model / prompt / pool change in Phase 1
- Any new `tp_parity_trace` schema column

---

## 3. Files in Phase 1 (if approved)

| File | Change | Δ LOC |
|---|---|---|
| `docs/internal/specs/ncp-3a-streaming-connect-2026-04-27.md` | This doc, finalized as the Phase 1 spec | new |
| `tokenpak/proxy/server.py` | Two `EVENT_STREAM_ABORT` emit sites: add `abort_phase=…` to `notes`; capture in-scope `bytes_from_upstream` / `upstream_status` defensively at the upstream-exception site | ~40 (additive, inside existing try-except guards) |
| `scripts/inspect_session_lanes.py` | `_dim_parity_trace_coverage`: parse `abort_phase` from `notes` for `event_type='stream_abort'`; new `stream_abort_phase_distribution` field; Q11 synthesis line | ~50 |
| `tests/test_ncp3_inspect_session_lanes.py` | Phase classification tests + synthesis renders Q11 + legacy-notes-classified-as-unknown | ~120 |

**Files NOT touched in Phase 1:**
- `tokenpak/proxy/parity_trace.py` — no schema, no event-constant changes
- `tokenpak/services/**` — no edits
- `tokenpak/proxy/connection_pool.py` (or wherever httpx pool lives) — no edits in Phase 1
- All routing / retry / cache / auth / provider / model / prompt code

---

## 4. Test plan (Phase 1)

- **Unit tests** in `tests/test_ncp3_inspect_session_lanes.py`:
  1. `test_stream_abort_pre_headers_classified` — synthetic `notes='abort_phase=pre_headers'` → distribution `{pre_headers: 1}`, legacy stream_abort row → `{unknown: 1}` mixed
  2. `test_stream_abort_pre_first_byte_classified` — same for `pre_first_byte`
  3. `test_stream_abort_mid_stream_classified` — same for `mid_stream`
  4. `test_stream_abort_post_completion_classified` — same for `post_completion`
  5. `test_stream_abort_legacy_notes_classified_unknown` — emit without the prefix → `{unknown: 1}`
  6. `test_q11_synthesis_renders_phase_breakdown` — markdown synthesis includes Q11 line with phase distribution
- **Regression:** all 36 existing `test_ncp3_inspect_session_lanes.py` cases must still pass; existing `test_parity_trace_phase_ncp_3i.py` cases unchanged
- **Integration sanity:** rerun the 3-concurrent post-#78 workload; confirm the 3 stream_aborts now carry `abort_phase=pre_first_byte` (or `pre_headers`) in `notes`

---

## 5. CI plan

All required-status checks must remain green: `bandit (blocking)`, `cli-docs-in-sync (blocking)`, `headline-benchmark (blocking)`, `self-conformance (blocking) (3.10/3.11/3.12)`, `Test (Python 3.10–3.13)`, `Lint (Ruff)`, `Import contracts`, `Repo Hygiene Check`. Process-enforced gating per `21 §9.8`.

---

## 6. Acceptance against #74

| #74 acceptance bullet | Phase | Satisfied by |
|---|---|---|
| "The 32-trace `stream_abort` cohort is materially reduced or root-caused." | Phase 2 | Out of scope for this PR — separate initiative |
| "Distinguishable telemetry exists for `pre-first-byte abort` vs `mid-stream abort` vs `post-completion abort`." | **Phase 1 (this PR)** | `abort_phase` in `notes` + `stream_abort_phase_distribution` in harness output + Q11 synthesis |

Phase 1 closes the *observability* half of #74 and provides the data needed to direct Phase 2 work. The issue itself stays open after Phase 1 lands until Phase 2 closes the cohort. Alternatively #74 closes after Phase 1 with a follow-up issue opened for Phase 2 — Kevin's call.

---

## 7. Open questions for Kevin

1. **Phase 1 only, or also propose Phase 2 timing?** Recommendation: Phase 1 only in this lane; Phase 2 scoped after Phase 1 ships and one workload's phased data is in hand.
2. **Issue-close shape.** Close #74 on Phase 1 merge (and open a Phase 2 issue) — OR — keep #74 open until Phase 2 also lands? Recommendation: close #74 on Phase 1, open `NCP-3A-streaming-connect Phase 2: root-cause / mitigation` as the successor.
3. **Should `connection_closed_early` be retroactively populated from existing per-trace fields where derivable?** Recommendation: no — keep Phase 1 strictly forward-looking to avoid back-filling that could mask future bugs.
4. **`abort_phase=unknown` for legacy traces** (those without the new notes prefix). Recommendation: yes — surfaces in the distribution as a known "pre-instrumentation" cohort.

---

## 8. Next steps (gated on Kevin approval)

1. Kevin reviews this scope doc and approves Phase 1
2. Open feature branch `feat/issue-74-streaming-connect-phase-1`
3. Land the four files as one PR titled `feat(proxy,scripts,tests,docs): #74 phase 1 — abort_phase classification`
4. Re-run the 3-concurrent workload; confirm classified abort phases land in `notes` and surface in the harness Q11 line
5. Merge per `21 §2.1` (squash) once CI green
6. Close #74 (Phase 1 acceptance bullet only) and open Phase 2 successor issue

**Estimated effort (Phase 1):** ~2 hours (proxy emit-site additions + harness parsing + 6 tests + workload rerun + PR cycle).
