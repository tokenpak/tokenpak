# tokenpak v1.2.0

## Tier-package separation

Paid-tier features (Pro/Team/Enterprise) now ship as a separate package, **tokenpak-paid**, distributed through the license-gated index at `pypi.tokenpak.ai`. The OSS `tokenpak` package stays free forever; paid features live behind your subscription.

### If you're an OSS-only user

Nothing changes. All the commands you already use (`status`, `doctor`, `config`, `integrate`, `index`, `search`, `demo`, `start`, `stop`, `cost`, …) work exactly as before.

### If you've been using paid commands

`optimize`, `dashboard`, `compliance`, `policy`, `vault`, `workflow`, `handoff`, and 18 other paid-tier commands now require an active subscription. First time you run one in 1.2.0 you'll see:

```
⚠ The `tokenpak optimize` command requires a Pro subscription.
  Run: tokenpak activate <YOUR-KEY>
  Then: tokenpak install-tier pro
```

Migrating takes two commands once you have a key:

```bash
tokenpak activate <YOUR-LICENSE-KEY>
tokenpak install-tier pro      # or: team, enterprise
```

`install-tier` resolves `tokenpak-paid` from the license-gated private index and pip-installs the real implementations. Your OSS install is unaffected.

### What's new under the hood

- **Plugin discovery** — The OSS CLI auto-discovers paid commands via Python entry-points (`tokenpak.commands` group), feature-flagged via `TOKENPAK_ENABLE_PLUGINS=1`.
- **Three-layer gating** — (1) license-key-gated PEP 503 index controls who can download; (2) `tokenpak_paid.entitlements.gate_command` runtime-gates every paid command against your tier and features; (3) license-server periodic revalidation, offline-tolerant (30 days), with tier-dependent grace periods (14d Pro / 7d Team / 3d Enterprise).
- **`install-tier` subcommand** — thin pip helper that authenticates to `pypi.tokenpak.ai` using your local license key for HTTP Basic auth (`__token__:<KEY>`).

### Breaking changes

- 25 command modules at `tokenpak.cli.commands.*` are now `DeprecationWarning` stubs. Any callable you import from them is aliased to an upgrade-stub that prints a message and exits 2. Public symbol names are preserved — downstream code that only imports symbols (without invoking them) still works.
- `tokenpak.enterprise.{audit,compliance,governance,policy,sla}` now raises `ImportError` on attribute access. The canonical home is `tokenpak_paid.enterprise.*`.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                   # 1.2.0
```

For the paid distribution chain, see `tokenpak help install-tier`.

---

Full changelog: [CHANGELOG.md](CHANGELOG.md)
