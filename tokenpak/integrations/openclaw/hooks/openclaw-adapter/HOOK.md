---
name: openclaw-adapter
description: "Bind OpenClaw session UUIDs to tokenpak proxy journal.db via filesystem rendezvous (active.json) — Path C design"
homepage: https://docs.openclaw.ai/automation/hooks
metadata:
  {
    "openclaw": {
      "emoji": "🔗",
      "events": ["message:received", "message:sent"],
      "requires": { "bins": ["node"] }
    }
  }
---

# openclaw-adapter

Path C session-binding hook. OpenClaw v2026.3.23-2 has no outbound-request
mutation surface (per `PHASE-A-MEMO.md`, 2026-04-28), so this hook uses a
message-event + filesystem rendezvous pattern instead of header injection.

## What it does

On every `message:received` and `message:sent` event, the hook reads
`event.sessionKey` (OpenClaw's per-conversation key) and writes a JSON
record to `~/.openclaw/sessions/active.json`. The tokenpak proxy reads
that file when traffic arrives with `User-Agent: openclaw*` and uses
the UUID as `journal.db.session_id` for cross-platform attribution.

## active.json schema (v1.0)

```json
{
  "schema_version": "1.0",
  "session_uuid": "9f449869-e72a-4d19-9f0b-1a46305a62dd",
  "last_event": "message:received",
  "last_event_ts": 1714312345,
  "event_count": 42,
  "agent": "agent-1"
}
```

- `schema_version` — string, currently `"1.0"`. Bumped on breaking changes.
- `session_uuid` — UUID v4 from `event.sessionKey`. Refused if not UUID-shaped.
- `last_event` — `"message:received"` or `"message:sent"` (matches the firing event).
- `last_event_ts` — unix seconds at write time. Proxy uses this for stale-TTL check.
- `event_count` — monotonic counter; preserved across writes via prior-file read.
- `agent` — from `OPENCLAW_AGENT_NAME` env or `os.hostname()`.

## Hook event subscriptions

- `message:received` — PRIMARY. Fires when a user→agent message arrives, before LLM dispatch.
- `message:sent` — keeps `last_event_ts` fresh during long conversations.

Both events are filtered server-side via `metadata.openclaw.events`; other
event types short-circuit the handler immediately.

## Safeguards

1. **Atomic write** — write to `<file>.tmp.<pid>.<ts>`, then `renameSync` to target.
2. **Schema validation** — refuses to write if `session_uuid` does not match the UUID regex.
3. **File permissions 0600** — set immediately after rename via `chmodSync`.
4. **Stale TTL** — handler does NOT enforce; proxy reader does (`now - last_event_ts > 300s` → fall back to anonymous attribution).
5. **Never throws** — every entry point wrapped in try/catch with `console.warn` on failure.
6. **Tmp file cleanup** — strays older than 60s removed at write time (`cleanupStaleTmp`).

## Multi-session race-condition note

If two conversations on the same agent host fire concurrently, the second
write wins. Acceptable for the typical single-active-Telegram-conversation
pattern fleet hosts use today. Sharded multi-session (one file per session
key) is future work and would require a parallel proxy-side reader update.

## Failure mode

- Missing `event.sessionKey` → no-op, no warning.
- Malformed UUID → no-op (refused by validation regex).
- Disk full / permission denied → `console.warn`, hook returns cleanly.
- Rename failure → tmp file retained for next cleanup; target unchanged.

The host gateway is never affected by adapter failures; absence of
`active.json` simply means the proxy falls back to anonymous attribution.

## Compatibility

- OpenClaw runtime ≥ v2026.3.23-2 (uses `event.sessionKey` from `HookEvent` ctx; verified via `PHASE-A-MEMO.md` investigation, 2026-04-28).
- Coexists with `tokenpak-telemetry` (separate hook, separate storage).
- Node ≥ 18 (built-in `node:fs`, `node:path`, `node:os` only).

## Source of truth

- Repo: `tokenpak/integrations/openclaw/hooks/openclaw-adapter/`
- Handler: `handler.js` (OAS-02) — schema authority for `active.json` payload shape
- Initiative: `2026-04-28-openclaw-adapter-session-binding` (Path C)
- Phase A finding: `PHASE-A-MEMO.md`
- Spec: `03-SPEC.md §Component 2`
- Proxy-side reader: OAS-11 (`platform_bridge.py _openclaw_extract`)

## Revert

```bash
openclaw hooks disable openclaw-adapter
systemctl --user restart openclaw-gateway
```
