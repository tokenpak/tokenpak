# TokenPak REST API — `/tpk/v1/*`

The proxy exposes application-level REST endpoints under `/tpk/v1/*`, distinct
from the `/v1/*` LLM passthrough that Anthropic/OpenAI clients hit. These are
the endpoints the companion MCP server calls — and that external tooling
(dashboards, language-agnostic clients, CI scripts) can call directly.

## Auth

- **Localhost-only by default.** Requests from anything other than
 `127.0.0.1` / `::1` / `localhost` are rejected with `401`.
- **Optional key auth.** If `TOKENPAK_PROXY_KEY` is set in the proxy's
 environment, all `/tpk/v1/*` requests must include the header
 `X-TPK-Key: <same-value>`.
- **Guard snapshots require this key.** The session guard snapshot route
 rejects access when the key is unconfigured, including loopback requests.
- No CORS today; this is a dev-host API. For remote use, put a reverse proxy
 in front of it that handles TLS + auth.

## Error shape

4xx / 5xx responses use an error code and may include a detail:

```json
{"error": "<machine-readable-code>", "detail": "<human message>"}
```

Status codes follow HTTP semantics: `400` malformed, `401` unauthorized,
`404` not found, `500` internal, `503` index not loaded / dependency missing.

## Endpoint reference

### `POST /tpk/v1/sessions/guard-snapshot`

Observe the serving proxy's rolling guard accounting for an explicit session.
Send `Content-Type: application/json`, the configured `X-TPK-Key`, and exactly
`{"session_id":"your-session-id"}`. The body is limited to 4096 bytes; session
identifiers are limited to 512 characters and retain their exact identity.
Recognized session headers must agree with the body. Query parameters, extra
fields, duplicate JSON keys, caller clocks and database paths are rejected.
Browser `Origin` requests are refused. Responses use `Cache-Control: no-store`.

The `native-guard-snapshot/1` response contains the explicit `session_id`, an
opaque `owner_instance_id`, `scope: serving_proxy_process`, `observed_at` and
`produced_at` timestamps, the effective `rolling_policy` and a fingerprint of
the full effective policy. `recorded_usage` and `pending_projected_usage` each
contain per-agent and overall cost, input-plus-output tokens, and cache-read tokens.
Recorded costs retain their ledger estimate semantics. Pending costs are
existing admission estimates. These components can overlap during settlement;
`components_may_overlap` is always true. Do not add them as exact billed usage.

`session_observed` means the ledger contains a row for the supplied identifier.
`agent_attribution_available` requires durable attribution to agree with the
serving process's current mapping. Unavailable agent values are `null`, as is
an unreadable or invalid recorded component. Expired reservations are reported
in `expired_pending_count`; observing them does not remove them.

Every version 1 response has `guard_evidence_eligible: false` and explicit
`reason_codes`: accounting cannot prove coverage across all request paths,
other proxy processes or ledger ownership. Missing data adds further reasons.
This endpoint cannot authorize spending, create a production recommendation,
or replace fresh guard validation at confirmation. It creates no application
record and makes no provider request. SQL opens read-only with fresh WAL
visibility; SQLite may coordinate readers through WAL sidecars. There is no
claim of zero filesystem effects from that coordination.

Errors include `400 invalid_snapshot_input`, `401 unauthorized`,
`403 browser_origin_forbidden`, `413 snapshot_body_size`, and
`503 snapshot_auth_unconfigured`, `snapshot_owner_unavailable` or
`snapshot_resolution_unavailable`. No config path, credential, prompt, other
session identifier or raw exception is returned.

With `spend_guard.reservations_enabled: true`, the response uses
`native-guard-snapshot/2` and `scope: configured_monitor_domain`. This opt-in
is off by default. Requests reserve projected cost and tokens in the existing
guard database before provider send, including requests without an agent tag.
One monitor ledger binds to one reservation store, so separate proxy processes
using that ledger serialize against the same budget. Replacing the monitor
database changes its accounting identity; a conflicting store binding refuses.

Version 2 adds an opaque `ledger_scope_sha256`, `accounting_generation` and
`agent_caps_applicable`. It correlates committed monitor rows with their holds,
so `components_may_overlap` is false. The values retain the guard's token and
rate-estimate semantics; they are not provider billing statements. Unknown
agent dimensions are null while overall caps still apply.

`guard_evidence_eligible` can be true after a fully measured, attributed request
has finished and its monitor row has committed. In-progress requests, unknown
usage, retries with unmeasured attempts, unsupported forwarding, alternate
backends and opaque tunnels prevent complete evidence. Missing cache counts
are not assumed to be zero. Expired unresolved reservations make accounting
unavailable; expiry does not prove that no spend occurred. The configured
domain does not measure traffic sent outside these proxy instances. A true
flag supplies guard accounting evidence only; economic recommendations and
confirmation still need their own complete, fresh inputs.

Completed reservation and request metadata is retained for 30 days by default,
or the rolling window if longer. Cleanup happens during writes. Each table has
a default limit of 100,000 records; exhaustion refuses new admission without
discarding unresolved history. Configure `reservation_history_seconds` and
`reservation_max_records` in the `spend_guard` block to change these limits.
`reservation_ttl_seconds` defaults to 600; its environment override is
`TOKENPAK_SPEND_GUARD_RESERVATION_TTL`. The feature switch also accepts
`TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED`. Observing a snapshot does not
provision, prune or change permissions on these records.

