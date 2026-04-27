# NCP-1R — Claude Code OAuth/Subscription parity test protocol (PRIMARY)

**Date**: 2026-04-26 (NCP-1R revision)
**Status**: 🟡 **measurement-only** — no behavior changes proposed
**Workstream**: NCP (Native Client Concurrency Parity)
**Authors**: Sue (protocol) / Kevin (review + auth-plane scoping correction)
**Companion docs**:
  - Standard proposal: `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md`
  - Diagnostic plan: `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md`
  - **Secondary** protocol (harness validation only): `docs/internal/specs/ncp-1-ab-test-protocol-2026-04-26.md`

> **Goal:** settle hypotheses **H1 (cache prefix disruption)** and **H2 (session-id collapse)** plus the new NCP-1R parity invariants **I-0 (auth-plane)**, **I-3 (session)**, and **I-6 (retry)** by running a side-by-side native-vs-TokenPak A/B test on the **same Claude Code OAuth/subscription account**, with identical workload. **No code path is modified during the test run.**

> **What changed from NCP-1**: the original protocol's mitmproxy-based capture was scoped against generic Anthropic API-key traffic. That cannot answer the OAuth/subscription parity question — different auth plane, different bucket, different rate-limit attribution. NCP-1R replaces the runbook with an OAuth/subscription-only protocol that observes CLI-side behavior + TokenPak telemetry.

---

## 0. Reading guide

| § | Question |
|---|---|
| 1 | What's the auth plane being tested, and how do we verify it? |
| 2 | Pre-test invariant checks (I-0 / I-3 / I-6) |
| 3 | Variant A — native Claude Code TUI (OAuth/subscription) |
| 4 | Variant B — TokenPak Companion (OAuth/subscription) |
| 5 | Run order + workload (§5.1 quick / §5.2 saturated / §5.3 concurrency ceiling) |
| 6 | Observable-behavior capture (no API-key telemetry) |
| 7 | Generating the diff report |
| 8 | Results template (operator handoff) |
| 9 | Failure modes + reproducibility |
| 10 | Out of scope |
| 11 | Why mitmproxy is forbidden in this protocol |

---

## 1. Auth plane under test

This protocol tests **Claude Code OAuth/subscription** parity only. Both variants MUST:

