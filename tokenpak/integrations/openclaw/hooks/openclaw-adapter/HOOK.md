---
name: openclaw-adapter
version: 1.0
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

Path C session-binding hook. OpenClaw v2026.3.23-2 has no outbound-request mutation surface (per the 2026-04-28 PHASE-A-MEMO investigation), so this hook uses a message-event + filesystem rendezvous pattern instead.

## What it does

On every `message:received` and `message:sent` event, this hook reads `event.sessionKey` (OpenClaw's per-conversation key) and writes a JSON record to `~/.openclaw/sessions/active.json`. The tokenpak proxy reads that file when traffic arrives with `User-Agent: openclaw*` and uses the UUID as `journal.db.session_id` for cross-platform attribution.

## active.json schema

```json
{
  "schema_version": "1.0",
  "session_uuid": "9f449869-e72a-4d19-9f0b-1a46305a62dd",
  "last_event": "message:received",
  "last_event_ts": 1714312345,
  "event_count": 42,
  "agent": "trixbot"
}
```

- `session_uuid` — UUID v4 from `event.sessionKey`. Refuses to write if not UUID-shaped.
- `last_event_ts` — unix seconds at write time. Proxy uses this for stale-TTL check.
- `event_count` — monotonic counter; preserved across writes (read-prior-incr-write).
- `agent` — from `OPENCLAW_AGENT_NAME` env or `hostname()`.

## Hook event subscriptions

- `message:received` — PRIMARY. Fires when a user→agent message arrives, before LLM dispatch.
- `message:sent` — keeps `last_event_ts` fresh during long conversations.

## Safeguards

1. Atomic write — `<file>.tmp.<pid>.<ts>` → `renameSync`.
2. Schema validation — refuses to write if `session_uuid` doesn't match UUID regex.
3. File permissions 0600 — set immediately after rename.
4. Stale TTL — handler does NOT enforce; proxy reader does (`now - last_event_ts > 300s` → fall back to anonymous).
5. Never throws — try/catch every entry point with `console.warn`.
6. Tmp file cleanup — strays older than 60s removed at write time.

## Multi-session race-condition note

If two conversations on the same agent host fire concurrently, the second write wins. Acceptable for the typical single-active-Telegram-conversation pattern. Sharded multi-session (one active.json per session) is future work.

## Failure mode

- Missing `event.sessionKey` → no-op (does not inject, does not throw)
- Malformed UUID → no-op (refused by validation)
- Disk full / permission denied → console.warn, hook returns cleanly
- Rename failure → tmp file retained, target unchanged

## Compatibility

- OpenClaw runtime ≥ v2026.3.23-2 (uses `event.sessionKey` from `HookEvent` ctx; verified via PHASE-A-MEMO.md investigation)
- Coexists with `tokenpak-telemetry` (separate event handlers; both subscribe to `message:received` independently)

## Source of truth

- Repo: `tokenpak/integrations/openclaw/hooks/openclaw-adapter/`
- Initiative: `2026-04-28-openclaw-adapter-session-binding` (Path C)
- Phase A finding: `PHASE-A-MEMO.md`
- Spec: `03-SPEC.md §Component 1`
- Proxy-side reader: OAS-11 (`platform_bridge.py _openclaw_extract`)

## Revert

```bash
openclaw hooks disable openclaw-adapter
systemctl --user restart openclaw-gateway
```
