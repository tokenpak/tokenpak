# NCP-1 — Claude Code parity A/B test protocol

**Date**: 2026-04-26
**Status**: 🟡 **measurement-only** — no behavior changes proposed
**Workstream**: NCP (Native Client Concurrency Parity)
**Authors**: Sue (protocol) / Kevin (review)
**Companion docs**:
  - Standard proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - Diagnostic plan: `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`

> **Goal:** settle hypotheses **H1 (cache prefix disruption)** and **H2 (session-id collapse)** from NCP-0 by running a side-by-side A/B test against the same Anthropic OAuth account, identical prompts, identical concurrency. Capture the 20-metric measurement contract from Standard #24 §3 (or mark unavailable with a documented reason). Produce a results report. **No code path is modified during the test run.**

---

## 0. Reading guide

| § | Question |
|---|---|
| 1 | What hosts / accounts / tools are needed? |
| 2 | Variant A — native Claude Code: how to capture |
| 3 | Variant B — TokenPak-fronted: how to capture |
| 4 | The 20-metric capture map (per metric: where to get it) |
| 5 | Run order + timing |
| 6 | Generating the diff report |
| 7 | Results template (what to send back) |
| 8 | Failure modes + reproducibility |
| 9 | Out of scope |

---

## 1. Prerequisites

| Item | Notes |
|---|---|
| Anthropic OAuth account | Same account for both variants. `~/.claude/.credentials.json` populated. |
| `claude` CLI | Same version on both runs. Capture via `claude --version`. |
| TokenPak | Built from `main` at or after PR #62 (NCP-0 closeout). `tokenpak --version` ≥ the version that includes commit `ccc1a3a259`. |
| Optional: mitmproxy | For variant A. Used to capture upstream `Retry-After`, `anthropic-ratelimit-*` headers, and `usage` blocks on streaming responses. |
| Telemetry DB | `~/.tokenpak/telemetry.db` — exists after first TokenPak invocation. The capture script reads from it. |
| Disk | ~5 MB for two baselines + one diff report. |
| Time | ~30 minutes for an unsaturated A/B. ~2–4 hours for a saturated A/B that triggers 429s. |

**Test stage MUST be a clean shell session** (no leftover environment from prior runs). Set `TOKENPAK_HOME` to a fresh directory if you want to keep the parity telemetry separate from the operator's normal traffic:

```bash
export TOKENPAK_HOME=$HOME/.tokenpak-ncp1
mkdir -p "$TOKENPAK_HOME"
```

---

## 2. Variant A — native Claude Code (no TokenPak in path)

The native CLI talks directly to `api.anthropic.com`. TokenPak is **not** in the path — its telemetry won't see this run. The operator captures observations out-of-band.

### 2.1 Setup

```bash
unset ANTHROPIC_BASE_URL          # ensure no proxy redirect
unset CLAUDE_BASE_URL
which claude                      # verify the CLI binary
claude --version                  # record the version
```

### 2.2 Two capture options

#### Option A.1 — mitmproxy (recommended)

```bash
# In one terminal: start mitmproxy on 8080 with a recording log.
mitmdump -p 8080 -w "$HOME/ncp1-native-$(date -u +%Y%m%dT%H%M%SZ).flow"

# In another terminal: route the CLI through mitmproxy.
export HTTPS_PROXY=http://localhost:8080
export REQUESTS_CA_BUNDLE=$HOME/.mitmproxy/mitmproxy-ca-cert.pem
export SSL_CERT_FILE=$HOME/.mitmproxy/mitmproxy-ca-cert.pem
claude  # run the workload (see §5)
```

Each captured request preserves: full headers (`Retry-After`, `anthropic-ratelimit-*`, `X-Claude-Code-Session-Id`), full response body (the `usage` block), wall-clock timing.

After the run, post-process with a small jq / mitmproxy script (or by hand) to extract per-request: `session_id`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `Retry-After`, status code, latency.

#### Option A.2 — `claude` CLI debug logging (lighter weight, less complete)

