# Issue #73 — `tp_parity_trace` canonicality for wire-side completion

**Date:** 2026-04-27
**Status:** ✅ **approved + landing in this PR (Option B)** — Kevin approved 2026-04-27 evening; observability-only; no runtime behavior changes
**Workstream:** Observability (telemetry canonicality clarification)
**Authors:** Sue (scope + implementation) / Kevin (review + go-ahead)
**Tracker:** [tokenpak/tokenpak#73](https://github.com/tokenpak/tokenpak/issues/73)
**Companion docs:**
- NCP-3A enrichment: PR #77 merged 2026-04-27 (the four early-return emit blocks this work consumes)
- NCP-3I instrumentation v1: `docs/internal/specs/ncp-3i-instrumentation-2026-04-27.md`
- Architecture standard: `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/01-architecture-standard.md` §1 (telemetry subsystem) + §6 (state partitioning, lines 213–223)

> **Goal:** resolve issue #73 — `traces_with_completion_in_tp_events = 0/187` for successful streamed requests — as an **observability-only** harness correction. Declare `tp_parity_trace` the canonical wire-side completion ledger for proxy-served Claude Code traffic. Update `scripts/inspect_session_lanes.py` Q8 logic to consume canonical terminal events from `tp_parity_trace`. Keep the legacy `tp_events` comparison only as a deprecated diagnostic. **No routing, retry, cache, auth, provider, model, or prompt-behavior changes; no proxy writes to `tp_events`.**

---

## 0. Problem statement (from #73)

The NCP-3I-v3 trace at 2026-04-27T18:41:57Z showed:

| Metric | Value |
|---|---|
| Distinct traces (30-min window) | 187 |
| `traces_with_handler_entry` | 187 |
| `traces_with_completion_in_tp_events` | **0** |
| Cleanly streamed (`stream_complete`, HTTP 200, byte-matched) | 86 |

The harness's `Q8` verdict computed `interp_b_count = n_with_handler_entry − n_with_completion_in_tp_events` and fired `interp_b_supported` whenever that count > 0. It fired for **every** request, so the signal was unusable as a failure detector. The post-merge NCP-3A workload (2026-04-27T20:38:36Z, after PR #77) reproduced the same 0/21 ratio.

---

## 1. Root cause (architectural, not a regression)

**Finding:** the proxy's request hot path has **never** written `tp_events` rows. It writes `tp_parity_trace` (since PR #70). `tp_events` is populated only by `tokenpak.telemetry.pipeline.TelemetryPipeline.process(...)`, called exclusively from `tokenpak/telemetry/server.py` — a separate HTTP server that ingests POSTed events. No code path in `tokenpak/proxy/server.py` calls `TelemetryDB.insert_event` or `insert_trace`.

**Evidence:**
- `git log --all -S 'insert_event' -- tokenpak/proxy/` returns no commits
- `git log --all -S 'insert_trace' -- tokenpak/proxy/` returns no commits
- `grep -rE 'INSERT INTO tp_events' tokenpak/` returns only test fixtures
- The two real `tp_events` rows in the active DB are doctor-check seed data, not Claude Code traffic

**Implication:** Q8's logic was written against a `tp_events`-completion assumption the Claude Code path has never satisfied. The completion signal that *does* exist is `tp_parity_trace` terminal events.

**Standards alignment:** `01-architecture-standard.md` §1 says the telemetry store is "Written to by `services/`; read by `dashboard/` and `alerts/`." Entrypoints don't write. So the gap closes at the harness layer (which compares the wrong signal). Adding writes to `proxy/` would *violate* the standard.

---

## 2. Approved approach: Option B

Declare `tp_parity_trace` the canonical wire-side completion ledger; update Q8 to consume terminal events from it; keep the legacy `tp_events` comparison only as a deprecated diagnostic. A `services/`-side `tp_events` writer (Option A) was considered and **explicitly rejected** for this PR — would not be observability-only, and would duplicate information already in `tp_parity_trace`.

### 2.1 Canonical wire-side terminal events

Per Kevin's directive, the four canonical terminal events for Claude Code wire-side traffic are:

| Event | Classification |
|---|---|
| `stream_complete` | clean upstream stream completion |
| `dispatch_subprocess_complete` | intentional in-process / subprocess companion completion |
| `request_rejected` | known terminal fast-fail (auth-401 / circuit-503 / validator-400) — **not** a silent death |
| `stream_abort` | known terminal abort — **not** a silent missing completion |

A trace with at least one of these in its event set has a terminal wire-side event. A trace with `handler_entry` but **no** terminal wire-side event is a true silent death (the interp-B condition).

### 2.2 Q8 reframed

Old: *"Do parity traces also have a `tp_events` completion row?"*
**New: *"Do parity traces have a terminal wire-side event?"***

Verdict states (unchanged labels, redefined semantics):

| Verdict | New definition |
|---|---|
| `interp_a_or_clean` | every `handler_entry` trace has at least one canonical terminal event |
| `interp_b_supported` | one or more `handler_entry` traces have **no** canonical terminal event (true silent death) |
| `indeterminate` | telemetry layer cannot answer (table missing or empty window) |

### 2.3 New / updated `dim9_parity_trace_coverage` fields

| Field | Type | Meaning |
|---|---|---|
| `traces_with_wire_completion` | int | distinct `handler_entry` traces with **any** of the four canonical terminal events |
| `traces_with_clean_wire_completion` | int | distinct `handler_entry` traces with `stream_complete` |
| `traces_with_terminal_fast_fail` | int | distinct `handler_entry` traces with `request_rejected` |
| `traces_with_terminal_abort` | int | distinct `handler_entry` traces with `stream_abort` |
| `traces_without_terminal_event` | int | distinct `handler_entry` traces with **none** of the four canonical terminals (silent-death cohort) |
| `traces_with_completion_in_tp_events_deprecated` | int \| null | legacy `tp_events`-stitched count, kept for diagnostic context only; not used by Q8 verdict |
| `interp_b_count` | int | redefined as `traces_without_terminal_event`; preserved as an alias so dependent tooling continues to compile |

The 6th field also satisfies the user's "old `tp_events` comparison remains available only as deprecated context" requirement. `dispatch_subprocess_complete` is reachable via `terminal_event_distribution` (already implicit in the existing `event_type_distribution`); no separate top-level field is added per Kevin's spec.

The existing `early_return_*` fields from PR #77 are **kept unchanged** (Q10 still answers a different question: "which subset is intentional early termination?"). They overlap with `traces_with_terminal_fast_fail` + the subprocess-complete subset, but Q10's framing remains useful, so we leave it alone.

---

## 3. Out of scope (do not start)

Per Kevin's 2026-04-27 directive:
- NCP-4
- NCP-9
- streaming-connect work (#74)
- NCP-3I-v4 (#75)
- Any retry behavior change
- Any routing, cache, auth, provider, model, or prompt-mutation change
- Wiring a `services/`-side `tp_events` writer (Option A — deferred to future initiative)
- The 1-week NCP-3A cleanup follow-up agent (explicitly skipped)
- **Standards ratification.** Per Kevin: "Open a separate follow-up standards proposal for: 'Wire-side completion canonicality: tp_parity_trace vs tp_events.' Do not include standards ratification in this PR." A follow-up issue will be filed post-merge.

---

## 4. Files changed in this PR

| File | Change | Δ LOC |
|---|---|---|
| `docs/internal/specs/issue-73-tp-events-canonicality-2026-04-27.md` | This doc | new |
| `scripts/inspect_session_lanes.py` | `_dim_parity_trace_coverage`: add canonical-terminal counts; redefine Q8 verdict over `traces_without_terminal_event`; rename legacy field to `traces_with_completion_in_tp_events_deprecated`; update synthesis text | ~80 |
| `tests/test_ncp3_inspect_session_lanes.py` | Update existing Q8 tests for new semantics; add 6 new tests covering each terminal class + silent-death + legacy-field + tp_events-doesn't-affect-verdict | ~200 |

**Files NOT touched:**
- `tokenpak/proxy/**` — no edits, no new emit calls
- `tokenpak/proxy/parity_trace.py` — schema, events, and emit points unchanged
- `tokenpak/services/**` — no new writers
- `tokenpak/telemetry/storage*.py` — no schema migrations
- Routing / retry / cache / auth / provider / model / prompt code

---

## 5. Test plan

- **Updated tests:** `test_dim9_interp_b_supported_entry_without_completion` and `test_dim9_interp_a_clean_when_completions_match` reframed to drive the verdict via `tp_parity_trace` terminal events instead of `tp_events` rows.
- **New tests** (in `TestParityTraceCoverage`):
  1. `test_wire_completion_via_stream_complete` — handler_entry + stream_complete → clean wire completion; verdict `interp_a_or_clean`
  2. `test_wire_completion_via_request_rejected` — handler_entry + request_rejected → terminal fast-fail; verdict `interp_a_or_clean`
  3. `test_wire_completion_via_stream_abort` — handler_entry + stream_abort → terminal abort; verdict `interp_a_or_clean`
  4. `test_wire_completion_via_subprocess_complete` — handler_entry + dispatch_subprocess_complete → counted in `traces_with_wire_completion`; verdict `interp_a_or_clean`
  5. `test_traces_without_terminal_event_is_silent_death` — handler_entry only → counted in `traces_without_terminal_event`; verdict `interp_b_supported`
  6. `test_legacy_tp_events_field_kept_as_deprecated` — `traces_with_completion_in_tp_events_deprecated` present and computed; verdict NOT influenced by it (handler_entry + stream_complete + zero `tp_events` rows still yields `interp_a_or_clean`)
- **Regression:** existing 67 parity-trace + harness tests must still pass; existing 7 NCP-3A tests must still pass.
- **Integration sanity:** rerun the post-merge 3-concurrent baseline workload; confirm Q8 reports `interp_a_or_clean` instead of universal interp B.

---

## 6. CI plan

All required-status checks must remain green:
`bandit (blocking)`, `cli-docs-in-sync (blocking)`, `headline-benchmark (blocking)`, `self-conformance (blocking) (3.10 / 3.11 / 3.12)`, `Test (Python 3.10 / 3.11 / 3.12 / 3.13)`, `Lint (Ruff)`, `Import contracts (Architecture §2 + §1.4)`, `Repo Hygiene Check`. Per `21 §9.8` process-enforced gating: do not merge if any blocking check is red.

---

## 7. Acceptance (against Kevin's 2026-04-27 bar)

| Criterion | How this PR satisfies |
|---|---|
| Observability-only | Only `docs/` + `scripts/` + `tests/` changed; zero touches to proxy/services/schema/runtime |
| No proxy writes to `tp_events` | Confirmed — proxy/ untouched |
| No runtime behavior changes | `tokenpak/` package not modified |
| No routing/retry/cache/auth/provider/model/prompt changes | None of those subsystems edited |
| Existing parity trace remains canonical for NCP wire diagnostics | `parity_trace.py` unchanged; canonicality formalized in this doc |
| Old `tp_events` comparison remains available only as deprecated context | `traces_with_completion_in_tp_events_deprecated` retained; not used by Q8 verdict |
| CI green | All blocking checks gated before merge |
| #73 acceptance: "for at least N successfully-streamed requests, completion exists keyed on `trace_id`" | `tp_parity_trace.stream_complete` rows keyed on `trace_id` (verified: 20/20 in the 2026-04-27T20:38:36Z post-merge baseline) |
| #73 acceptance: "Q8 reports `interp_a_or_clean` when no real interp-B failure" | Direct outcome of the harness change; verified by rerun workload after this PR's harness lands |

---

## 8. Follow-up (separate from this PR)

A standards-proposal issue will be filed post-merge titled *"Wire-side completion canonicality: tp_parity_trace vs tp_events"* against the `tokenpak/tokenpak` repo, asking whether `01-architecture-standard.md` §6 should be amended to encode the canonicality clause. Kevin's explicit direction: not part of this PR.
