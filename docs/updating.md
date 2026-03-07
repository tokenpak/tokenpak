# How to Update TokenPak

TokenPak provides CLI commands to update the proxy, CLI, and configuration from a centralized source.

## Quick Update

```bash
# Check if updates are available (no changes)
tokenpak update --check

# Apply updates
tokenpak update

# Preview what would change without applying
tokenpak update --dry-run
```

## What Gets Updated

| Component | Update Source | User Files Touched? |
|-----------|--------------|---------------------|
| CLI / Library | PyPI (`pip install --upgrade`) | No |
| Proxy (`proxy.py`) | Git / PyPI | Yes — core only |
| Config defaults | Git vault | No (user config preserved) |
| Lock file | Auto-updated after update | Yes |

## Update Flags

```bash
tokenpak update               # Full update (proxy + config)
tokenpak update --check       # Check for updates, don't install
tokenpak update --dry-run     # Preview changes
tokenpak update --force       # Update even if already up to date
tokenpak update --core-only   # Skip config merge
```

## Update Flow

1. Check PyPI for latest `tokenpak` version
2. Download and install via `pip install --upgrade tokenpak`
3. If proxy was running → restart it
4. Update `~/vault/System/tokenpak.lock.json` with new version/hash

## Multi-Agent Environments

When multiple agents share a vault, all agents should run the same versions.

```bash
# On each agent machine:
tokenpak update
tokenpak version   # verify all match
```

The lock file at `~/vault/System/tokenpak.lock.json` acts as the canonical version pin. Any agent with drift will warn on startup.

## Config Sync

To pull the latest config from the canonical vault/git source:

```bash
tokenpak config sync            # sync from vault (git)
tokenpak config sync --dry-run  # preview only
tokenpak config pull --source=url --url=https://example.com/tokenpak-config.json
```

## Rollback

Not yet automated. To rollback:
```bash
pip install tokenpak==0.3.0
# Restart proxy
```

Future: `tokenpak rollback <version>`

## Troubleshooting

See `~/vault/System/TROUBLESHOOTING.md` for common issues.

Run `tokenpak doctor` for a full diagnostics report.
