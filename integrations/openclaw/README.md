# OpenClaw integration scripts

Canonical install + uninstall scripts for routing OpenClaw's gateway through
TokenPak's compression + caching pipeline (Claude Code companion bridge,
Codex Path 1, etc.).

Full reference: <https://docs.tokenpak.ai/integrations/openclaw>

## Files

- `tokenpak-inject.sh` — idempotent installer with two modes:

  - **Default (additive)** — Mirrors providers as `tokenpak-*`,
    copies auth profiles, adds `tokenpak-*` model refs to the
    allowlist, syncs the Codex JWT. Your existing primary model,
    fallback chains, and per-agent model selections are
    **untouched**. Result: you see `tokenpak-*` providers in OpenClaw
    as new options alongside your existing routing; you opt in by
    manually selecting one.
  - **Exclusive (`--exclusive` or `TOKENPAK_INJECT_EXCLUSIVE=1`)** —
    Additionally rewrites every primary model to its `tokenpak-*`
    version and clears fallback chains, so all agent traffic
    auto-routes through the proxy. Use this for hosts dedicated to
    tokenpak (single-purpose bots, testbeds). Destructive — overwrites
    your model selections.

  Either way, existing non-tokenpak provider entries are **never
  removed**. A one-time `.pre-tokenpak-backup` is written so
  `tokenpak-uninstall.sh` can restore the pre-injection state cleanly.
  Designed to run as `ExecStartPre=` on `openclaw-gateway.service`, so
  the TokenPak entries self-heal on every gateway restart.
- `tokenpak-uninstall.sh` — companion that restores the pre-install
  config from the backup (or strips `tokenpak-*` entries in place when no
  backup exists), removes the systemd drop-in, optionally stops the
  TokenPak proxy and purges caches. Always writes
  `*.uninstall-rollback` files so the post-install state can be recovered.

## Quick install

```bash
install -m 0755 tokenpak-inject.sh tokenpak-uninstall.sh ~/.local/bin/

mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
cat > ~/.config/systemd/user/openclaw-gateway.service.d/tokenpak-inject.conf <<'EOF'
[Service]
ExecStartPre=%h/.local/bin/tokenpak-inject.sh
EOF

systemctl --user daemon-reload
# Then restart the gateway by killing its main PID:
kill $(systemctl --user show openclaw-gateway.service -p MainPID --value)
```

For exclusive (proxy-only) routing, add the flag to the drop-in:

```ini
[Service]
ExecStartPre=%h/.local/bin/tokenpak-inject.sh --exclusive
```

…or set `TOKENPAK_INJECT_EXCLUSIVE=1` in the gateway's environment.

## Quick uninstall

```bash
~/.local/bin/tokenpak-uninstall.sh --dry-run    # preview
~/.local/bin/tokenpak-uninstall.sh               # do it
~/.local/bin/tokenpak-uninstall.sh --stop-proxy --purge-caches --yes
                                                 # full teardown
```

## Doctor compatibility

`openclaw doctor` and `doctor --repair` are safe to run alongside this
integration. The TokenPak entries (providers, auth profiles, the
`X-TokenPak-Backend` header) are preserved. If `doctor --repair`
normalises per-model `contextWindow` values, the next gateway restart
re-runs `tokenpak-inject.sh` and restores them automatically. See the
docs for the full compatibility matrix.
