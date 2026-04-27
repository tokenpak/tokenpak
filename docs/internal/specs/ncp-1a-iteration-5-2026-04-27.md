# NCP-1A iteration 5 — H10 streaming integrity + NCP-3I activation gap

**Date**: 2026-04-27
**Status**: 🟢 **NCP-3I-v2 landed** (this PR) — measurement-only; activation verification + streaming-integrity dimensions
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Iteration 1 (1v1 baseline): `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md`
  - Iteration 2 (2-TP concurrent degraded): `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md`
  - Iteration 3 (2 TP retry + 1 native healthy): `docs/internal/specs/ncp-1a-iteration-3-2026-04-27.md`
  - Iteration 4 (post-tool-result + interp B): `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md`
  - NCP-3I v1 spec: `docs/internal/specs/ncp-3i-instrumentation-2026-04-27.md`

> **Headline:** during the 3-concurrent `tokenpak claude` repro, one TokenPak session displayed `API Error: JSON Parse error: Unterminated string`. This is a **streaming integrity** symptom — TokenPak may be forwarding a malformed / truncated / corrupted SSE response to Claude Code under concurrent load. Adds **H10**. Also: the previous NCP-3I run produced no `tp_parity_trace` table — the instrumentation isn't observing the actual `tokenpak claude` path. NCP-3I-v2 ships an **activation verification script** + **stream-integrity dimensions** + **schema migration** to address both findings.

---

## 1. Operator-supplied evidence (iter-5)

| Slot | Variant | Behavior |
|---|---|---|
| Session 1 | TokenPak Claude Code | retrying |
| Session 2 | TokenPak Claude Code | **`API Error: JSON Parse error: Unterminated string`** |
| Session 3 | TokenPak Claude Code | retrying |

The "Unterminated string" message is rendered by the Claude CLI's own JSON parser failing on a chunk it received. Because TokenPak is the only middleman, the most likely cause is that **TokenPak's streaming forward path delivered truncated / malformed JSON** to the CLI under concurrent load. This is qualitatively different from rate-limit-driven retry (H4) or session-id collapse (H2) — those produce HTTP-level signals; "Unterminated string" is a wire-content corruption symptom.

---

## 2. New hypothesis — H10 (streaming JSON/SSE corruption)

**H10**: Under concurrent TokenPak Claude Code sessions, TokenPak forwards malformed / truncated / corrupted JSON or SSE bytes to the CLI. Causes Claude Code's parser to fail with errors like "Unterminated string", which cascade into retry behavior visible in the TUI.

Sub-mechanisms (all candidates):

| Sub-hypothesis | Surface | Likely magnitude |
|---|---|---|
| **H10a** Race in shared response buffer | Hypothetical — would require buffer-sharing between concurrent streams. Unlikely on inspection: `iter_bytes` chunks per-response. | LOW |
| **H10b** Truncated SSE delivery on `BrokenPipeError` mid-write | server.py:1807-1808 — the loop `break`s on the first `BrokenPipeError`, leaving the SSE stream truncated. The CLI sees an incomplete final frame. | **HIGH (most likely)** |
| **H10c** Concurrent stream interference | Multiple concurrent `pool.stream` calls on the same httpx client. httpx's threading model says client is thread-safe, but stream-level interference under load is possible. | MEDIUM |
| **H10d** Response body decoded incorrectly | proxy strips `content-encoding` (gzip) and forwards decoded bytes; if upstream sends partial-gzip mid-stream and the decoder produces garbled output. | LOW–MEDIUM |
| **H10e** Header / body mismatch | `Content-Type: text/event-stream` set but body is non-SSE (e.g. truncated to a JSON error frame). | MEDIUM |

**Updated hypothesis ranking (per directive)**:

| Rank | Hypothesis | Status |
|---:|---|---|
| **1** | **H10** streaming JSON/SSE corruption | NEW HIGH (iter-5) |
| **2** | **H4** retry amplification | HIGH |
| **3** | **H9b** OAuth/shared credential lane | HIGH |
| **4** | **H2** session/lane collapse | HIGH |
| **5** | **H3** post-tool-result request-size amplification | MEDIUM-HIGH |
| 6 | H9a/H9c pool lock / rotation lock | MEDIUM |
| 7 | H1 cache prefix disruption | SECONDARY |
| 8 | H9d SQLite telemetry lane | LOW |
| RULED OUT | H8 companion-side model calls | unchanged |

---

## 3. Activation gap — NCP-3I v1 captured nothing

