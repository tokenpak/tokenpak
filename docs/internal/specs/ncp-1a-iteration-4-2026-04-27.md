# NCP-1A iteration 4 — retry localizes to post-tool-result continuation

**Date**: 2026-04-27
**Status**: 🟡 **phase-localized evidence** — recommended next phase is **NCP-3I** (in-proxy instrumentation) per directive
**Workstream**: NCP (Native Client Concurrency Parity)
**Operator**: Kevin
**Companion docs**:
  - Iteration 1 (1v1 baseline): `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md`
  - Iteration 2 (2-TP concurrent degraded): `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md`
  - Iteration 3 (2 TP retry + 1 native healthy concurrently): `docs/internal/specs/ncp-1a-iteration-3-2026-04-27.md`
  - NCP-3 diagnostic plan: `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md`

> **Headline:** the retry message `Retrying in 8s · attempt 4/10` was captured immediately after a local `Bash` tool result containing `git log` + `git status` + untracked-file output. **The failure phase is post-tool-result model continuation, not initial prompt dispatch.** This sharpens the picture: amplification of input tokens by the tool result + companion context + intent guidance + next-turn prompt may be pushing the request into a tier where retry / rate-limit fires.

---

## 1. Operator-supplied evidence (iter-4)

| Field | Value |
|---|---|
| TUI message captured | `Retrying in 8s · attempt 4/10` |
| Phase observed | **immediately after** a local Bash/tool result |
| Tool result content (approximate) | `git log` output, `git status` output, untracked `tests/baselines/ncp-3-trace/` |
| Variant | TokenPak Claude Code (companion + proxy in path) |
| Concurrent native session | (parallel observation from iter-3 — native healthy) |

**Interpretation (per directive):** the retry occurs after a tool round-trip, when Claude Code packages the tool result and sends a continuation request back through TokenPak to the upstream OAuth/subscription endpoint. The failure mode is **post-tool-result continuation**, not initial dispatch.

This phase distinction matters because:

- The **continuation request** carries: prior turn(s) + tool call + **tool result body** + companion-added context + (optionally) PI-3 intent guidance + next-turn user prompt.
- The **initial prompt** is much smaller: just system + first user turn (+ companion-added context).
- A `git log` + `git status` + directory listing easily adds 2–10 KB of tool-result text. That's roughly 500–2 500 tokens of input on the continuation, which can move the request into a different tier of cache-prefix behavior, refresh-fairness, or rate-limit attribution than the initial prompt.

The "Retrying in 8s · attempt N/10" format is the **Claude CLI's own retry loop** (TokenPak's failover engine uses different format and doesn't expose attempt counts). So `retry_owner=claude_code_client` is the leading interpretation — **but we cannot confirm without the new instrumentation** because TokenPak telemetry does not currently distinguish CLI-side retries from upstream-side or proxy-failover retries.

---

## 2. New diagnostic dimensions (per directive)

The following dimensions are required to discriminate the post-tool-result phase from initial-dispatch behavior. None are observable in current `tp_events` / `tp_usage` schema — see §3 routing logic.

### 2.1 `retry_phase`

Classifies which point in the conversation lifecycle the retry occurred at:

| Value | Meaning |
|---|---|
| `initial_user_prompt` | First request of a session — system + user message + (optional) companion context |
| `post_tool_result` | After a tool call completed and Claude Code sent the tool result back for continuation |
| `streaming_continuation` | Mid-stream chunk delivery (server-sent events), distinct from terminal status |
| `message_stop_finalization` | After `stop_reason` resolved; rare retry phase |
| `unknown` | Phase not identifiable from the request shape |

### 2.2 `tool_result_size`

Captures the size of any tool result included in the failing request:

| Sub-field | Type |
|---|---|
| `stdout_chars` | int — characters of stdout in the tool result |
| `stderr_chars` | int — characters of stderr |
| `tool_result_tokens_est` | int — heuristic token estimate (chars / 4) |
| `tool_command` | string — command name/type (`bash`, `read_file`, `web_fetch`, etc.) |

### 2.3 `request_size_before_retry`

Captures the size of the request that triggered the retry — distinct from the post-retry request:

| Sub-field | Type |
|---|---|
| `input_tokens` | int — billed input tokens (from upstream `usage` if available) |
| `body_bytes` | int — wire-bytes of the request body |
| `companion_added_chars` | int — chars added by the companion pre-send hook |
| `intent_guidance_chars` | int — chars added by PI-3 `apply_patch_to_companion_context` (0 when prompt_intervention disabled or no patch applicable) |

### 2.4 `retry_owner`

Identifies which layer initiated the retry attempt:

| Value | Meaning |
|---|---|
| `claude_code_client` | The `claude` CLI's own retry loop (e.g. `Retrying in Ns · attempt N/M` style messages) |
| `tokenpak_proxy` | TokenPak's failover engine fired the retry (per `failover_engine.py:42`) |
| `upstream_provider` | The upstream returned a transient response that wasn't surfaced to the client (rare) |
| `unknown` | Cannot attribute |

### 2.5 `retry_signal`

The trigger that caused the retry:

| Value | Meaning |
|---|---|
| `429` | Upstream rate-limit response |
| `5xx` | Upstream server error (500–599) |
| `timeout` | Read or connect timeout |
| `connection_reset` | TCP-level reset / EOF mid-stream |
| `retry_after_header` | Honored a `Retry-After` header |
| `unknown` | Signal not classifiable |

---

## 3. Existing-telemetry coverage check

Per the directive:

> If existing telemetry cannot identify retry_phase, retry_owner, or request_size_before_retry, route next phase to NCP-3I in-proxy instrumentation.

Schema audit against the `tp_events` columns inventoried in NCP-3 plan §2:

| Required dimension | Available in `tp_events` / `tp_usage`? |
|---|---|
| `retry_phase` | ❌ No column distinguishes initial vs continuation requests. The `api='messages'` column is present but identical for both phases. |
| `retry_owner` | ❌ `error_class='retry'` exists but does not distinguish CLI-side vs proxy-side vs upstream-side. |
| `request_size_before_retry` | ❌ `tp_usage.input_billed` captures total input tokens **after** the retry resolved; it does NOT distinguish the failing request size from the retry-completed request size. The companion_added_chars is not captured at all. |
| `tool_result_size` | ❌ Not captured. |
| `retry_signal` | Partial — `tp_events.status` records the HTTP code that completed the request, but transient retry triggers (timeouts, connection resets, intermediate 429s) are not separately logged. |

**Conclusion: existing telemetry is insufficient.** Per the directive's last clause, the recommended next phase is **NCP-3I — in-proxy instrumentation**.

---

## 4. Updated hypothesis ranking (per directive)

