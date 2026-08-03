# OpenClaw Integration

This directory contains TokenPak's local OpenClaw integration scripts and hook
bundle. The integration is additive: it installs a session-binding hook for
OpenClaw traffic and leaves existing provider/runtime configuration intact.

## Inventory

| Path | Purpose |
|---|---|
| `tokenpak-inject.sh` | Installs or upgrades the OpenClaw hook bundle and patches `~/.openclaw/openclaw.json` with an enabled `openclaw-adapter` entry. |
| `tokenpak-uninstall.sh` | Removes the `openclaw-adapter` hook bundle and its `openclaw.json` entry. |
| `hooks/openclaw-adapter/handler.js` | Writes the active OpenClaw session to `~/.openclaw/sessions/active.json` on message events. |
| `hooks/openclaw-adapter/HOOK.md` | Documents the hook events, output schema, safeguards, compatibility, and rollback path. |
| `hooks/openclaw-adapter/tests/test-active-json.js` | Node smoke test for the hook's `active.json` writer behavior. |

## openclaw-adapter

`openclaw-adapter` is the OpenClaw-side counterpart to TokenPak's proxy-side
OpenClaw session reader. OpenClaw does not provide an outbound request mutation
hook for this integration shape, so the hook uses a filesystem rendezvous:
OpenClaw message events update `active.json`, and the TokenPak proxy reads that
file when it receives `User-Agent: openclaw*` traffic.

The expected result is that OpenClaw-originated traffic can be attributed to a
fresh session UUID. If the file is missing, stale, malformed, or unreadable, the
proxy falls back to anonymous OpenClaw attribution rather than failing the
request path.

## Verification

Run the hook smoke test from the repository root:

```bash
node tokenpak/integrations/openclaw/hooks/openclaw-adapter/tests/test-active-json.js
```

For proxy-side behavior, run:

```bash
python -m pytest tests/services/routing_service/test_openclaw_extract_path_c.py -q
```
