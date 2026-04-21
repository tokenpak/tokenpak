# tokenpak v1.2.1

## Tier-package separation — release-blocking fixes over 1.2.0

1.2.1 is the first usable release of the tier-package separation. 1.2.0 shipped with four release-blocking bugs that a pre-announcement audit caught before any customer-facing communication went out. 1.2.0 was yanked; **install 1.2.1**.

### What's fixed vs. 1.2.0

- **`tokenpak install-tier <tier>`** — the upgrade command documented in the release notes — now actually exists on the CLI. In 1.2.0 it was an unregistered module.
- **`tokenpak audit *`** and **`tokenpak compliance report`** no longer crash with `ImportError` on OSS installs. They route to a clean "upgrade to Enterprise" message.
- **Default for `upstream.ollama`** is now `http://localhost:11434` (was a private Tailscale IP from our dev fleet; broken default for external users).
- **`tokenpak savings`** gracefully reports "no data yet" on fresh installs instead of dumping an `sqlite3.OperationalError` traceback.

### What 1.2.x is about (unchanged from 1.2.0)

Paid-tier features (Pro/Team/Enterprise) now ship as a separate private package, **tokenpak-paid**, distributed through the license-gated index at `pypi.tokenpak.ai`. The OSS `tokenpak` package stays free forever; paid features live behind your subscription.

### If you're an OSS-only user

Nothing changes. All the commands you already use (`status`, `doctor`, `config`, `integrate`, `index`, `search`, `demo`, `start`, `stop`, `cost`, …) work exactly as before.

### If you've been using paid commands

`optimize`, `dashboard`, `compliance`, `policy`, `vault`, `workflow`, `handoff`, and 18 other paid-tier commands now require an active subscription. First time you run one in 1.2.x you'll see:

```
⚠ The `tokenpak optimize` command requires a Pro subscription.
  Run: tokenpak activate <YOUR-KEY>
  Then: tokenpak install-tier pro
```

Migration takes two commands once you have a key:

```bash
tokenpak activate <YOUR-LICENSE-KEY>
tokenpak install-tier pro      # or: team, enterprise
```

`install-tier` resolves `tokenpak-paid` from the license-gated private index and pip-installs the real implementations. Your OSS install is unaffected.

### What's new under the hood

- **Plugin discovery** — The OSS CLI auto-discovers paid commands via Python entry-points (`tokenpak.commands` group), feature-flagged via `TOKENPAK_ENABLE_PLUGINS=1`.
- **Three-layer gating** — (1) license-key-gated PEP 503 index controls who can download; (2) `tokenpak_paid.entitlements.gate_command` runtime-gates every paid command against your tier and features; (3) license-server periodic revalidation, offline-tolerant (30 days), with tier-dependent grace periods (14d Pro / 7d Team / 3d Enterprise).
- **`install-tier` subcommand** — thin pip helper that authenticates to `pypi.tokenpak.ai` using your local license key for HTTP Basic auth (`__token__:<KEY>`).

### Breaking changes (from 1.1.x)

- 25 command modules at `tokenpak.cli.commands.*` are now `DeprecationWarning` stubs. Any callable you import from them is aliased to an upgrade-stub that prints a message and exits 2. Public symbol names are preserved — downstream code that only imports symbols (without invoking them) still works.
- `tokenpak.enterprise.{audit,compliance,governance,policy,sla}` now raises `ImportError` on attribute access. The canonical home is `tokenpak_paid.enterprise.*`.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                   # 1.2.1
```

For the paid distribution chain, see `tokenpak install-tier --help`.

---

Full changelog: [CHANGELOG.md](CHANGELOG.md)
