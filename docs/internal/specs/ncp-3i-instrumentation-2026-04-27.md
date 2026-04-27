# NCP-3I — In-proxy parity-trace instrumentation (v1)

**Date**: 2026-04-27
**Status**: 🟢 **landed** (this PR) — measurement-only; gated behind `TOKENPAK_PARITY_TRACE_ENABLED` env-var (default `false`)
**Workstream**: NCP (Native Client Concurrency Parity) → NCP-3 (Session-Lane Preservation) → NCP-3I (Instrumentation)
**Authors**: Sue (implementation) / Kevin (review + scope)
**Companion docs**:
  - NCP-1A iteration 4 (post-tool-result + interp B observation): `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md`
  - NCP-3 diagnostic plan: `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`
  - Standard #24: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`

> **Goal:** make the iter-4 §11 "interp B" condition (visible TUI retries with empty TokenPak telemetry) **detectable and characterizable** by adding entry-time + upstream-attempt-time hooks the existing completion-time `tp_events` writer cannot see. **Measurement only.** No routing, retry, cache, prompt-mutation, provider, model, or auth behavior changes.

---

## 0. What landed

### 0.1 New module — `tokenpak/proxy/parity_trace.py`

- `ParityTraceRow` dataclass — 27 fields, all optional except `trace_id` / `event_type` / `ts`
- `ParityTraceStore` — SQLite writer for `tp_parity_trace` table; lazy-init; thread-safe (process-wide lock); swallows all errors
- `emit(event_type, *, trace_id, **fields)` — public API; gated on every call by `TOKENPAK_PARITY_TRACE_ENABLED`; O(1) cheap-dict-lookup cost when disabled
- Six lifecycle event constants: `EVENT_HANDLER_ENTRY`, `EVENT_REQUEST_CLASSIFIED`, `EVENT_UPSTREAM_ATTEMPT_START`, `EVENT_UPSTREAM_ATTEMPT_FAILURE`, `EVENT_RETRY_BOUNDARY`, `EVENT_REQUEST_COMPLETION`
- Documented value sets: `RETRY_PHASES`, `RETRY_OWNERS`, `RETRY_SIGNALS`

### 0.2 New SQLite table — `tp_parity_trace`

Lives under the existing `$TOKENPAK_HOME/telemetry.db` (zero new files). 27 columns covering every iter-4-mandated dimension:

- **Process metadata**: `pid`, `ppid`, `tokenpak_home`, `telemetry_db_path`
- **Identity**: `trace_id` (required), `request_id`, `session_id`, `provider`, `auth_plane`, `credential_class`
- **Retry classification (iter-4)**: `retry_phase`, `retry_owner`, `retry_signal`, `retry_count`, `retry_after_seconds`
- **Tool result classification**: `tool_command_first`, `tool_result_stdout_chars`, `tool_result_stderr_chars`, `tool_result_tokens_est`
- **Request size**: `body_bytes`, `companion_added_chars`, `intent_guidance_chars`
- **Concurrency / lane indicators**: `queue_wait_ms`, `lock_wait_ms`, `sqlite_write_ms`
- **Free-form**: `notes` (caller-supplied; privacy-contracted to NEVER contain prompt content)

Three indexes: `(trace_id, ts)`, `(event_type, ts)`, `(session_id, ts)`.

### 0.3 Hook integration — `tokenpak/proxy/server.py::_proxy_to_inner`

Three minimal hook calls, each wrapped in try/except, lazy-imported:

| Hook | Location | Captures |
|---|---|---|
| `EVENT_HANDLER_ENTRY` | server.py:~772, immediately after `_req_id` is generated | every request that enters the proxy handler — fires BEFORE any other processing including auth, route resolution, body read |
| `EVENT_UPSTREAM_ATTEMPT_START` (streaming) | server.py:~1707, just before `pool.stream(...)` | streaming request about to dispatch upstream; carries `body_bytes` |
| `EVENT_UPSTREAM_ATTEMPT_START` (non-streaming) | server.py:~1847, just before `pool.request(...)` | non-streaming request about to dispatch upstream; carries `body_bytes` |

The deliberate scope-bound: I did **not** add hooks for completion (the function is 1500 lines with multiple return paths — risk of behavior drift) or retry-boundary (the FailoverEngine isn't actually wired into the request path; it's library-only — see iter-4 §11 finding). Both are documented as deferred to NCP-3I-v2 if the v1 instrumentation surfaces a need.

### 0.4 Harness extension — `scripts/inspect_session_lanes.py`

Added **dimension 9 (`dim9_parity_trace_coverage`)**. Reads the new table and computes the iter-4 §11 interp-A-vs-B disambiguator:

- `traces_with_handler_entry` — count of distinct trace_ids that emitted `handler_entry`
- `traces_with_completion_in_tp_events` — count joined to existing `tp_events`
- `interp_b_count` = entry – completion → number of requests that fired but never reached completion-time logging
- Verdict: `interp_b_supported` / `interp_a_or_clean` / `indeterminate`

The synthesis section now renders a Q8 line summarizing dim 9.

### 0.5 Tests

- **`tests/test_parity_trace_phase_ncp_3i.py`** — 26 tests across 12 categories: disabled-by-default, entry-without-completion, write-failures-swallowed, privacy contract, schema migration, fetch ordering, multi-event assembly, invalid event types, structural (no dispatch coupling), live env-var toggle, process metadata, concurrent emit
- **`tests/test_ncp3_inspect_session_lanes.py`** — 5 new tests for dim 9: unavailable when table missing, empty when window has no rows, interp-B-supported when entries without completion, interp-A-clean when completions match, synthesis renders Q8

Total NCP-3I + NCP-3 + NCP-1 tests: **51/51 green**. Full PI-x + NCP-x suite: **110/111 green** (1 environmental local failure verified to pass on fresh runner state — see "PI-3 loader fall-through" finding below).

---

## 1. How the operator runs the v1 instrumentation

```bash
# Enable the trace in the operator's environment.
export TOKENPAK_PARITY_TRACE_ENABLED=true

# Restart the proxy (or just re-launch via the launcher; emit() reads the
# env-var on every call, but a fresh proxy process picks up the new
# environment cleanly).
tokenpak start

# Run the NCP-3 §4.2 workload — 2 concurrent `tokenpak claude` sessions.
# (Two terminals, same prompt sequence in both.)

# After both sessions exit:
scripts/inspect_session_lanes.py --window-minutes 30 \
    --output tests/baselines/ncp-3-trace/$(date -u +%Y%m%dT%H%M%SZ).md
```

The harness output now includes a **dim 9** block. The synthesis Q8 line tells the operator one of:

- `iter-4 §11 interp B SUPPORTED` — N entries without completion (the interp B condition is now characterizable)
- `traces with handler_entry, M with tp_events completion — no interp-B gap` (interp A or clean)
- `indeterminate` (need to dig into raw rows)

---

## 2. Privacy contract

The schema's only free-form sink is the `notes` column. The privacy contract is that callers **MUST NOT** put prompt or credential content there. The hook calls in `server.py` only pass:

- `method` (HTTP method — `POST` / `GET` etc.) at handler entry
- `"stream"` or `"request"` (literal strings) at upstream-attempt-start

No request body, header value, or prompt text reaches any column. The privacy test in `test_parity_trace_phase_ncp_3i.py::TestPrivacyContract` pins this — it asserts that a sentinel string in the prompt never appears in any persisted column when the existing hooks fire.

---

## 3. Behavior change — what's "off-path" really means

When `TOKENPAK_PARITY_TRACE_ENABLED` is **unset / false** (the default):

- Each hook site executes one `os.environ.get()` lookup
- The string comparison short-circuits
- `emit()` returns immediately
- No SQLite connection is opened
- No table is created
- The proxy's behavior is byte-identical to pre-NCP-3I

When `TOKENPAK_PARITY_TRACE_ENABLED=true`:

- Each hook site emits one row write to `tp_parity_trace`
- The write is wrapped in `try/except Exception: pass`
- A schema-migration error, a connection failure, a deadlock — all silently swallowed
- Hot-path overhead: a single SQLite INSERT per hook (3 hooks per request, so ~3 INSERTs/request when active)

The proxy continues serving traffic if the trace store fails mid-stream. This is the iter-4 directive's "trace write failures are swallowed" requirement, asserted in `TestWriteFailuresSwallowed`.

---

## 4. What NCP-3I v1 deliberately defers

To keep this PR focused on the iter-4 directive's primary ask:

| Dimension | Deferred reason |
|---|---|
| `companion_added_chars` | Would require hooking into `tokenpak/companion/hooks/pre_send.py` enrichment path. The column exists in the schema; emit() accepts it; but no caller populates it yet. |
| `intent_guidance_chars` | Same as above for `tokenpak/companion/intent_injection.py`. |
| `retry_phase` classification | Would require parsing the request body to detect `tool_result` content. NCP-3I v1's body-bytes capture is enough to know request size; phase classification is v2 scope. |
| `retry_owner` for `claude_code_client` | The CLI's "Retrying in Ns" message comes from a layer the proxy can't introspect. Inferred from absence of a TokenPak-side retry event. |
| `retry_after_seconds` parsing | Would require failover-engine instrumentation. Iter-4 finding: failover engine isn't wired into the request path — instrumenting it captures no real retries. |
| `lock_wait_ms` / `queue_wait_ms` | Would require wrapping every lock acquire / queue put. Conservative for v1; can land in v2 if dim 9 surfaces a concurrency-lane signal that needs deeper attribution. |
| Connection-acceptance event | The Python `BaseHTTPServer` doesn't expose a clean acceptance hook above the socket level. Would need a server-class subclass. Defer until v1 dim 9 confirms whether it's needed. |
| `EVENT_REQUEST_COMPLETION` hook | Would require touching the 1500-line `_proxy_to_inner` at every return path. The harness already joins `tp_parity_trace` to `tp_events.request_id` for the interp-B disambiguator, so we don't need a duplicate completion event. |

