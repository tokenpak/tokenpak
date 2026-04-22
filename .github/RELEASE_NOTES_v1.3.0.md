# tokenpak v1.3.0

## Claude Code capability restoration — complete

1.3.0 lands the five-phase Claude Code capability restoration designed in the 2026-04-22 architecture map. Zero legacy code was restored as-is — every capability is implemented natively in the current modular architecture, with route classification + Policy as the single branching signal.

### What changed

- **`RouteClass` taxonomy** — 9 values: `claude-code-{tui,cli,tmux,sdk,ide,cron}`, `anthropic-sdk`, `openai-sdk`, `generic`. One classifier. One answer to "is this Claude Code?" everywhere.
- **Policy-gated pipeline stages** — DLP + context enrichment plug into the canonical pipeline slots. Byte-preserve routes (all `claude-code-*`) can't accidentally have their bodies mutated; the downgrade is enforced at the Stage layer, not in ad-hoc `if` ladders.
- **Backend selector** — `X-TokenPak-Backend: claude-code` routes through the OAuth path (the `claude` CLI subprocess) instead of API-key billing. No silent fallback: if the CLI is absent the selector fails loudly with a diagnostic.
- **`tokenpak integrate claude-code`** — one command wires settings.json + mcp.json + env-var recipe + post-install diagnostic verification.
- **`tokenpak doctor --claude-code`** — shared-diagnostics checks that catch the exact dist-info shadow bug class that hit us in April.
- **Per-mode dashboard** + **inline savings events** + **cost forecast** — final product surfaces.

### No user-visible breaking changes

Every 1.2.x flow keeps working. Default preset policies keep the new stages as no-ops on Claude Code routes until operators explicitly opt in.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                   # 1.3.0
tokenpak integrate claude-code       # wires settings + runs post-install doctor
tokenpak doctor --claude-code        # verify any time
```

### Architecture invariants

- No ad-hoc `"claude-code" in …` branches outside `RouteClassifier`.
- No parallel implementations of DLP / classifier / diagnostics across proxy + companion.
- Companion stays as an entrypoint; shares services rather than duplicating them.
- Byte-preserve contract mechanically enforced by Policy gate, not `if` ladder.

Full per-phase changelog: [CHANGELOG.md](CHANGELOG.md).
