# How to Update TokenPak

TokenPak provides CLI commands to update the proxy, CLI, and configuration from a centralized source.

## Update Notifications

Automatic checks are disabled until you opt in. At the end of the first eligible
interactive command, TokenPak asks before making an automatic PyPI update-check
request:

```text
TokenPak can check PyPI once per day for new releases.
    The check sends no TokenPak project, prompt, completion, usage, credential,
    file, tool-inventory, vault, or proxy-log data.
    PyPI still sees ordinary HTTPS/TLS transport metadata such as your IP,
    TLS handshake metadata, and HTTP headers.
    Enable daily update checks? [y/N]:
```

Enter defaults to **No**. If you opt in, every automatic attempt—successful or
failed—is cached for 24 hours. When a newer release is available, the end of a
successful command shows:

```text
⚠️  TokenPak 1.14.0 is available (you have 1.13.0).
    Options: [U]pdate now  [S]kip this version
    Choose [u/S]:
```

- **Update** runs the existing safe `tokenpak update` flow. Pip installs are
  upgraded in place; pipx installs use `pipx upgrade tokenpak`.
- **Skip** suppresses that exact release. A later release can notify again.
- JSON/CSV/JSONL/raw/Markdown output, quiet, non-interactive, CI, server,
  browser-launching, and long-running invocations never prompt or check.
- Set `TOKENPAK_NO_UPDATE_CHECK=1` to disable both the check and notification.

The local update-check state contains only your consent choice, attempt time,
latest public version, and skipped release. The check is a bodyless HTTPS GET to
`https://pypi.org/pypi/tokenpak/json`; it sends no TokenPak project, prompt,
completion, usage, credential, file, tool-inventory, vault, or proxy-log data.
Ordinary network transport metadata remains visible to PyPI.

Manage the saved preference without making a request:

```bash
tokenpak update --check-status
tokenpak update --enable-checks
tokenpak update --disable-checks
```

`tokenpak update --check` is a one-time explicit check. It does not enable
future automatic checks.

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
tokenpak update # Full update (proxy + config)
tokenpak update --check # Check for updates, don't install
tokenpak update --check-status # Show automatic-check consent/cache state
tokenpak update --enable-checks # Enable automatic checks (no request now)
tokenpak update --disable-checks # Disable automatic checks (no request)
tokenpak update --dry-run # Preview changes
tokenpak update --force # Update even if already up to date
tokenpak update --core-only # Skip config merge
```

## Update Flow

1. Check PyPI for latest `tokenpak` version
2. Download and install through the active package manager (`pip` or `pipx`)
3. If proxy was running → restart it
4. Refresh the configured TokenPak version lock with the new version and hash

## Multi-Agent Environments

When multiple TokenPak instances share configuration, all installations should
run the same version.

```bash
# On each agent machine:
tokenpak update
tokenpak version # verify all match
```

The configured lock file acts as the canonical version pin. An installation
with version drift warns on startup.

## Config Sync

To pull the latest config from the configured Git source:

```bash
tokenpak config sync # sync from the configured Git source
tokenpak config sync --dry-run # preview only
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

See the [troubleshooting guide](troubleshooting.md) for common issues.

Run `tokenpak doctor` for a full diagnostics report.