| Rank | Hypothesis | Status post-iter-4 | Notes |
|---:|---|---|---|
| **1** | **H4** retry amplification under concurrent TP sessions | **HIGH (promoted from #3)** | Iter-4 directly localized to the retry message. CLI is showing attempt 4/10, indicating multiple retries are firing on a continuation request. |
| **1** | **H9b** OAuth refresh / shared credential lane | **HIGH (unchanged)** | A long-running tool-using session crosses OAuth-token TTL boundaries; a refresh during continuation would amplify a phase-specific failure. |
| **1** | **H2** session / session-id / lane collapse | **HIGH (unchanged)** | Multiple TokenPak sessions on one wire-side session-id concentrate continuation requests. |
| **2** | **H3** token / tool-output amplification (post-tool-result-specific) | **MEDIUM-HIGH (promoted from MEDIUM)** | New iter-4 evidence: tool result body amplifies the continuation request. If H3 dominates *only* in this phase, the fix is phase-aware (e.g., omit companion enrichment on continuation requests with large tool results). |
| **3** | **H9a / H9c** pool lock / rotation lock | MEDIUM | Unchanged. |
| **4** | **H1** cache prefix disruption | SECONDARY | Iter-4 doesn't strengthen H1; tool result text is volatile by nature, but cache-prefix would have hit the initial prompt too — yet initial prompts seem fine. |
| **5** | **H9d** SQLite telemetry lane | LOW | Unchanged. |
| RULED OUT | H8 companion-side model calls | unchanged from NCP-0 | — |

The three top-tier HIGH hypotheses (H2, H4, H9b) now all carry a phase-specific contributor: **the post-tool-result continuation is the failure phase**, regardless of which mechanism dominates within that phase.

---

## 5. Updated NCP-3 §6 decision tree (additive)

The NCP-3 plan §6 decision tree (Q1–Q6) is augmented with a **Q7 retry-phase question**. Q7 takes precedence over Q2–Q6 when iter-4-style evidence is present, because the phase classification narrows the implementation surface.

```
Q7: Was the failing request a post-tool-result continuation?
    YES → the failure phase is phase-localized (iter-4)
        → if existing telemetry cannot identify retry_phase /
          retry_owner / request_size_before_retry
          (per §3 above)
            → route to NCP-3I (in-proxy instrumentation)
            → the instrumentation MUST include the iter-4 dimensions
              (retry_phase, tool_result_size, request_size_before_retry,
               retry_owner, retry_signal)
        → else if telemetry already settles the phase
            → continue to Q1–Q6 with phase-filtered data
              (e.g. consider only post_tool_result rows)
    NO  → continue to Q1–Q6 with the original NCP-3 §6 logic
    PENDING → record as "phase classification needed"; route to
              NCP-3I to enable Q7 in future runs
```

The original Q1–Q6 are still useful AFTER Q7 — once instrumentation lands and we can filter to `retry_phase = post_tool_result`, the existing tree (Q1 session collapse, Q2 staggered test, Q3 retry count, etc.) re-applies on the phase-filtered subset.

---

## 6. NCP-3I instrumentation update — additive dimensions

The NCP-3 plan §3.1 already proposed an in-proxy instrumentation phase ("NCP-3I") with columns like `process_id`, `lane_id`, `lock_wait_ms`, etc. This iter-4 update **extends** that proposed column set with the iter-4 dimensions:

| Column | Type | Purpose |
|---|---|---|
| `retry_phase` | TEXT | One of {`initial_user_prompt`, `post_tool_result`, `streaming_continuation`, `message_stop_finalization`, `unknown`} — classified at request-handler entry |
| `tool_result_stdout_chars` | INTEGER | Sum of stdout in tool results in this request, when classifiable |
| `tool_result_stderr_chars` | INTEGER | Same for stderr |
| `tool_result_tokens_est` | INTEGER | Heuristic tokens (chars/4) |
| `tool_command_first` | TEXT | Name of the first tool referenced in the request body |
| `body_bytes` | INTEGER | Raw wire bytes of the request body sent upstream |
| `companion_added_chars` | INTEGER | Chars added by `pre_send.py` enrichment (already proposed in NCP-3 §3.1; carried forward) |
| `intent_guidance_chars` | INTEGER | Chars added by PI-3 `apply_patch_to_companion_context` (extended) |
| `retry_owner` | TEXT | One of {`claude_code_client`, `tokenpak_proxy`, `upstream_provider`, `unknown`} |
| `retry_signal` | TEXT | One of {`429`, `5xx`, `timeout`, `connection_reset`, `retry_after_header`, `unknown`} |
| `retry_after_seconds` | INTEGER | Already proposed in NCP-3 §3.1 |

All writes follow the NCP-3 §3 contract: **off-path** (`try/except: pass`), additive ALTER TABLE columns (PI-3 schema-migration pattern), gated behind a `TOKENPAK_PARITY_TRACE_ENABLED` env-var (default `false`) — zero behavior change in production until the operator opts in.

The CLI / test design for NCP-3I lands in its own ratification PR. This iter-4 doc only **specifies** what needs to be added; nothing is implemented here.

---

## 7. Recommended next phase

Per the directive's routing rule and the §3 telemetry-coverage gap:

> **NCP-3I — in-proxy instrumentation phase.**

Specifically: implement the NCP-3 §3.1 proposed columns + the iter-4 §6 additive columns; gate behind `TOKENPAK_PARITY_TRACE_ENABLED`; emit on every Claude Code request handler path; verify writes are off-path and non-blocking. Then re-run §4.2 of the NCP-3 plan + the iter-3-style 2-TP-concurrent workload, and route the next phase via §5's updated Q7-first decision tree.

**This recommendation is gated on explicit Kevin approval per the standing acceptance gate.** No code is implemented in this iter-4 PR.

---

## 8. Held throughout

Per the directive's iter-4 acceptance criteria:

- ❌ No routing behavior changes
- ❌ No retry behavior changes
- ❌ No cache behavior changes
- ❌ No prompt mutation changes
- ❌ No provider/model changes
- ❌ No auth behavior changes
- ❌ No production behavior changes
- ❌ No new SQLite columns or tables in this PR (NCP-3I is the next-phase deliverable; design extended here, code deferred)
- ❌ No imports of dispatch / credential-injector primitives
- ✅ Tests still 20/20 NCP-3 + 28/28 NCP-1 + 32/32 PI-3 green (verified pre-commit)

---

## 9. Status snapshot

| Item | State |
|---|---|
| Iter-4 evidence (post-tool-result retry localization) | ✅ recorded |
| 5 new diagnostic dimensions | ✅ specified (§2) |
| Existing-telemetry coverage check | ✅ insufficient (§3) |
| Hypothesis re-ranking (H4 promoted to #1; H3 promoted MEDIUM→MEDIUM-HIGH) | ✅ |
| NCP-3 §6 decision-tree augmentation (Q7 retry-phase) | ✅ documented |
| NCP-3I instrumentation column-set extension | ✅ designed |
| Recommended next phase | ✅ **NCP-3I** (per directive routing rule) |
| Implementation | ⛔ frozen — awaiting explicit Kevin approval for NCP-3I |
| C/D test variants from iter-1 | ⏸️ pending; phase-classification will need to be applied if/when they're run |

---

## 11. Empty-harness-but-visible-retry condition (post-write addendum)

After this iter-4 doc was drafted, three harness JSON snapshots from
`tests/baselines/ncp-3-trace/` (timestamps 2026-04-27 12:36 / 12:38 /
12:43 UTC, filename suffix `-2tp-retry-native-healthy`) were inspected.
All three report:

- `claude_code_event_count = 0`
- `dim1_session_collapse.verdict = "no_data"`
- `dim5_provider_audit.distribution = {}`
- `dim6_retry_count.retry_event_lower_bound = 0`

**Visible TUI retries + zero TokenPak telemetry rows** is itself a
diagnostic finding. There are two interpretations:

### Interpretation A — different host

The captures were produced on the TokenPak development host (which
this iteration was authored on); Kevin's actual concurrent-TUI test
likely ran on a separate operator host with its own proxy + telemetry.
Re-running the harness on the operator host would surface the rows.

This is the "harness ran on the wrong machine" case — straightforward
and resolved by pointing the harness at the right `~/.tokenpak/telemetry.db`.

### Interpretation B — pre-handler failure (HIGH-impact for NCP-3I scope)

If the captures were produced on the same host the test ran on, **the
absence of `tp_events` rows for failing requests is itself H9a-class
evidence**: the failures are happening *before* TokenPak's request
handler completes a write. Specifically:

- TokenPak writes `tp_events` rows at request completion (see
  `tokenpak/proxy/monitor.py` queued writer).
- If the CLI's local retry fires because the proxy hasn't accepted /
  processed the connection in time (pool lock contention, OAuth
  refresh waiter queue, etc.), the failing request **never completes a
  TokenPak log entry**.
- The visible "Retrying in 8s · attempt 4/10" message is then
  attributed to `retry_owner = claude_code_client` operating on a
  request that **TokenPak's existing telemetry cannot see**.

This interpretation strengthens the NCP-3I recommendation in two ways:

1. **NCP-3I instrumentation must hook at connection-acceptance time,
   not just request-handler-completion time.** Otherwise the
   instrumentation will continue to miss the very failures it's
   designed to characterize. Specifically:
     - Add a row to `tp_events` (or a new `tp_request_attempts` table)
       at the moment the proxy ACCEPTS a connection / starts handling
       a request, not just when it completes.
     - Stamp `retry_phase`, `retry_owner` (provisionally
       `claude_code_client` if no upstream response was received),
       `retry_signal` (`timeout` / `connection_reset` / `unknown` for
       pre-completion failures).
2. **H9a (pool lock) ranking should rise.** If interpretation B is
   confirmed, H9a is no longer "MEDIUM" but a strong candidate
   alongside H4 / H9b / H2 — the TP proxy is slow to accept second
   concurrent connections, and the CLI's local retry compensates.

### Recommendation given the ambiguity

Before NCP-3I implementation: **operator confirms which interpretation
applies** by running, on the same host as the failing test:

```bash
# On the host where the 2 TP TUI sessions retried:
ls -la ~/.tokenpak/telemetry.db          # verify the file exists
sqlite3 ~/.tokenpak/telemetry.db \
    "SELECT COUNT(*) FROM tp_events
     WHERE ts > strftime('%s','now','-30 minutes')"
# (use sqlite3 if available, or run the harness against the live
# telemetry.db on that host)
```

If the count is **non-zero** but the harness shows zero Claude-Code-shaped
rows, the test is interpretation A (different host AND/OR provider-slug
mismatch — see iter-4 §3 of the I-0 audit).

If the count is **zero**, interpretation B is supported and NCP-3I
must include connection-acceptance-time instrumentation in addition to
request-completion-time instrumentation.

**Either way, NCP-3I remains the correct next phase** — the difference
is what NCP-3I must specifically instrument.

## 10. Cross-references

- `docs/internal/specs/ncp-1a-iteration-1-2026-04-27.md` — 1v1 baseline + ABCD plan
- `docs/internal/specs/ncp-1a-iteration-2-2026-04-27.md` — 2-TP-only degraded
- `docs/internal/specs/ncp-1a-iteration-3-2026-04-27.md` — 2 TP retry + 1 native healthy
- `docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md` — NCP-3 diagnostic plan; §3 instrumentation design (extended by iter-4 §6); §6 decision tree (augmented by iter-4 §5)
- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — Standard #24, invariants I-0 / I-3 / I-6
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — H2 / H9b / H9c surface
- `tokenpak/proxy/connection_pool.py` — H9a surface
- `tokenpak/proxy/failover_engine.py` — H4 surface; the `retry_owner=tokenpak_proxy` source
- `tokenpak/companion/hooks/pre_send.py` — companion enrichment (where `companion_added_chars` instrumentation would land)
- `tokenpak/companion/intent_injection.py` — PI-3 application library (where `intent_guidance_chars` instrumentation would land)
