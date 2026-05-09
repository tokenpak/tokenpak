# TIP Spend Guard — User Guide

The **TIP Spend Guard** is TokenPak's proxy-side circuit breaker. It blocks risky requests *before* they reach the upstream LLM provider — preventing runaway tool loops, accidental large prompts, and the death-by-1000-cuts pattern that killed two real-world sessions in early May 2026 ($101 and $99 in 88 minutes each, on what looked like normal coding traffic).

This is the canonical defense; the older companion-side **advisory budget** is fail-open and stays as a soft hint.

> **Status:** Available since TokenPak v1.5.1.
> **Authoritative contract:** [Standard 29 — TIP Spend Guard Agent Contract](https://github.com/tokenpak/docs/blob/main/standards/29-spend-guard-agent-contract.md)

---

## How it works (in 30 seconds)

When a client sends a request to the TokenPak proxy:

1. The proxy reads the request body.
2. Before forwarding upstream, the **estimator** projects total tokens and cost (using `tokenpak.models.get_rates(model)` as the single source of truth for pricing).
3. The **policy engine** compares the projection against four thresholds (warn / block / hard-block) and, separately, against your **session-cumulative running cost** for the last hour.
4. If the request is in the block band, the proxy returns an HTTP **402 Payment Required** with a structured JSON body. **Zero outbound bytes hit the provider.**
5. You release the held request by replying `yes` (or `no` to cancel), or by re-sending the request with a leading `[TIP: allow=once max=$X]` directive.
6. Hard-block exceeds an immutable ceiling (default 1M tokens / $50) and **cannot** be bypassed.

---

## Defaults

```yaml
# ~/.tokenpak/config.yaml
spend_guard:
 enabled: true # global on/off
 warn_tokens: 100000 # advisory only — no UX surface yet
 warn_cost_usd: 2.0
 block_tokens: 500000 # holds the request, prompts user
 block_cost_usd: 10.0
 hard_block_tokens: 1000000 # immutable ceiling
 hard_block_cost_usd: 50.0
 session_block_cost_usd: 10.0 # death-by-1000-cuts defense
 session_window_seconds: 3600 # 1h sliding window
 pending_ttl_seconds: 600 # held requests expire after 10 min
 audit_db_path: ~/.tokenpak/spend_guard.db
```

Every key is overrideable via `TOKENPAK_SPEND_GUARD_*` environment variables (e.g. `TOKENPAK_SPEND_GUARD_BLOCK_COST_USD=5.0`).

To disable entirely: `TOKENPAK_SPEND_GUARD_ENABLED=0` or `spend_guard.enabled: false`.

---

## What you'll see when the guard fires

### Block (recoverable)

```json
{
 "error": {
 "type": "tokenpak_spend_guard_blocked",
 "message": "TIP Spend Guard blocked this request before provider send. Reply 'yes' to proceed, 'no' to cancel, or prepend '[TIP: allow=once]' to bypass.",
 "reason": "session_cumulative_cost_exceeded",
 "threshold_hit": "session_block_cost_usd>=10.0 running=9.85",
 "projected_input_tokens": 78000,
 "projected_output_tokens": 4000,
 "projected_cost_usd": 0.62,
 "model": "claude-opus-4-7",
 "pending_id": "tpg_a1b2c3d4...",
 "expires_at": 1715126400.0,
 "approval_prompt": "Proceed? Yes / No",
 "retryable": true,
 "recovery_status": "user_action_required"
 }
}
```

HTTP status: **402 Payment Required**. This is **not** a model failure — your client must surface the prompt to the operator (or pre-declare via `[TIP: ...]` if running headless) and **must not auto-retry** the same request body. The proxy enforces anti-loop dedup on the request hash for 30 seconds.

### Hard-block (terminal)

Same shape but `error.type=tokenpak_spend_guard_hard_blocked`, `retryable: false`, `recovery_status: terminally_blocked`. Even `[TIP: bypass=on]` cannot release this — the hard-block ceiling is immutable.

---

## Releasing a held request

### Interactive (Yes / No)

After receiving a block response, send a request whose last user-message content is **exactly** one of:

- **Yes:** `yes`, `y`, `ok`, `okay`, `proceed`, `continue`, `approve`, `sure`, `go`, `go ahead`, `do it`, `run it`, `ship it`, `confirm`, `yep`, `yeah`, `let's go`, …
- **No:** `no`, `n`, `nope`, `stop`, `cancel`, `abort`, `deny`, `don't`, `nevermind`, `skip`, …

Match is **strict whole-string** (case-insensitive, trims trailing punctuation). `"I'll go ahead and write..."` is **AMBIGUOUS** — it will not auto-approve. Ambiguous replies trigger a re-prompt; the held request stays.

On Yes, the proxy replays the **original** held request byte-identically (with the original headers, including auth) — not the word `yes` itself.

### Programmatic (`[TIP: ...]` directive)

Prefix any provider-bound prompt with `[TIP: ...]` at the very front of the first user-text segment (after optional whitespace). The directive is parsed, applied, and **stripped** before forwarding — the model never sees it.

```text
[TIP: allow=once max=$15 reason="planned deep refactor"]
<your actual prompt>
```

| Directive | Values | Effect |
|---|---|---|
| `allow` | `once` / `15m` / `session` | Authorize replay of any held pending request. |
| `bypass` | `on` (default if bare) / `off` | Skip Yes/No prompt; still subject to hard-block. |
| `max` | `$N` (cost USD) / `Nk_tokens` / `Nm_tokens` | Per-request ceiling the directive authorizes. Unspecified dimensions are treated as user-authorized. |
| `estimate` | `on` (default if bare) | Return RiskEstimate JSON, no provider call. |
| `cancel` | `on` (default if bare) | Discard any pending request for this session. |
| `reason` | `"free text"` | Annotation written to the audit log. |

Mid-sentence `[TIP: ...]` is **not** a directive — it's content the model sees verbatim. Only the leading position is parsed.

Unknown directives are gracefully ignored (warning row in the audit log) — a future Pro version may add directives that OSS clients should pass through harmlessly.

### Examples

```bash
# Estimate without sending
[TIP: estimate=on]
Refactor the auth flow.

# One-time bypass with cost ceiling
[TIP: allow=once max=$15 reason="planned auth refactor"]
Refactor the auth flow.

# Cancel any held request
[TIP: cancel]

# Token-based ceiling
[TIP: allow=once max=500k_tokens]
<long prompt>
```

---

## For headless cycles / agents

Background agents (cron jobs, scheduled cycles, automated pipelines) have no human at the prompt. They MUST follow Standard 29 §6:

1. **Pre-declare for known-large cycles.** Prepend the first prompt of any cycle expected to exceed $5 with:
 ```text
 [TIP: allow=once max=$8 reason="<cycle name>"]
 ```
2. **Or set a tighter session ceiling per-cycle:**
 ```bash
 TOKENPAK_SPEND_GUARD_SESSION_BLOCK_COST_USD=20 \
 ANTHROPIC_BASE_URL=http://127.0.0.1:8766 \
 claude -p "..."
 ```
3. **Tolerate clean-exit on block.** Receive the structured 402, log it, terminate. **Never retry-loop** — the proxy enforces 30s anti-loop dedup, but a well-behaved agent shouldn't need that protection.

A reference cron-prompt example lives at `~/vault/06_RUNTIME/cron-prompts/spend-guard-pre-declaration-example.md`.

---

## Monitoring + audit

Every guard decision writes one row to `~/.tokenpak/spend_guard.db`:

```sql
-- Last 50 decisions for any session
SELECT
 datetime(ts, 'unixepoch', 'localtime') AS local_time,
 session_id, event_type, decision,
 projected_cost_usd, projected_tokens,
 pending_id, tip_directive_json
FROM spend_guard_audit
ORDER BY ts DESC LIMIT 50;

-- Decision counts by event_type, last 24h
SELECT event_type, COUNT(*) AS n
FROM spend_guard_audit
WHERE ts > strftime('%s','now') - 86400
GROUP BY event_type
ORDER BY n DESC;

-- Sessions that hit the block band (with running cost when blocked)
SELECT session_id, COUNT(*) AS blocks,
 MAX(projected_cost_usd) AS max_projected
FROM spend_guard_audit
WHERE event_type = 'block'
GROUP BY session_id
ORDER BY blocks DESC;
```

Event types: `block`, `hard_block`, `warn`, `allow`, `tip_bypass`, `approve_yes`, `cancel_no`, `cancel`, `replay`, `reprompt`, `estimate`, `expire`, `anti_loop_hit`, `pending_waiting`, `replay_race`.

The audit writer is best-effort and non-blocking — guard decisions never wait on the audit row, and IO errors are swallowed at DEBUG level.

---

## Threshold tuning

The 2026-05-07 v1.5.1 ship-defaults are intentionally conservative. The 7-day soak gate uses the standard threshold defaults:

- **Day 1–2:** monitor `spend_guard_audit` for `block` rows. If false-positive rate > 5%, raise `session_block_cost_usd` to 15.0.
- **Day 3–7:** review `tip_bypass` rows for repeating patterns that justify a permanent ceiling raise; or recommend per-cycle declared ceilings.
- **Day 8+:** thresholds locked unless a new Standard 29 review is invoked.

To override locally without waiting for the next default-tuning rollout: set the `TOKENPAK_SPEND_GUARD_*` env vars in your shell, or the `spend_guard:` block in `~/.tokenpak/config.yaml`.

---

## Troubleshooting

**"My request was hard-blocked but I really need to send it."**
The hard-block ceiling (default 1M tokens / $50) is immutable on purpose — this is the failsafe against catastrophic accidents. If you legitimately need to send something this large, raise `hard_block_tokens` / `hard_block_cost_usd` in `~/.tokenpak/config.yaml`. There's no run-time bypass.

**"I get re-prompts even when I reply 'yes'."**
Your reply isn't matching the strict-whole-string vocab. Check `~/tokenpak/tokenpak/proxy/spend_guard/intent.py:_POSITIVE` for the exact accepted set. Anything with extra words around the keyword (e.g. `"yes please"`) is AMBIGUOUS by design.

**"The guard isn't firing — I see big requests going through."**
Check `tokenpak status` and `sqlite3 ~/.tokenpak/spend_guard.db "SELECT COUNT(*) FROM spend_guard_audit"`. If zero rows: confirm `spend_guard.enabled=true` (`TOKENPAK_SPEND_GUARD_ENABLED=0` in env disables it), and that you restarted the proxy after upgrading. The proxy holds modules in memory — only restarts pick up code changes.

**"How do I know what session_id the proxy assigned me?"**
The proxy resolves session via the `X-Claude-Code-Session-Id` header (Claude Code), `X-TokenPak-Session` (OpenClaw cycles), or falls back to the model name. To force a specific session id: send `X-TokenPak-Session: my-explicit-id` on every request.

**"My request is blocked because of session-cumulative — but my session has been quiet."**
The session window reads from `~/.tokenpak/monitor.db`, which is the proxy's wire-side cost log (every completed request). If you ran an expensive cycle in the last hour under the same session id, that counts toward the cumulative. Either wait out the window, set a different session id, or temporarily raise `session_block_cost_usd`.

---

## See also

- **Standard 29:** [`29-spend-guard-agent-contract.md`](https://github.com/tokenpak/docs/blob/main/standards/29-spend-guard-agent-contract.md) — wire contract.
- **Reference implementation:** `tokenpak/proxy/spend_guard/`
- **Tests:** `tokenpak/tests/test_spend_guard_*.py` (149 tests including the canonical 2026-05-07 spike-replay).
- **Initiative record:** `~/vault/01_PROJECTS/tokenpak/initiatives/2026-05-07-tip-spend-guard-oss/_index.md`
