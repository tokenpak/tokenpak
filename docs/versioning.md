# TokenPak Version Control Strategy

> Version control ensures all agents and users run compatible TokenPak components. This document covers what gets versioned, how drift is detected, and how updates propagate.

## Version Taxonomy

TokenPak tracks three version streams:

| Stream | Field | Example | Notes |
|--------|-------|---------|-------|
| CLI / Library | `__version__` in `__init__.py` | `1.0.0-rc1` | Semver |
| Proxy | `PROXY_VERSION` in `proxy.py` | `0.4.0` | Semver |
| Config | `meta.configVersion` in `openclaw.json` | `2026-03-07-v1` | Date-based |

## Configuration Metadata

`~/.openclaw/openclaw.json` carries a `meta` section:

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

- **configVersion** — human-readable date-stamped version string
- **tokenpakVersion** — expected proxy/CLI version this config targets
- **lastUpdated** — ISO 8601 UTC timestamp of last config write
- **configHash** — SHA-256 (first 12 chars) of normalized config (meta excluded)

### Config Hash Calculation

```python
import hashlib, json

def config_hash(config: dict) -> str:
    normalized = {k: v for k, v in sorted(config.items()) if k != "meta"}
    raw = json.dumps(normalized, sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:12]
```

## Version Lock File

`~/vault/System/tokenpak.lock.json` pins the expected state:

```json
{
  "proxyVersion": "0.4.0",
  "configVersion": "2026-03-07-v1",
  "configHash": "sha256:3a1ef11ffb19",
  "lockedAt": "2026-03-07T20:46:45Z",
  "lockedBy": "trix"
}
```

On agent startup, the agent checks its running config against the lock file. Drift is logged as a warning.

## Drift Detection

Drift occurs when:
- Running proxy version ≠ lock `proxyVersion`
- Current config hash ≠ lock `configHash`

Check for drift:
```bash
tokenpak version      # shows all versions + drift status
tokenpak config validate  # validates hash integrity
```

## Proxy Version Endpoints

The running proxy exposes:

- `GET /version` — current proxy version, config hash, uptime, python version
- `GET /health` — full health report (includes `version` field)

```bash
curl http://localhost:8766/version
# → {"version":"0.4.0","configHash":"3a1ef11ffb19","uptime":42,...}
```

## Versioning Policy

- Config changes bump `meta.configVersion` (date + counter, e.g. `2026-03-07-v2`)
- Proxy changes bump `PROXY_VERSION` semver
- Breaking config changes also bump `meta.tokenpakVersion`
- Lock file updated after every successful `tokenpak update` or `tokenpak config sync`