The previous NCP-3I run produced no `tp_parity_trace` table. Per the iter-4 §11 / NCP-3I §1.1 acceptance gate, this means **the instrumentation didn't run in the process serving the `tokenpak claude` path**.

Six conditions have to align for v1 instrumentation to capture:

1. The running tokenpak includes PR #70 / commit `ec34c94703`+
2. `TOKENPAK_PARITY_TRACE_ENABLED` is truthy in the **proxy process's** environment (not just the operator's shell)
3. The proxy is running on the port the launcher will use
4. `ANTHROPIC_BASE_URL` is unset OR points at the proxy (the launcher's `env.setdefault` won't override)
5. `TOKENPAK_PROXY_BYPASS` is unset
6. `TOKENPAK_HOME` resolves to the same dir the proxy writes telemetry to

**The most common gap**: the operator sets the env var AFTER `tokenpak start` is already running. The proxy's environment was captured at start time and doesn't see the new value. **Fix**: `export TOKENPAK_PARITY_TRACE_ENABLED=true && tokenpak stop && tokenpak start`.

---

## 4. NCP-3I-v2 deliverables (this PR)

### 4.1 Activation verification — `scripts/verify_parity_trace_activation.py`

New read-only script that checks all six conditions + three secondary checks (TOKENPAK_HOME resolution, telemetry.db presence, `tp_parity_trace` table existence). Emits human-readable or JSON output; exits non-zero on required-check failure.

Smoke-tested against the dev host: correctly diagnosed that the running proxy was started before `TOKENPAK_PARITY_TRACE_ENABLED` was exported.

Operator usage:

```bash
scripts/verify_parity_trace_activation.py
# Fix any ✗ checks before re-running the workload.
```

### 4.2 Stream-integrity dimensions on `tp_parity_trace`

Sixteen new columns added via additive ALTER TABLE migration (v1 hosts upgrade automatically on first emit):

| Column | Type | Purpose |
|---|---|---|
| `lane_id` | TEXT | `<pid>:<thread_id>` — stable lane identifier |
| `concurrent_stream_count` | INTEGER | gauge at stream-start time |
| `stream_started` | INTEGER | 0/1 |
| `stream_completed` | INTEGER | 0/1 |
| `stream_aborted` | INTEGER | 0/1 (deferred to v3 — see §5) |
| `upstream_status` | INTEGER | resp.status_code |
| `downstream_status` | INTEGER | sent to client |
| `response_content_type` | TEXT | upstream Content-Type |
| `sse_event_count` | INTEGER | best-effort count of `event:` lines in SSE buffer |
| `sse_last_event_type` | TEXT | name of the last `event:` (deferred capture) |
| `bytes_from_upstream` | INTEGER | sum of chunk sizes received |
| `bytes_to_client` | INTEGER | sum of chunk sizes successfully written |
| `json_parse_error_seen` | INTEGER | 0/1 — deferred capture |
| `stream_exception_class` | TEXT | type name when an exception fires |
| `stream_exception_message_hash` | TEXT | sha256-hex of str(exc); never the message |
| `connection_closed_early` | INTEGER | 1 when bytes_to_client < bytes_from_upstream |

The `bytes_from_upstream` vs `bytes_to_client` gap is the **most direct H10b signal**: when client closes mid-stream (BrokenPipeError) the proxy stops writing, leaving `bytes_to_client < bytes_from_upstream`, which corresponds to a truncated SSE frame on the client side.

### 4.3 Three new event types

- `EVENT_STREAM_START` — fires just inside `with pool.stream(...)` after `send_response(status)`. Carries: `upstream_status`, `response_content_type`, `lane_id`, `concurrent_stream_count`.
- `EVENT_STREAM_COMPLETE` — fires after the `with` block exits cleanly. Carries: `bytes_from_upstream`, `bytes_to_client`, `sse_event_count`, `connection_closed_early`.
- `EVENT_STREAM_ABORT` — defined but **not yet wired** in v2. Wiring requires wrapping the 100-line streaming body in try/except; deferred to NCP-3I-v3 to keep v2 strictly additive (no re-indented code blocks).

### 4.4 Concurrent-stream gauge + helpers

`tokenpak/proxy/parity_trace.py` adds three helpers:

- `begin_stream() -> int` — increments the global counter; returns new value
- `end_stream() -> int` — decrements; returns new value
- `current_lane_id() -> str` — `f"{pid}:{thread_id}"`
- `hash_exception_message(exc) -> str` — sha256-hex; never persists the message text

