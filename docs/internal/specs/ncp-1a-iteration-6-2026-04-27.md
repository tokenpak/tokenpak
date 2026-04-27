# NCP-1A iteration 6 — pre-dispatch lifecycle hooks (NCP-3I-v3)

**Date**: 2026-04-27
**Status**: 🟢 **NCP-3I-v3 landed** (this PR) — measurement-only; pre-dispatch lifecycle hooks
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Iteration 5 (H10 + activation gap): `docs/internal/specs/ncp-1a-iteration-5-2026-04-27.md`
  - NCP-3I v1 spec: `docs/internal/specs/ncp-3i-instrumentation-2026-04-27.md`

> **Headline:** the disambiguation step (4-row trace inspection) confirmed that real-workload `tokenpak claude` requests **reach `_proxy_to_inner` but die before `pool.stream`**. The 1 row from the prior trace is the liveness curl; 3 of 4 rows in the latest disambiguation are real repro requests showing handler_entry without upstream_attempt_start. NCP-3I-v3 wires 6 new lifecycle hooks in the gap so the operator can localize exactly which stage requests stop at.

---

## 1. Disambiguation evidence (operator-supplied)

5 rows in `tp_parity_trace`:

| ts (UTC) | trace | events |
|---|---|---|
| 08:57:23 | liveness curl | handler_entry |
| 08:57:26 | liveness curl | upstream_attempt_start |
| 09:07:44 | repro req 1 | handler_entry only |
| 09:37:43 | repro req 2 | handler_entry only |
| 10:07:44 | repro req 3 | handler_entry only |

**Key finding:** the **liveness curl** flowed all the way through (handler_entry → upstream_attempt_start), proving the proxy's hooks fire end-to-end on a synthetic request. But the **3 actual `tokenpak claude` repro requests** all stop at handler_entry. They reach `_proxy_to_inner`, fire the entry hook, then never fire `upstream_attempt_start`.

The 900-line gap between server.py:772 (handler_entry hook) and server.py:1707/1847 (upstream_attempt_start hook) is where requests die. The liveness curl traverses this gap successfully because it's a simple synthetic POST; the `tokenpak claude` repro fails somewhere in the middle.

---

## 2. NCP-3I-v3 deliverables

### 2.1 Six new lifecycle event types

Per the directive's "minimum acceptable" set:

| Event | Position in `_proxy_to_inner` | Captures |
|---|---|---|
| `EVENT_AUTH_GATE_PASS` | top, after `handler_entry` | tautological-but-confirmed (do_POST gates pre-call) |
| `EVENT_ROUTE_RESOLVED` | top, after auth_gate_pass | confirms do_POST chose this route |
| `EVENT_BODY_READ_COMPLETE` | line ~813 (after `self.rfile.read`) | request body successfully read |
| `EVENT_ADAPTER_DETECTED` | line ~840 (after `detect_platform`) | platform adapter resolved |
| `EVENT_BEFORE_DISPATCH` | adjacent to existing `upstream_attempt_start` | last marker before `pool.stream` / `pool.request` |
| `EVENT_STREAM_ABORT` | (a) BrokenPipe except + (b) outer except at line 2237+ | downstream client disconnect OR upstream-side exception |

The directive's "optional" set (`EVENT_BODY_READ_START`, `EVENT_COMPRESSION_START/COMPLETE`, `EVENT_ENRICHMENT_START/COMPLETE`, `EVENT_CACHE_LOOKUP_START/COMPLETE`) is **deferred**. The minimum set is sufficient to localize where requests die; finer-grained hooks land if v3 evidence shows the death is between two of the existing checkpoints.

### 2.2 `LIFECYCLE_ORDER` constant

`tokenpak/proxy/parity_trace.py` exports a tuple of all events in canonical order:

```
handler_entry → auth_gate_pass → route_resolved → body_read_complete →
request_classified → adapter_detected → before_dispatch →
upstream_attempt_start → stream_start → stream_complete → stream_abort →
upstream_attempt_failure → retry_boundary → request_completion
```

Used by `inspect_session_lanes.py` to render per-trace progression.

### 2.3 Two stream_abort hooks

- **(a) Downstream BrokenPipe** — emitted in the existing `except (BrokenPipeError, ConnectionResetError)` inside the iter_bytes loop. Carries `bytes_from_upstream`, `bytes_to_client`, `connection_closed_early=1`, `retry_owner="claude_code_client"`. This is the **H10b** capture path.
- **(b) Upstream-side exception** — emitted at the top of the existing outer `except Exception as exc:` at server.py:~2237. Carries `stream_exception_class`, `stream_exception_message_hash`, `retry_owner="upstream_provider"`. This is the **H10c/d** capture path.

Both are pure additions to existing except blocks; no behavior change.