- Authenticate with the **same** `~/.claude/.credentials.json` OAuth token.
- Send the `claude-code-20250219` beta header (and `oauth-2025-04-20` companion beta).
- Hit the user's **same subscription seat** (Claude Pro / Max / Team).
- Use the **same model** (e.g. `claude-sonnet-4-6` or whichever the operator's Claude Code TUI defaults to today).
- Use the **same** `User-Agent: claude-cli/<version>` fingerprint.

If any of these differs, the test is invalid for the I-0 master invariant. The pre-test check in §2 enforces this.

The four other auth planes from Standard #24 §1.5 are **out of scope for this protocol**:

- ❌ Anthropic API key (`x-api-key: sk-ant-…`) — different bucket, different attribution. Use the *secondary* protocol if API-key parity is the question.
- ❌ Cloud-provider (Bedrock / Vertex) — different attribution model entirely.
- ❌ TokenPak proxy API-compatible passthrough — caller credential matters; out of NCP-1R scope.
- ❌ Mixed plane (e.g. native uses OAuth, TokenPak uses API key) — invalid by I-0.

---

## 2. Pre-test invariant checks

Before running either variant, verify all six gates pass. If any gate fails, abort the test — running with a known I-0 / I-3 / I-6 violation produces an invalid result.

### 2.1 I-0 (auth plane is OAuth/subscription on both sides)

```bash
# 1. The OAuth credentials file exists and is recent.
test -f ~/.claude/.credentials.json && \
    echo "✓ OAuth credentials present" || \
    echo "✗ MISSING OAuth credentials — run 'claude auth login'"

# 2. The credentials carry the OAuth path (not just an API key).
python3 -c "
import json
d = json.load(open('${HOME}/.claude/.credentials.json'))
oauth = d.get('claudeAiOauth')
assert oauth and oauth.get('accessToken'), 'no claudeAiOauth.accessToken in credentials'
print('✓ OAuth access token present, account_uuid=', oauth.get('account_uuid', '(unset)'))
"

# 3. TokenPak's claude-code provider is the active route.
tokenpak creds list 2>/dev/null | grep -qi 'tokenpak-claude-code' && \
    echo "✓ TokenPak claude-code provider available" || \
    echo "✗ TokenPak claude-code provider not configured"

# 4. TokenPak is NOT pointed at an API-key bucket for the test.
# (Inspect ~/.tokenpak/config.json or whichever config is active.)
echo "→ Manually verify TokenPak's effective claude-code route uses tokenpak-claude-code, NOT anthropic API-key path."
```

### 2.2 I-3 (session model is independent across CLI invocations)

The native `claude` CLI rotates `X-Claude-Code-Session-Id` per invocation. TokenPak's `ClaudeCodeCredentialProvider` (current implementation) collapses to one UUID per proxy process. **Observe and record both behaviors during the test** — that's the primary H2 evidence.

For the test, **start a fresh TokenPak proxy process** before variant B so any observed session-id is from this run:

```bash
# Kill any existing TokenPak proxy.
pkill -f 'tokenpak serve' || true
sleep 2
```

### 2.3 I-6 (retry layer behavior — observe but don't change)

NCP-1R is measurement-only. Do NOT disable TokenPak's failover engine. Just record what happens — if the proxy retries on 429s while the CLI is also retrying, that's H4 evidence and a candidate for NCP-5.

### 2.4 Workload identity check

The two variants MUST run the **same prompt sequence** against the **same model** in the **same repo**:

- Same `claude` CLI version: capture `claude --version` for both runs and pin them in the results.
- Same model selection: if the operator changes models in mid-run, both variants must change at the same point.
- Same repo: if the operator references files (`/path/to/file.py`), both variants must reference the same paths.
- Same prompt sequence: the operator runs from a script or saved transcript so the prompts are byte-identical.

---

## 3. Variant A — native Claude Code TUI (OAuth/subscription)

### 3.1 Setup

```bash
# Belt-and-suspenders: ensure no proxy redirect leaks into this run.
unset ANTHROPIC_BASE_URL
unset CLAUDE_BASE_URL
unset HTTPS_PROXY
unset HTTP_PROXY

# Verify the CLI fingerprint.
claude --version > ~/ncp1r-claude-version-A.txt
```

### 3.2 What the operator records

For variant A, **TokenPak is not in the path** — there is no telemetry to read. The operator captures **observable client behavior**:

- **Latency** — wall-clock from prompt enter to first response chunk; from prompt enter to completion. Use `time(1)` or screen-record the TUI.
- **Retry messages** — anything the TUI prints about transient failures, backoff, "rate limit hit, waiting Ns". Copy these verbatim.
- **API error messages** — anything the TUI surfaces as a hard failure. Capture the exact text.
- **Success / failure rate** — fraction of prompts that produced a usable response.
- **Session boundary count** — for §5.3 concurrency runs: how many distinct CLI invocations the operator opened. Native CLI opens one per `claude` start; record `N_invocations`.
- **Concurrency ceiling** — the smallest N at which the TUI starts surfacing rate-limit / retry / error messages.

The operator does **not** capture API-key-style telemetry (no mitmproxy, no `ANTHROPIC_LOG=debug` headers). That would change the auth plane (mitmproxy intercepts TLS; some `claude` CLI versions refuse to OAuth through a TLS-intercepting proxy). The test must observe *what the user observes* — TUI text, response times, success rate.

### 3.3 Optional: `claude` CLI built-in observability

If the CLI exposes a non-mitmproxy diagnostic (e.g. `claude --debug` writing to a log), the operator MAY record that — but only if it doesn't alter the OAuth flow. Verify the credential class hasn't changed by re-running the §2.1 check after `--debug` is enabled.

---

## 4. Variant B — TokenPak Claude Code Companion (OAuth/subscription)

### 4.1 Setup

```bash
# Start TokenPak (or verify it's already running with claude-code OAuth route).
tokenpak serve &
sleep 2
curl -s http://127.0.0.1:8765/healthz   # sanity check

# Verify TokenPak's effective Claude Code route.
tokenpak creds list 2>&1 | grep -i claude-code
# Expected: tokenpak-claude-code (OAuth + claude-code-20250219 beta).
# NOT expected: 'anthropic' (API-key path).

# Point Claude Code at TokenPak.
export ANTHROPIC_BASE_URL=http://127.0.0.1:8765
unset HTTPS_PROXY HTTP_PROXY        # don't double-proxy

claude --version > ~/ncp1r-claude-version-B.txt
diff ~/ncp1r-claude-version-A.txt ~/ncp1r-claude-version-B.txt && \
    echo "✓ same CLI version on both sides" || \
    echo "✗ ABORT — CLI version differs"
```

### 4.2 What the operator records

For variant B, TokenPak telemetry captures most of what we need. The same observable-behavior categories from §3.2 also apply (TUI messages still come from the CLI even when TokenPak is in the path), but additionally:

- The capture script (`scripts/capture_parity_baseline.py`) reads `tp_events` + `tp_usage` + `intent_patches` and emits the standard JSON.
- The `provider` field on every `tp_events` row MUST be `tokenpak-claude-code`. Any row with `provider='anthropic'` indicates an I-0 violation and the run is invalid.

### 4.3 Capture

After the run completes:

```bash
scripts/capture_parity_baseline.py \
    --label tokenpak \
    --window-days 1 \
    --output tests/baselines/ncp-1r-parity/tokenpak-$(date -u +%Y%m%dT%H%M%SZ).json

# Verify auth plane in the JSON before submitting.
python3 -c "
import json, sys
d = json.load(open('${OUTPUT_PATH}'))
# All Claude-Code-shaped events should have provider=tokenpak-claude-code.
# (The capture script's _claude_code_filter handles this; we re-verify here
# for I-0 enforcement.)
print('verify provider field in source telemetry: tp_events.provider should be tokenpak-claude-code on every row')
"
```

---

## 5. Run order + workload

### 5.1 Quick run — H1 (cache prefix disruption)

**Goal:** measure cache hit ratio with identical prompt sequences on the same OAuth account.

**Workload:** 10 sequential prompts, single CLI invocation per variant. Total: ~3 minutes.

1. **Variant A first.** Run `claude` direct (no TokenPak). Issue 10 sequential identical prompts. Note start + end time.
2. Wait **15 minutes** for any cache-prefix state to settle (Anthropic cache windows are short).
3. **Variant B.** Start fresh TokenPak proxy, point CLI at it, issue the same 10 prompts.
4. Capture variant B telemetry; hand-fill variant A's observable behavior into the native template (see §6).
5. Run the diff.

The H1 evidence here is mostly observable on the variant B side — `cache_hit_ratio` from `tp_usage.cache_read / (cache_read + cache_write)`. Variant A's contribution is the workload identity (same prompts) plus the observed latency / success rate. If TokenPak's `cache_hit_ratio` is materially lower than what Anthropic typically returns for a stable system block, H1 is supported.

### 5.2 Saturated run — H2 (session-id collapse)

**Goal:** observe whether N concurrent CLIs through TokenPak hit rate-limit / retry behavior earlier than N concurrent native CLIs.

**Workload:**
- Variant A: spawn N parallel `claude` CLI processes, each with its own session-id. Each loops one prompt every 5 seconds for 10 minutes.
- Variant B: spawn N parallel `claude` CLI processes all pointed at the same TokenPak proxy. The proxy collapses to one shared session-id (current behavior).

Recommended N: start with 5; if no disruption fires, double to 10.

For each variant, record:
- `N_invocations` — number of parallel CLIs.
- Wall-clock minute at which the first retry message / API error / rate-limit notice appears in any TUI.
- Fraction of CLIs that completed the full 10-minute loop without disruption.

The H2 evidence is the **disparity in time-to-first-disruption** between the two variants under identical N. If TokenPak's first disruption fires at request count `≈ R/N` where R is variant A's per-CLI disruption point, H2 is supported.

### 5.3 Concurrency ceiling — §4.5 target

**Goal:** establish the §4.5 concurrency parity target value.

For each variant, find the **largest N** that completes the §5.2 workload without disruption. Record:
- Variant A ceiling: `N_native_max`.
- Variant B ceiling: `N_tokenpak_max`.

The §4.5 target is `N_tokenpak_max ≥ 0.8 × N_native_max`. Anything below requires an attribution note (vault overhead? capsule overhead? intent-guidance overhead? retry layer? session-id collapse?).

---

## 6. Observable-behavior capture (variant A)

For variant A, the operator hand-fills the empty native template:

```bash
scripts/capture_parity_baseline.py \
    --label native \
    --window-days 1 \
    --note "OAuth/subscription, claude-cli/$(grep -oP 'claude-cli/\S+' ~/ncp1r-claude-version-A.txt), Claude Pro account, no proxy in path" \
    --output tests/baselines/ncp-1r-parity/native-$(date -u +%Y%m%dT%H%M%SZ).json
```

Then edit the JSON. For NCP-1R OAuth/subscription scope, fill in **only the metrics you actually observed**:

- `metrics.request_count` — count of prompts you issued.
- `metrics.429_count` — number of times the TUI surfaced a rate-limit message.
- `metrics.5xx_count` — number of upstream failures the TUI reported (may be 0 — CLI usually surfaces these as "request failed").
- `metrics.latency_ms.p50` / `.p95` — from your stopwatch / recording.
- `metrics.input_tokens` / `output_tokens` — leave `null` (you can't observe these without API-key telemetry, and capturing those requires a different auth plane). Document in the note: "input/output tokens not observable on OAuth/subscription path".
- `metrics.cache_creation_tokens` / `cache_read_tokens` / `cache_hit_ratio` — leave `null`. **The native variant's cache fields are NOT directly observable on the OAuth/subscription path** without API-key telemetry. This is the NCP-1R limitation: H1 must be settled by inference (does TokenPak's variant-B cache_hit_ratio look reasonable on its own merits?) rather than direct A/B comparison.
- `session.distinct_session_id_count` — the operator records how many `claude` invocations they opened. Each invocation rotates the session-id, so this is the count of independent session-ids the upstream observed.
- `session.session_id_rotations_per_hour` — `distinct_session_id_count / wall_clock_hours`.

For metrics that genuinely cannot be observed on the OAuth/subscription path, leave them `null` and note the reason. The diff script's `inconclusive` verdict path will surface the gap.

---

## 7. Generating the diff report

```bash
scripts/diff_parity_baselines.py \
    --native tests/baselines/ncp-1r-parity/native-<TIMESTAMP>.json \
    --tokenpak tests/baselines/ncp-1r-parity/tokenpak-<TIMESTAMP>.json \
    --output tests/baselines/ncp-1r-parity/results-<TIMESTAMP>.md
```

Expected outcomes for an OAuth/subscription run:

- **H1 cache verdict** is most likely `inconclusive` for NCP-1R, because variant A can't surface cache fields without API-key telemetry. The variant B cache_hit_ratio is still informative as a standalone signal: if it's near zero, H1 is *suggestive* even though the diff verdict is inconclusive.
- **H2 session verdict** is the strongest NCP-1R signal: the diff compares variant A's `distinct_session_id_count` (operator-recorded; one per CLI invocation) vs variant B's (read from `tp_events`; usually one per proxy process). If TokenPak collapses to 1 session over N variant-A invocations, H2 supported with high confidence.

---

## 8. Results template

Send this back to the workstream for NCP-2 / NCP-3 ratification.

```markdown
# NCP-1R results — <YYYY-MM-DD>

**Operator**: <name>
**Auth plane**: Claude Code OAuth/subscription (Claude Pro / Max / Team — specify)
**Account / seat ID**: <account_uuid> (from credentials.json — first 8 chars OK)
**Claude CLI version (both variants identical)**: <claude-cli/...>
**TokenPak version**: <tokenpak --version>
**Workload**: <§5.1 quick / §5.2 saturated / §5.3 ceiling — list which sections ran>
**Wall-clock window**: <ISO-8601 start> → <ISO-8601 end>

## Pre-test invariant checks

- I-0 (auth plane parity): <pass | fail>
  - Variant A credential class: oauth_subscription
  - Variant B credential class: <oauth_subscription | api_key | OTHER>
  - If FAIL: abort, fix routing, rerun.
- I-3 (session-id model): observed
  - Variant A distinct session-ids: <N>
  - Variant B distinct session-ids: <N>
- I-6 (retry layer): observed
  - Variant A retry messages observed: <count>
  - Variant B retry messages observed: <count>
  - TokenPak proxy retry events recorded: <count from tp_events.error_class='retry'>

## Verdicts

- **H1 (cache prefix disruption)**: <supported | not_supported | inconclusive>
  - Reason: <e.g. variant A cache fields unobservable on OAuth path; variant B cache_hit_ratio = X.XX taken as standalone signal>
- **H2 (session-id collapse)**: <supported | not_supported | inconclusive>
  - Variant A distinct sessions: <N>
  - Variant B distinct sessions: <N>
  - Ratio: <N>×

## Concurrency ceiling (§4.5 target)

- Native: N_native_max = <number>
- TokenPak: N_tokenpak_max = <number>
- Ratio: <N_tokenpak_max / N_native_max>×
- §4.5 target met (≥ 0.8×): <yes | no>
- If no, attribution: <which feature accounts for the gap?>

## Dominant cause

<copy-paste from the diff script's "Synthesis" block, OR write your own
when the diff is inconclusive due to OAuth/subscription unobservability>

## Confidence

<high | medium | low>

## Recommended next phase

- <NCP-2 if H1 supported and well-attributed>
- <NCP-3 if H2 supported>
- <Combined NCP-2 + NCP-3 if both>
- <Instrumentation expansion (NCP-1+ instrumentation phase) if H1 inconclusive
   on OAuth path AND we need direct cache evidence>
- <No fix if both unsupported and concurrency ceiling parity is met>

## Out-of-band observations

<free-form: anomalies the operator noticed during the run>

## Attached files

- `tests/baselines/ncp-1r-parity/native-<TIMESTAMP>.json`
- `tests/baselines/ncp-1r-parity/tokenpak-<TIMESTAMP>.json`
- `tests/baselines/ncp-1r-parity/results-<TIMESTAMP>.md`
- `~/ncp1r-claude-version-A.txt` and `-B.txt` (CLI version captures)
```

---

## 9. Failure modes + reproducibility

### 9.1 Common gotchas (NCP-1R-specific)

- **Variant B routes through TokenPak's `anthropic` provider (API key) instead of `tokenpak-claude-code` (OAuth).** Symptom: `tp_events.provider='anthropic'` rows. Fix: re-check `tokenpak creds list`, ensure the claude-code-OAuth route is selected. Re-run the test — the previous data is invalid for I-0.
- **Variant A and variant B Claude CLI versions differ.** Pinning matters because `User-Agent` matters for billing-pool routing. Re-run with the same version.
- **TokenPak proxy was started under a different OS user / `TOKENPAK_HOME`.** TokenPak may pick up a different `~/.claude/.credentials.json`. Verify the OAuth account UUID matches between variants.
- **Variant A used a proxy (`HTTPS_PROXY` was set).** The CLI may refuse OAuth through a TLS-intercepting proxy, OR the test inadvertently became an API-key test. Re-run with `unset HTTPS_PROXY`.
- **Anthropic rolled out a model change between runs.** Pin the model explicitly (e.g. via the CLI's `--model` flag) so both variants hit the same model.

### 9.2 Reproducibility

The protocol is reproducible when:

- The two `*.json` baselines + the diff `*.md` are checked into `tests/baselines/ncp-1r-parity/`.
- The operator's CLI version captures (`*-A.txt`, `*-B.txt`) are attached.
- The pre-test invariant check output is captured (e.g. via `tee`).
- The workload (prompt sequence) is captured in the results.

A second operator running the same workload with the same TokenPak / Claude versions on a similar Claude Pro / Max / Team seat should reach the same verdicts (within threshold noise).

---

## 10. Out of scope for NCP-1R

- ❌ Implementing measurement instrumentation (NCP-1+ instrumentation phase).
- ❌ Implementing fail-safe (NCP-4).
- ❌ Changing companion behavior.
- ❌ Changing proxy behavior (failover, connection pool, retry, credential injection unchanged).
- ❌ Changing session-id behavior (NCP-3 fix).
- ❌ Changing cache placement (NCP-2 fix).
- ❌ Comparing across auth planes (would invalidate I-0).
- ❌ mitmproxy / TLS-intercept-based capture on the OAuth/subscription path. (See §11.)
- ❌ Other interactive clients (Codex, Cursor) — the standard generalizes; this protocol is Claude Code only.

NCP-1R is **strictly observational + operator-run on the same OAuth/subscription account.**

---

## 11. Why mitmproxy is forbidden in this protocol

mitmproxy works by intercepting TLS — installing a CA cert that signs upstream certs on the fly. For an Anthropic API-key call, that's fine: the CLI just sends `x-api-key`, the intercept terminates TLS, decrypts the body, re-encrypts, forwards. The auth plane is unchanged.

For Claude Code OAuth/subscription, **mitmproxy MAY break the OAuth flow** depending on `claude` CLI version: the CLI may detect a TLS intercept and refuse the OAuth handshake, OR it may complete but with subtly different headers (e.g. dropping the `claude-code-20250219` beta because it can't verify the server cert chain). Either way: the variant under test is no longer the same as the user's normal Claude Code behavior.

For the **secondary** API-key protocol (`ncp-1-ab-test-protocol-2026-04-26.md`), mitmproxy is fine — the auth plane is API-key on both sides. Use that protocol if you specifically want to settle API-key parity questions.

For NCP-1R, capture observable client behavior (TUI text, latency, success rate, retry messages, concurrency ceiling) instead. The data is less granular but it answers the right question.

---

## 12. Cross-references

- `docs/internal/standards-proposals/24-native-client-concurrency-parity-standard.md` — the standard (NCP-1R revision); §1.5 auth-plane definitions; I-0 master invariant; §4.5 concurrency parity target
- `docs/internal/specs/native-client-concurrency-parity-diagnostic-2026-04-26.md` — the diagnostic plan + hypothesis matrix
- `docs/internal/specs/ncp-1-ab-test-protocol-2026-04-26.md` — the secondary (harness validation) protocol
- `scripts/capture_parity_baseline.py` — capture script (works for both protocols; auth-plane-aware filtering for NCP-1R is a future NCP-1+ instrumentation deliverable)
- `scripts/diff_parity_baselines.py` — diff script
- `tokenpak/services/routing_service/credential_injector.py::ClaudeCodeCredentialProvider` — the I-0 / I-3 surface (auth-plane preservation + session-id rotation)
- `tokenpak/proxy/failover_engine.py` — the I-6 surface (retry layer)

---

## 13. Acceptance criteria

NCP-1R is **complete** when:

- [x] The auth-plane scoping correction is reflected in the standard (§1.5 + I-0 + I-3 + I-6 + §4.5).
- [x] This primary protocol exists.
- [x] The 2026-04-26 NCP-1 protocol is marked secondary with a forwarding banner.
- [x] The pre-test invariant checks (§2) are concrete and runnable.
- [x] The observable-behavior capture (§3.2 + §6) is documented.
- [x] The results template (§8) handles the OAuth/subscription unobservability cleanly.
- [x] No runtime / classifier / routing / retry / cache / session-id behavior changes.
- [ ] CI green on the closeout PR.

After NCP-1R lands, the next step is the operator (NCP-1A) running §5.1 + §5.2 + §5.3 on a real Claude Code OAuth/subscription account and submitting the §8 results.