### `GET /tpk/v1/health`

Returns proxy version + uptime + vault status.

```json
{
  "version": "1.26.0",
 "uptime_s": 336.5,
 "vault": { "available": true, "blocks": 13443, "ready": true }
}
```

### `GET /tpk/v1/vault/search?q=<query>&limit=<N>`

BM25 search over the indexed vault. `limit` defaults to 5, capped at 20.

```json
{
 "query": "credential refresh",
 "count": 2,
 "results": [
 { "block_id": "...", "path": "...", "score": 14.437,
 "tokens": 312, "preview": "..." }
 ]
}
```

### `GET /tpk/v1/vault/block/{block_id}`

Full content + metadata of a specific block.

```json
{
 "block_id": "docs.guides.example.md",
 "path": "docs.guides.example.md",
 "tokens": 312,
 "content": "---\ntitle: ...\n..."
}
```

### `GET /tpk/v1/budget`

Session + daily cost snapshot. `remaining_usd` is `null` when no budget is
set (`TOKENPAK_COMPANION_BUDGET` not configured).

```json
{
 "session_cost_usd": 0.0,
 "daily_cost_usd": 0.0,
 "daily_budget_usd": 0.0,
 "remaining_usd": null,
 "session_requests": 0,
 "budget_set": false
}
```

### `GET /tpk/v1/journal/sessions?limit=<N>`

Recent companion sessions with basic counts.

```json
{
 "sessions": [
 { "session_id": "...", "project_dir": "...",
 "total_requests": 12, "total_cost_usd": 0.34, "entry_count": 5 }
 ]
}
```

### `GET /tpk/v1/journal/{session_id}?entry_type=<type>&limit=<N>`

Entries for a specific session. `entry_type` optionally filters to e.g.
`auto`, `user`, `companion_savings`.

```json
{
 "session_id": "abc-123",
 "entries": [
 { "timestamp": 1776462567.9, "type": "user", "content": "..." }
 ]
}
```

### `POST /tpk/v1/journal/{session_id}/entry`

Add a journal entry. Body:

```json
{ "content": "<required text>", "entry_type": "user" }
```

Returns: `{"status":"ok", "session_id":"...", "entry_type":"user"}`.

### `GET /tpk/v1/capsules?limit=<N>` / `GET /tpk/v1/capsules/{session_id}`

List available memory capsules / fetch a specific capsule's content. When
fetching a specific capsule, passing `?caller_session_id=<sid>` (or the
`X-TPK-Session` header) attributes a `load_capsule` savings event to the
caller's journal.

### `POST /tpk/v1/compress`

Head/tail truncate to fit `max_tokens`. Body:

```json
{
 "text": "<required>",
 "max_tokens": 2000,
 "session_id": "optional-caller-session-for-savings-attribution"
}
```

Returns `pruned_text`, `original_tokens`, `pruned_tokens`, `tokens_avoided`,
`cost_avoided_usd`, `reduction_pct`.

### `POST /tpk/v1/optimize`

Offline prompt linter. Body:

```json
{ "text": "<required>", "source": "<optional label for report>" }
```

Returns an `OptimizationReport` with `findings[]` (whitespace, repeated
phrases, verbose phrasings) and estimated savings.

### `POST /tpk/v1/tokens/estimate`

Token count for inline text or a readable file on the proxy host. Body:

```json
{ "text": "..." }
{ "file_path": "/abs/path/to/file.md" }
```

Returns `{"chars": N, "tokens": M, "chars_per_token": R}`.

### `GET /tpk/v1/session/info`

Proxy-side snapshot of mode, profile, cache TTL, in-flight session
counters, and vault availability. Used by the companion's `session_info`
MCP tool to merge with local companion state.

```json
{
  "version": "1.26.0",
 "uptime_s": 71.4,
 "mode": "hybrid",
 "profile": "balanced",
 "cache_ttl": "1h",
 "session": { "requests": 2, "input_tokens": 136417,
 "output_tokens": 475, "cost_usd": 1.51,
 "cost_saved_usd": 0.0, "errors": 0 },
 "vault": { "available": true, "blocks": 13443 }
}
```

## Design notes

- Every endpoint is **fail-soft**: backend-missing conditions return `503`
 with a `detail` string, never a 500 / stack trace.
- **Strict JSON**: responses use `allow_nan=False`; unbounded floats (e.g.
 infinite `remaining_usd` when no budget is set) serialize as `null`.
- State locations: vault index from `proxy/vault_bridge.py` singleton,
 budget + journal at `~/.tokenpak/companion/{budget,journal}.db`,
 capsules at `~/.tokenpak/companion/capsules/*.md`.

## Using from a non-Python client

The companion MCP tools are the reference consumer — see
`tokenpak/companion/mcp/tools.py` for the wire pattern. Essentially every
tool is: build params → `urlopen` with `X-TPK-Key` if set → parse JSON →
return to the MCP caller.

A Node or Rust companion can implement the MCP server in the host language
and call these endpoints over HTTP without needing to `pip install tokenpak`.