### 2.4 Harness extension — per-trace lifecycle progression

`scripts/inspect_session_lanes.py` dim 9 now reports two new fields:

- **`last_stage_distribution`**: counts of how many traces' last-observed event was each stage. Tells the operator at a glance "N traces died at handler_entry, M at body_read_complete, etc."
- **`pre_upstream_death_count`**: count of traces whose last-observed event was BEFORE `upstream_attempt_start` — the iter-6 §1 condition.
- **`pre_upstream_death_stage_distribution`**: same breakdown, filtered to only pre-dispatch deaths.

A new **Q9** synthesis line summarizes: "N traces died BEFORE upstream_attempt_start. Top stages (stage_at_death=count): X=N1, Y=N2, Z=N3."

---

## 3. Death-localization decision tree (post-v3)

After running the workload with v3 hooks live, the harness Q9 line directly identifies the death point. Routing logic:

| Q9 finding | What it means | Next phase |
|---|---|---|
| Most deaths at `handler_entry` | Request enters `_proxy_to_inner` then immediately dies — likely an early-return or exception in the first ~50 lines | **NCP-3A-handler-init** — debug the early-return paths |
| Most deaths at `auth_gate_pass` | Tautological row alone means body read or URL parse died — but `auth_gate_pass` should ALWAYS be paired with `route_resolved` since they emit consecutively. If only one fires, the emit() itself raised | **NCP-3I-v3-fix** |
| Most deaths at `body_read_complete` | Body read completed but classification / adapter detection died | **NCP-3A-classification** — instrument the classification path |
| Most deaths at `adapter_detected` | Adapter detection completed but compression / vault / cache lookup died | **NCP-3A-enrichment** — instrument compression / vault / cache |
| Most deaths at `before_dispatch` | About to call `pool.stream` / `pool.request` and died — classic connection-pool issue, OAuth refresh thundering herd (H9b), or pool lock contention (H9a) | **NCP-9b** OAuth refresh fix OR **NCP-3A-pool** pool fix |
| `upstream_attempt_start` fires + `stream_abort` with bytes_to_client < bytes_from_upstream | Stream completed upstream-side but client disconnected mid-write | **NCP-3A-streaming** drain-remainder fix |
| All clean — every trace reaches `request_completion` | The repro didn't reproduce the failure mode in this run | **NCP-1C** more operator data |

---

## 4. Held throughout NCP-3I-v3

- ❌ No routing changes
- ❌ No retry behavior changes
- ❌ No cache behavior changes
- ❌ No auth behavior changes
- ❌ No prompt mutation changes
- ❌ No provider/model changes
- ❌ No raw prompts / secrets stored (8 hooks, all with structured fields only)
- ❌ No new SQLite tables or columns (uses existing v2 schema)
- ❌ Module + harness + tests + docs ONLY

The 6 new event types extend `LIFECYCLE_ORDER` but don't change the schema. The `notes` field is the only free-form sink, populated only with target_url-truncated strings (URL is non-secret) or platform_name (already public).

---

## 5. Tests

- `tests/test_parity_trace_phase_ncp_3i.py::TestPreDispatchLifecycleV3` — **5 new tests**:
  - All 5 minimum-set v3 events present in `ALL_EVENTS`
  - `LIFECYCLE_ORDER` ordering: handler_entry < auth_gate_pass < body_read_complete < before_dispatch < upstream_attempt_start
  - Pre-dispatch chain persists when emitted in sequence (death-mid-pipeline simulation)
  - Full chain persists when emitted to completion
  - V3 events respect the env-var disable
- `tests/test_ncp3_inspect_session_lanes.py::TestParityTraceCoverage` — **2 new tests**:
  - V3 pre-dispatch death localization: 3 traces with different death stages → distribution surfaces correctly
  - V3 synthesis renders Q9 line when pre-dispatch deaths exist

Total: **40 module + 27 harness = 67 tests** (35 + 5 + 25 + 2).

---

## 6. Cross-references

- `docs/internal/specs/ncp-1a-iteration-5-2026-04-27.md` — H10 + activation gap (predecessor)
- `docs/internal/specs/ncp-3i-instrumentation-2026-04-27.md` — NCP-3I v1 spec
- `tokenpak/proxy/parity_trace.py` — module with v3 events + `LIFECYCLE_ORDER`
- `tokenpak/proxy/server.py::_proxy_to_inner` — 6 new hook insertions
- `scripts/inspect_session_lanes.py` — dim 9 v3 fields + Q9 synthesis
- `tests/test_parity_trace_phase_ncp_3i.py::TestPreDispatchLifecycleV3` — module tests
- `tests/test_ncp3_inspect_session_lanes.py::TestParityTraceCoverage` — harness tests
