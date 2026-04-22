# tokenpak v1.2.2

## Tier-package separation — the first usable release

1.2.2 is the first 1.2.x release where the full CLI surface has been exercised end-to-end against a running proxy. 1.2.0 and 1.2.1 both had release-blocking runtime bugs caught before any customer-facing announcement went out — they were held as GitHub Drafts and never publicly announced. **Install 1.2.2.**

### What's fixed vs. 1.2.0 and 1.2.1

- **`tokenpak install-tier <tier>`** now exists on the CLI (it was an unregistered module in 1.2.0).
- **`tokenpak audit *`** and **`tokenpak compliance report`** no longer `ImportError` on OSS installs — clean Enterprise upgrade message.
- **`tokenpak dashboard`** no longer crashes with `ModuleNotFoundError: tokenpak.token_manager` (stale import from a prior refactor).
- **`tokenpak status`** no longer crashes when the proxy is running (circuit-breakers shape mismatch).
- **`tokenpak start`** tells the truth: child stderr to `~/.tokenpak/proxy-stderr.log`, 10s health-poll, surfaces the tail of stderr if the daemon dies.
- **`tokenpak demo`** runs the live compression demo without requiring the `recipes/` data directory; recipe-catalog paths print a friendly install-hint if the dir is missing.
- **`tokenpak benchmark`** no longer crashes on a stale `from .agent.compression.recipes` relative import, and degrades gracefully when the recipe catalog is absent.
- **`tokenpak index`** with no args prints a usage hint instead of an argparse traceback.
- **Output headers** show `TOKENPAK v1.2.2 | …` instead of a hardcoded `v0.3.1`.
- **`python3 -m tokenpak.proxy`** works again — fixes a 20k+ restart crash-loop for anyone running the fleet's `tokenpak-proxy.service` systemd unit.
- Default for **`upstream.ollama`** is `http://localhost:11434` (was a Tailscale IP from the dev fleet).

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
tokenpak --version                   # 1.2.2
```

For the paid distribution chain, see `tokenpak install-tier --help`.

---

Full changelog: [CHANGELOG.md](CHANGELOG.md)
