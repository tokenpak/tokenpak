# OpenClaw integration scripts

Canonical install + uninstall scripts for routing OpenClaw's gateway through
TokenPak's compression + caching pipeline (Claude Code companion bridge,
Codex Path 1, etc.).

Full reference: <https://docs.tokenpak.ai/integrations/openclaw>

## Files

- `tokenpak-inject.sh` — idempotent installer that mutates
  `~/.openclaw/openclaw.json` to add `tokenpak-*` providers, mirror auth
  profiles, stamp the `X-TokenPak-Backend` header, sync the Codex JWT,
  and write a one-time `.pre-tokenpak-backup` for clean revert. Designed
  to run as `ExecStartPre=` on `openclaw-gateway.service`, so the
  TokenPak entries self-heal on every gateway restart.
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