Each deferred dimension is documented in the schema (column exists with NULL default), so callers can populate them in v2 without another schema migration.

---

## 5. Side note — PI-3 loader fall-through finding (out of scope)

While testing NCP-3I locally, two existing tests fail when a real `~/.tokenpak/policy.yaml` is present (regardless of `TOKENPAK_HOME=tmp_path` set by the test):

- `test_intent_prompt_intervention_phase_pi_3.py::TestCliRendersAppliedState::test_intervention_status_disabled_default`
- `test_intent_policy_engine_phase21.py::TestConfigDefaults::test_default_config_values`

Root cause: `tokenpak/proxy/intent_policy_config_loader.py` resolves `$TOKENPAK_HOME/policy.yaml` first, then **falls through to `~/.tokenpak/policy.yaml`** if the first doesn't exist. Tests that set `TOKENPAK_HOME=tmp_path` and don't write a sentinel policy.yaml under tmp_path leak the global config.

Verified both pass on a clean runner state (CI is unaffected — runners have no global policy.yaml). Tracked as a small loader-isolation fix worth a separate PR; **out of scope for NCP-3I** because it would be a behavior change.

---

## 6. Acceptance criteria

| Criterion | Status |
|---|---|
| NCP-3I lands as measurement-only instrumentation | ✅ |
| Supports interp B | ✅ via dim 9 + entry-without-completion test |
| Explains whether retries happen before normal `tp_events` completion | ✅ via `interp_b_count` |
| Disabled by default | ✅ env-var unset = false; tested |
| Request-entry trace can exist without completion trace | ✅ tested |
| Trace write failures swallowed | ✅ tested (unwritable path, unknown field, corrupt DB) |
| No raw prompts or secrets stored | ✅ tested (sentinel + auto-population check + structural import scan) |
| `inspect_session_lanes.py` consumes the new fields | ✅ dim 9 + 5 new tests |
| No routing / retry / cache / prompt-mutation / provider / model / auth behavior changes | ✅ structural tests pin no dispatch-primitive imports |
| CI green | ⏸️ pending PR run |

---

## 7. Cross-references

- `tokenpak/proxy/parity_trace.py` — module
- `tokenpak/proxy/server.py::_proxy_to_inner` — three hook insertions (lines ~770, ~1707, ~1847)
- `scripts/inspect_session_lanes.py` — dim 9 added
- `tests/test_parity_trace_phase_ncp_3i.py` — 26 tests
- `tests/test_ncp3_inspect_session_lanes.py` — 5 new dim-9 tests
- `docs/internal/specs/ncp-1a-iteration-4-2026-04-27.md` — iter-4 directive that scoped this phase
- `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md` — NCP-3 plan §3 instrumentation design (extended by iter-4 §6)

---

## 8. After this PR

1. **Operator enables the trace** on the host where the iter-3 / iter-4 condition reproduces:
   ```
   export TOKENPAK_PARITY_TRACE_ENABLED=true
   tokenpak start  # or restart
   ```
2. **Operator re-runs the NCP-3 §4.2 workload** (2 concurrent `tokenpak claude` sessions).
3. **Operator runs the harness:**
   ```
   scripts/inspect_session_lanes.py --window-minutes 30
   ```
4. **Harness's dim 9 + Q8 synthesis line** disambiguates iter-4 §11 interp A vs B.
5. **Next phase routing** depends on the answer:
   - **interp B confirmed** (`interp_b_count > 0`) → next phase is **NCP-3I-v2** to capture the missing `companion_added_chars` / `intent_guidance_chars` / `lock_wait_ms` dimensions for the failing requests, OR jump directly to **NCP-3A** (session-id rotation) / **NCP-9** (OAuth refresh lane) if dim 1 (session collapse) + dim 9 together pinpoint the cause.
   - **interp A** (entries match completions) → the test reproduced on a different host or under different conditions; need fresh trace.
   - **indeterminate** → need to inspect raw rows.

NCP-3I v1 is the **measurement layer** the next implementation phase will read from. It does not itself change behavior.