```bash
ANTHROPIC_LOG=debug claude  # workload
```

The CLI prints request + response logs to stderr. Capture to a file. **Note:** debug output may not include `usage` cache fields on streaming responses; mitmproxy is more reliable for cache-hit-ratio.

### 2.3 Hand-fill the native baseline

Use `scripts/capture_parity_baseline.py --label native` to seed an empty template, then hand-edit:

```bash
scripts/capture_parity_baseline.py \
    --label native \
    --window-days 1 \
    --output tests/baselines/ncp-1-parity/native-$(date -u +%Y%m%dT%H%M%SZ).json
```

Open the resulting JSON. For each metric in §4 you have data for, replace the `null` value. Leave metrics you couldn't capture as `null` — the diff script will flag them.

The minimum fields to fill in for H1+H2 settling:

- `metrics.cache_creation_tokens` (sum across the run)
- `metrics.cache_read_tokens` (sum across the run)
- `metrics.cache_hit_ratio` (= `cache_read_tokens / (cache_read_tokens + cache_creation_tokens)`)
- `metrics.input_tokens`, `metrics.output_tokens`, `metrics.request_count`, `metrics.429_count` (for context)
- `session.distinct_session_id_count` (count of distinct `X-Claude-Code-Session-Id` headers across the run)
- `session.session_id_rotations_per_hour` (= `distinct_session_id_count / wall_clock_hours`)
- `session.session_ids_truncated` (first 10 distinct ids — for sanity check that they're really different)

---

## 3. Variant B — TokenPak Companion Claude Code (proxy in path)

The CLI talks to the local TokenPak proxy, which talks to `api.anthropic.com`. TokenPak telemetry sees every request; the capture script reads from `~/.tokenpak/telemetry.db`.

### 3.1 Setup

```bash
# Start TokenPak (or verify it's already running).
tokenpak serve &        # or however the operator normally starts it
sleep 2
curl -s http://127.0.0.1:8765/healthz   # sanity check

# Point the CLI at TokenPak.
export ANTHROPIC_BASE_URL=http://127.0.0.1:8765
unset HTTPS_PROXY                       # don't double-proxy
claude                                  # run the workload (see §5)
```

### 3.2 Capture from telemetry.db

After the run completes, run the capture script:

```bash
scripts/capture_parity_baseline.py \
    --label tokenpak \
    --window-days 1 \
    --output tests/baselines/ncp-1-parity/tokenpak-$(date -u +%Y%m%dT%H%M%SZ).json
```

Inspect the JSON. Metrics that are unavailable have a sibling `_unavailable` entry explaining why.

---

## 4. The 20-metric capture map

This table maps each Standard #24 §3 metric to (a) where the operator captures it, (b) what to do if unavailable. Every row marked **AUTO** is filled in by `capture_parity_baseline.py` from existing telemetry. Every row marked **MANUAL** must be hand-filled or skipped.

| # | Metric | Variant A (native) | Variant B (tokenpak) | Notes |
|---|---|---|---|---|
| 1 | `request_count` | MANUAL — count requests in mitmproxy log | AUTO — `tp_events` row count | — |
| 2 | `retry_count` | MANUAL — count retried requests in mitmproxy | AUTO — `tp_events.error_class='retry'` (lower bound; current schema doesn't tag every retry) | NCP-1 limitation: TokenPak retry bookkeeping is incomplete; treat as lower bound |
| 3 | `429_count` | MANUAL — count `429 Too Many Requests` in mitmproxy | AUTO — `tp_events.status='429'` | — |
| 4 | `5xx_count` | MANUAL — count 500–599 in mitmproxy | AUTO — `tp_events.status` filter | — |
| 5 | `latency_ms` (p50 / p95 / p99) | MANUAL — request timing in mitmproxy | AUTO — `tp_events.duration_ms` percentiles | — |
| 6 | `time_to_first_token_ms` | MANUAL — first SSE chunk timestamp - request send timestamp | UNAVAILABLE — proxy stream layer doesn't record TTFT | NCP-1+ instrumentation phase will fix |
| 7 | `input_tokens` | MANUAL — sum of `usage.input_tokens` from response bodies | AUTO — `tp_usage.input_billed` (or `input_est`) | — |
| 8 | `output_tokens` | MANUAL — sum of `usage.output_tokens` | AUTO — `tp_usage.output_billed` | — |
| 9 | `cache_creation_tokens` | MANUAL — sum of `usage.cache_creation_input_tokens` | AUTO — `tp_usage.cache_write` | **H1 critical** |
| 10 | `cache_read_tokens` | MANUAL — sum of `usage.cache_read_input_tokens` | AUTO — `tp_usage.cache_read` | **H1 critical** |
| 11 | `companion_added_chars` | N/A (no companion in path) | UNAVAILABLE — pre-send hook doesn't log additionalContext length | Set to `null` for both variants in NCP-1; capture in NCP-1+ instrumentation |
| 12 | `companion_added_tokens_est` | N/A | UNAVAILABLE — derived from #11 | Same as #11 |
| 13 | `vault_injection_chars` | N/A | UNAVAILABLE — vault retrieval doesn't log result-set length | Set to `null` for both; instrument in NCP-1+ |
| 14 | `capsule_injection_chars` | N/A | UNAVAILABLE — capsule loader doesn't log loaded-bytes | Same |
| 15 | `intent_guidance_chars` | N/A | AUTO — `intent_patches.patch_text` length (when PI-3 applied) | — |
| 16 | `hook_triggered_calls` | N/A | UNAVAILABLE — companion hook dispatcher doesn't emit count | Logically zero by H8 |
| 17 | `extra_background_calls` | N/A | AUTO = 0 (H8 ruled out) | — |
| 18 | `retry_after_seconds` (per 429) | MANUAL — `Retry-After` header on 429 responses in mitmproxy | UNAVAILABLE — proxy doesn't parse `Retry-After` (H4) | NCP-5 will fix; capture native side via mitmproxy for H4 evidence |
| 19 | `ratelimit_tokens_remaining` | MANUAL — `anthropic-ratelimit-tokens-remaining` from mitmproxy | UNAVAILABLE — proxy doesn't capture rate-limit headers | — |
| 20 | `ratelimit_requests_remaining` | MANUAL — `anthropic-ratelimit-requests-remaining` from mitmproxy | UNAVAILABLE — same | — |

### Session-block metrics

| # | Metric | Variant A | Variant B | Notes |
|---|---|---|---|---|
| S1 | `distinct_session_id_count` | MANUAL — count distinct `X-Claude-Code-Session-Id` in mitmproxy | AUTO — `SELECT COUNT(DISTINCT session_id) FROM tp_events` | **H2 critical** |
| S2 | `session_id_rotations_per_hour` | MANUAL — `distinct_session_id_count / wall_clock_hours` | AUTO — same formula | **H2 critical** |
| S3 | `session_ids_truncated` (first 10) | MANUAL — copy from mitmproxy | AUTO — first 10 distinct ids | — |

The H1 verdict is computed from #9 + #10. The H2 verdict is computed from S1 + S2. Both can be settled with the MANUAL-marked subset above; the `UNAVAILABLE` rows are useful but not required for NCP-1.

---

## 5. Run order + timing

### 5.1 Quick (cache hit ratio test, settles H1)

**Goal:** measure cache hit ratio with identical prompt sequences.

**Workload:** 10 sequential identical prompts, single session, no concurrency. Total: ~3 minutes.

1. **Native run.** `unset ANTHROPIC_BASE_URL` + start mitmproxy + `claude` + run the prompt sequence. Stop mitmproxy.
2. **TokenPak run.** Start TokenPak proxy + `export ANTHROPIC_BASE_URL=http://127.0.0.1:8765` + `claude` + run the same prompt sequence. Stop the CLI.
3. Wait 60 seconds (let TokenPak telemetry flush).
4. Run both `capture_parity_baseline.py` invocations.
5. Hand-fill the native JSON (see §2.3).
6. Run `diff_parity_baselines.py`.

### 5.2 Saturated (settles H2)

**Goal:** measure 429 onset under N concurrent sessions.

**Workload:** N parallel CLIs, each looping identical prompts every 5 seconds for 10 minutes. Recommended N: start with 5; if no 429s fire, double to 10.

1. **Native:** spawn N parallel `claude` CLIs (each its own process, no shared session-id). Capture all via mitmproxy. Record the request count when the first 429 fires per CLI.
2. **TokenPak:** spawn N parallel `claude` CLIs all pointed at the same TokenPak proxy. The proxy synthesizes one shared `X-Claude-Code-Session-Id`. Record total 429 count.
3. Capture both baselines as in §5.1.
4. Run the diff script.

The H2 verdict surfaces as: "TokenPak's `session_id_rotations_per_hour` is < `1/H2_SESSION_ROTATION_RATIO_THRESHOLD` of native's" (default threshold ratio: 3×).

### 5.3 Order matters — but cache state, not bucket state

Run native FIRST, TokenPak SECOND when comparing cache hit ratio (so neither variant has a 'warmed up' cache from the other). For the saturated H2 test, run them with at least 1 hour of recovery time between (Anthropic's rate-limit windows reset on a sliding hourly basis).

---

## 6. Generating the diff report

```bash
scripts/diff_parity_baselines.py \
    --native tests/baselines/ncp-1-parity/native-<TIMESTAMP>.json \
    --tokenpak tests/baselines/ncp-1-parity/tokenpak-<TIMESTAMP>.json \
    --output tests/baselines/ncp-1-parity/results-<TIMESTAMP>.md
```

The script emits a markdown report (or `--json` for the raw verdict). Every verdict is one of:

- `supported` — the data clears the threshold (H1: cache_hit delta ≥ 0.30; H2: rotation ratio ≥ 3×)
- `not_supported` — the data is within parity
- `inconclusive` — at least one side has missing data (the diff script tells you which)

The synthesis maps the (H1, H2) pair to a dominant cause:

| H1 | H2 | Dominant cause | Confidence | NCP-x fix |
|---|---|---|---|---|
| supported | supported | H1+H2 both | high | NCP-2 (cache) + NCP-3 (session) |
| supported | not_supported | H1 only | high | NCP-2 (cache) |
| not_supported | supported | H2 only | high | NCP-3 (session) |
| not_supported | not_supported | neither | medium | re-run with H3/H4 evidence |
| any inconclusive | any | inconclusive | low | fill gap, re-run |

---

## 7. Results template

Send this back to the workstream for NCP-2 ratification. Keep it short.

```markdown
# NCP-1 results — <YYYY-MM-DD>

**Operator**: <name>
**Anthropic account**: <account-handle>
**TokenPak version**: <output of tokenpak --version>
**Claude CLI version**: <output of claude --version>
**Workload**: <one of: §5.1 quick / §5.2 saturated / custom>
**Wall-clock window**: <ISO-8601 start> → <ISO-8601 end>

## Verdicts

- **H1 (cache prefix disruption)**: <supported | not_supported | inconclusive>
  - native cache_hit_ratio: <0.0–1.0>
  - tokenpak cache_hit_ratio: <0.0–1.0>
  - delta: <number>
- **H2 (session-id collapse)**: <supported | not_supported | inconclusive>
  - native session_id_rotations_per_hour: <number>
  - tokenpak session_id_rotations_per_hour: <number>
  - ratio: <number>×

## Dominant cause

<copy-paste from the diff script's "Synthesis" block>

## Confidence

<copy-paste — high / medium / low>

## Recommended NCP-2 / NCP-3 fix direction

<copy-paste from the diff script's "Recommended next step" block>

## Out-of-band observations

<free-form: any anomalies the operator noticed during the run that
the metrics don't capture — e.g. "request 47 of native run hit a
500 from Anthropic; not counted in tokenpak side because TokenPak
wasn't in the path">

## Attached files

- `tests/baselines/ncp-1-parity/native-<TIMESTAMP>.json`
- `tests/baselines/ncp-1-parity/tokenpak-<TIMESTAMP>.json`
- `tests/baselines/ncp-1-parity/results-<TIMESTAMP>.md`
- (optional) raw mitmproxy capture file
```

---

## 8. Failure modes + reproducibility

### 8.1 Common gotchas

- **`HTTPS_PROXY` left set across runs.** Set it for variant A, **unset** for variant B. The TokenPak proxy is reached via `ANTHROPIC_BASE_URL`, not `HTTPS_PROXY`.
- **mitmproxy CA cert not installed.** Symptom: SSL handshake errors from the CLI. Run `mitmproxy --version` once to generate the CA, then point `REQUESTS_CA_BUNDLE` + `SSL_CERT_FILE` at it.
- **Telemetry from prior runs polluting the window.** Use `--window-days <small>` to scope, or set a fresh `TOKENPAK_HOME` for the test.
- **CLI session-id stability.** The `claude` CLI emits a new `X-Claude-Code-Session-Id` on every invocation. Native variant: kill + restart the CLI between sub-runs to force rotation. TokenPak variant: the proxy collapses to one id regardless.
- **Anthropic rate-limit window.** If you hit a 429 in variant A, **wait an hour** before starting variant B, or the tokenpak side will be measuring against a depleted bucket.

### 8.2 Reproducibility

The protocol is reproducible when:

- The two `*.json` baselines + the diff `*.md` are checked into `tests/baselines/ncp-1-parity/`.
- The operator's command history is captured (e.g. `script(1)` recorded the shell session).
- The workload script (the prompt sequence) is captured in the results.
- The TokenPak version + Claude CLI version are pinned in the results.

A second operator running the same workload with the same TokenPak / Claude versions should reach the same H1 / H2 verdicts (within the threshold's noise floor).

---

## 9. Out of scope for NCP-1

- ❌ Implementing measurement instrumentation (NCP-1+ instrumentation phase). The directive says: capture or mark unavailable. NCP-1 marks 8 of 20 metrics unavailable.
- ❌ Changing companion behavior (vault / capsule / intent injection unchanged).
- ❌ Changing proxy behavior (failover, connection pool, retry, credential injection unchanged).
- ❌ Changing session-id behavior (NCP-3 fix; NOT in NCP-1).
- ❌ Changing cache placement (NCP-2 fix; NOT in NCP-1).
- ❌ Implementing `tokenpak doctor --parity` CLI (NCP-1+ instrumentation phase).
- ❌ Other interactive clients (Codex, Cursor) — the standard generalizes; the protocol is Claude Code only.

NCP-1 is **strictly observational + operator-run.** No code path changes.

---

## 10. Cross-references

- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — the standard proposal whose §3 measurement contract this protocol exercises
- `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md` — the diagnostic plan, including §5 A/B test methodology that this protocol concretizes
- `scripts/capture_parity_baseline.py` — capture script
- `scripts/diff_parity_baselines.py` — diff script
- `tests/test_ncp1_parity_baseline.py` — unit tests for both scripts

---

## 11. Acceptance criteria

NCP-1 is **complete** when:

- [x] The capture script exists and reads existing telemetry without modifying it.
- [x] The diff script exists and produces a results report from two baselines.
- [x] The 20-metric measurement contract is enumerated; available metrics auto-fill from telemetry; unavailable metrics carry a documented reason.
- [x] The protocol doc enumerates how to capture each metric for each variant.
- [x] A results template exists.
- [x] No runtime / classifier / routing / retry / cache-placement / session-id behavior changes.
- [ ] CI green on the closeout PR.

After NCP-1 is closed, the next step is the **operator running the §5.1 + §5.2 tests** and submitting the §7 results template. NCP-2 / NCP-3 implementation does not begin until those results are in.
