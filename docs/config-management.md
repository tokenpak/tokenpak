# TokenPak Config Management for Teams

When multiple agents (Trix, Cali, Sue, etc.) share a TokenPak deployment, config consistency matters. This doc covers how to manage, sync, and validate config across agents.

## Config Location

The primary config lives at:
```
~/.openclaw/openclaw.json
```

A lock file at `~/vault/System/tokenpak.lock.json` pins the expected versions.

## Config Version Fields

Every config carries version metadata in `meta`:

```json
{
  "meta": {
    "configVersion": "2026-03-07-v1",
    "tokenpakVersion": "0.4.0",
    "lastUpdated": "2026-03-07T20:44:46Z",
    "configHash": "sha256:3a1ef11ffb19"
  }
}
```

## Validating Config

```bash
tokenpak config validate
```

Checks:
- All `meta` fields present
- `configHash` matches computed hash of current config
- Hash consistent with lock file

## Syncing Config

### From Git (Vault)

```bash
tokenpak config sync           # pull from vault git
tokenpak config sync --dry-run # preview diff only
```

This runs `vault-sync.sh` to pull latest, then checks for drift.

### From URL

```bash
tokenpak config pull --source=url --url=https://tokenpak.io/config/v1
```

Merge strategy: local additions preserved, remote wins on conflicts.

### Merge Strategies

| Strategy | Behavior |
|----------|----------|
| `merge` (default) | Local additions kept, remote wins conflicts |
| `replace` | Remote config replaces local entirely |
| `diff` | Show diff without applying |

```bash
tokenpak config pull --source=url --url=... --merge=replace
```

## Drift Detection

Run `tokenpak version` to see if any agent is drifted:

```
TokenPak CLI     : 1.0.0-rc1
Proxy (running)  : 0.4.0  uptime=2h15m  python=3.12.3
Config version   : 2026-03-07-v1
Config hash      : sha256:3a1ef11ffb19

Lock file        : ~/vault/System/tokenpak.lock.json
  Locked version : 0.4.0
  Locked hash    : sha256:3a1ef11ffb19
  ✓ Config matches lock file
```

If drift is detected:
```
⚠️  Config drift detected!
  Lock hash    : sha256:3a1ef11ffb19
  Current hash : sha256:ff2ab0912c01
  Run `tokenpak config sync` to reconcile.
```

## Best Practices

1. **Lock after config changes** — after any config edit, run `tokenpak config validate` and commit `tokenpak.lock.json` to vault
2. **Sync before work sessions** — agents should call `tokenpak config sync` at the start of shifts
3. **Version-bump on changes** — update `meta.configVersion` whenever `openclaw.json` changes (bump the counter: `v1` → `v2`)
4. **Never edit `meta` manually** — use CLI commands to manage version fields

## Agent Startup Check

Agents can check version consistency on startup by reading the lock file and comparing to running proxy:

```python
import json, urllib.request
lock = json.loads(open("~/vault/System/tokenpak.lock.json").read())
proxy = json.loads(urllib.request.urlopen("http://localhost:8766/version").read())
if lock["proxyVersion"] != proxy["version"]:
    print(f"⚠️ Version drift: lock={lock['proxyVersion']}, proxy={proxy['version']}")
```

This is implemented in the agent startup validation (see `docs/versioning.md`).