Process-wide thread-safe via `_CONCURRENT_STREAMS_LOCK`.

### 4.5 Server.py hook integration

Five additive hook insertions in `_proxy_to_inner` (zero behavior change when `TOKENPAK_PARITY_TRACE_ENABLED=false`):

1. **Pre-stream setup** (initialize counters, call `begin_stream`)
2. **Stream-start emit** (just inside the `with`, after `send_response`)
3. **Bytes-up counter** (in the `iter_bytes` loop, before `wfile.write`)
4. **Bytes-down counter** (in the `iter_bytes` loop, after successful write)
5. **Stream-complete emit + end_stream** (after the `with` block)

All wrapped in try/except. The bytes counters are pure additive `+=` operations on local variables.

### 4.6 Tests

`tests/test_parity_trace_phase_ncp_3i.py` adds **9 new v2 tests** in `TestStreamIntegrityV2`:

- New event constants are present and in `ALL_EVENTS`
- All 16 v2 columns persist on a fresh install
- v1 → v2 schema migration via ALTER TABLE works
- Concurrent-stream counter round-trips
- Counter is thread-safe under 20 concurrent threads
- `lane_id` format: `<pid>:<thread_id>`
- `hash_exception_message` is deterministic and 64-hex
- Hash never contains the exception message text (privacy)
- `idx_parity_lane` index exists post-migration

Plus the v1 26-test suite continues to pass. Total module: **35/35 green**.

---

## 5. Held throughout NCP-3I-v2 (per directive)

- ❌ No routing changes
- ❌ No retry changes (no failover-engine modifications)
- ❌ No stream behavior changes (byte counters are passive; the `with pool.stream` block body is byte-identical)
- ❌ No auth / provider / model changes
- ❌ No prompt mutation changes
- ❌ No raw prompts or secrets stored (privacy contract continues)
- ❌ No new SQLite tables (just ALTER TABLE on existing `tp_parity_trace`)
- ❌ Tests: 35/35 module + regression-clean PI-3 / NCP-1 / NCP-3 / NCP-3I-v1

---

## 6. Recommended next phase

**NCP-3I-v2 → operator activation + workload run.**

```bash
# 1. Verify activation.
scripts/verify_parity_trace_activation.py

# 2. If any required check fails, fix and re-verify.
#    Most common fix:
export TOKENPAK_PARITY_TRACE_ENABLED=true
tokenpak stop ; tokenpak start

# 3. Run the iter-3-style 2-TP-concurrent or iter-5 3-TP-concurrent workload.
#    (Two or three terminals, each running `tokenpak claude` with the same
#    prompt sequence.)

# 4. After the run:
scripts/inspect_session_lanes.py --window-minutes 30 \
    --output tests/baselines/ncp-3-trace/$(date -u +%Y%m%dT%H%M%SZ).md

# 5. Inspect dim 9 (parity-trace coverage). The v2 columns will surface:
#      - bytes_from_upstream vs bytes_to_client gap (H10b signal)
#      - concurrent_stream_count at each event (H10c signal)
#      - response_content_type (H10e signal)
```

**Routing logic post-trace** (per the iter-4 §6 + iter-5 H10 additions):

| Observed | Recommended phase |
|---|---|
| `bytes_to_client < bytes_from_upstream` on the failing trace | **NCP-3A-streaming** — the `BrokenPipeError` `break` truncates SSE; needs proper "drain remainder + emit final frame marker" before exiting |
| Multiple traces with `concurrent_stream_count > 1` AND degraded `interp_b` count | **NCP-3I-v3** — wire `stream_aborted` event (requires the with-block try/except restructure deferred from v2) |
| `response_content_type` ≠ `text/event-stream` on the failing trace | **NCP-9b** — header / body mismatch fix |
| All clean | **NCP-1C** — more operator data needed |

---

## 7. Cross-references

- `docs/internal/specs/ncp-3i-instrumentation-2026-04-27.md` — NCP-3I v1 spec (extended by this iter-5 / v2)
- `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md` — directive that promoted H10 / scoped activation verification
- `tokenpak/proxy/parity_trace.py` — module with v2 schema + helpers
- `tokenpak/proxy/server.py::_proxy_to_inner` — 5 v2 hook insertions in the streaming branch
- `scripts/verify_parity_trace_activation.py` — activation-gap diagnostic
- `scripts/inspect_session_lanes.py` — harness; consumes new v2 columns via existing dim 9
- `tests/test_parity_trace_phase_ncp_3i.py::TestStreamIntegrityV2` — 9 new v2 tests
