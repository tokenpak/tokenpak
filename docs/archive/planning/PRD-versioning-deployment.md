# PRD: TokenPak Production Versioning & Deployment Protocol

**Status:** DRAFT
**Author:** Sue (Governor)
**Date:** 2026-03-24
**Review:** Kevin Yang
**Task chain:** → SPEC → PLAN → TASKS

---

## 1. Problem Statement

TokenPak has no formal versioning, deployment, or update protocol. Today:

- Proxy is deployed as a standalone file with no version number
- Users have no way to update without knowing about `sync-tokenpak-proxy.sh`
- No CHANGELOG, no migration notes, no safety for user config/data
- Updates could silently overwrite user-modified configs or `.md` files
- Agents (Trix, Cali) can drift from the canonical source with no detection
- No rollback mechanism if an update breaks production

This PRD defines a production-grade standard that mirrors how OpenClaw handles updates — safe, transparent, config-preserving.

---

## 2. Goals

1. **Single command update** — `tokenpak update` handles everything
2. **Config-safe** — never overwrites user data (`.json`, `.md`, `.db`, `.sql`, `.yaml`)
3. **Versioned** — proxy and package have explicit SemVer versions
4. **Transparent** — CHANGELOG, version history, migration notes per release
5. **Rollback** — one command to undo a bad update
6. **Fleet-aware** — fleet agents stay in sync automatically
7. **Auditable** — every update logged with who/what/when

---

## 3. Non-Goals

- **No auto-update** — always explicit user action (`tokenpak update`)
- **No cloud sync** — vault-first, no external telemetry without consent
- **No forced migrations** — migrations are opt-in with escape hatch

---

## 4. User Stories

| Who | Wants | So That |
|-----|-------|---------|
| End user | `tokenpak update` — one command | They never think about it |
| End user | Update can't break their config | They trust upgrades |
| End user | See what changed before applying | They make informed decisions |
| Fleet operator | All agents update together | Fleet stays consistent |
| Developer (Kevin) | PR → version bump → publish | Release is one workflow |
| Developer | Rollback if something breaks | Night sleep is preserved |

---

## 5. Architecture: Two Deliverables

TokenPak ships in two layers that version independently but are tested together:

```
┌─────────────────────────────────┐
│  tokenpak (PyPI)  v1.0.x        │  ← Python package (pip install tokenpak)
│  - CLI, compression, routing    │
│  - tokenpak update / start / .. │
└─────────────────────────────────┘
         │ bundled inside
         ▼
┌─────────────────────────────────┐
│  proxy.py  PROXY_VERSION 1.0.x  │  ← Standalone runtime
│  - The actual HTTP proxy server │
│  - Imports from tokenpak pkg    │
└─────────────────────────────────┘
```

**Key rule:** proxy.py is bundled inside the pip package at `tokenpak/runtime/proxy.py`.
`tokenpak update` extracts and deploys it. Users never touch the file directly.

---

## 6. Version Numbering

Both the package and proxy follow **SemVer** (MAJOR.MINOR.PATCH):

| Bump | When |
|------|------|
| PATCH | Bug fixes, minor improvements (no breaking changes, no migration) |
| MINOR | New features, new config keys (backward compatible) |
| MAJOR | Breaking changes to config schema, API, or user-facing behavior |

**Rule:** Package version and PROXY_VERSION always match after a release.
If only the proxy changes, still bump the package version (single source of truth).

---

## 7. Protected User Files

`tokenpak update` will **never touch** these paths:

```
~/.tokenpak/config.json          ← user's main config
~/.tokenpak/config.yaml          ← user's main config (yaml variant)
~/.tokenpak/*.db                 ← cost/usage databases
~/.tokenpak/vault/               ← user vault content
~/.tokenpak/keys/                ← API keys and tokens
~/.tokenpak/rules/               ← custom compression rules
~/.tokenpak/templates/           ← custom prompt templates
~/vault/**/*.md                  ← vault markdown files
~/vault/**/*.json                ← vault JSON files
~/vault/**/*.sql                 ← vault SQL files
```

**Migration rule:** If a new version requires a config schema change:
1. Detect old schema on startup
2. Log a warning with migration instructions
3. Never auto-migrate
4. Provide `tokenpak migrate` command to explicitly apply

---

## 8. Update Flow (from user perspective)

```
tokenpak update

Checking versions...
  Package   (installed) : 1.0.1
  Package   (PyPI)      : 1.0.2  → upgrade available
  Proxy     (running)   : 1.0.1
  Proxy     (canonical) : 1.0.2  → upgrade available

What's new in 1.0.2:
  • Fix: WebSocket proxy memory leak on idle connections
  • Improve: Compression ratio +4% on short prompts
  → Full changelog: https://github.com/kaywhy331/tokenpak/releases/1.0.2

Updating package...  ✓
Syncing proxy...     ✓
Restarting proxy...  ✓
Writing lock file... ✓

✓ Update complete. TokenPak 1.0.2 is running.
```

---

## 9. Rollback Flow

```
tokenpak rollback

Current version: 1.0.2
Previous version: 1.0.1 (installed 2026-03-24 14:00 UTC)

Rolling back to 1.0.1...
  Restoring proxy...   ✓
  Restoring package... ✓
  Restarting proxy...  ✓

✓ Rolled back to 1.0.1.
```

Rollback uses the `.tokenpak/versions/` cache (last 3 versions kept).

---

## 10. CHANGELOG Standard

File: `CHANGELOG.md` at repo root. Format: **Keep a Changelog** (https://keepachangelog.com).

```markdown
## [1.0.2] - 2026-03-24

### Fixed
- WebSocket proxy memory leak on idle connections (#142)
- CLI `tokenpak update` now correctly detects proxy drift

### Improved
- Compression ratio +4% on short prompts (< 500 tokens)
```

Sections: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.

---

## 11. Release Checklist (enforced in CI)

See: `docs/release-checklist.md`

Every release requires:
- [ ] Version bump in `pyproject.toml` (MAJOR.MINOR.PATCH)
- [ ] `PROXY_VERSION` in `proxy.py` matches
- [ ] CHANGELOG entry with date
- [ ] Tests pass (`pytest tests/ -q` — 0 failures)
- [ ] `tokenpak version` shows correct version
- [ ] `tokenpak update --check` detects the update
- [ ] `tokenpak update` applies without error
- [ ] `tokenpak rollback` works
- [ ] Config files untouched after update
- [ ] Migration docs if config schema changed

---

## 12. Fleet Sync (internal)

For the 3-agent fleet, `sync-tokenpak-proxy.sh` becomes a thin wrapper:

```bash
# This is now just an alias for:
ssh trix@trixbot "tokenpak update --proxy-only"
ssh cali@calibot "tokenpak update --proxy-only"
```

Or: push the proxy via git to vault and each agent's heartbeat runs `tokenpak update --check` and auto-applies if drift detected.

---

## 13. Open Questions for Kevin

1. **Bundle proxy in pip?** — Cleanest UX but requires restructuring how `tokenpak serve` finds proxy.py. Recommend: yes, bundle at `tokenpak/runtime/proxy.py`.
2. **PyPI or internal Cloudsmith for `tokenpak update`?** — Public users hit PyPI. Internal fleet agents could use Cloudsmith for pre-release builds.
3. **Auto-update on heartbeat?** — Let heartbeat run `tokenpak update --check` and notify, or silently apply PATCH updates?
4. **Version lockfile location** — `~/.tokenpak/tokenpak.lock.json` (current) or in vault?

---

*Next: SPEC.md → detailed technical spec for implementation*
