# Dashboard — Per-Mode Views

TokenPak's dashboard adapts its panels to the consumption mode you're using. Open the dashboard at `http://localhost:8766/dashboard` and use the **mode selector** at the top to switch between views.

## Accessing the dashboard

```bash
tokenpak serve          # start the proxy + dashboard
open http://localhost:8766/dashboard
```

Remote dashboard access defaults to an SSH local tunnel. Run this from the
machine where you want the browser to open:

```bash
tokenpak dashboard connect dashboard.example.internal --remote-port 8766 --local-port auto --open
```

TokenPak starts an SSH forward to the remote loopback dashboard, stores the
control socket and PID under `~/.tpk/tunnels/`, waits for
`http://127.0.0.1:<local>/health` to return OK, then opens
`http://127.0.0.1:<local>/dashboard`. Use
`tokenpak dashboard disconnect dashboard.example.internal` to close the tunnel.

Use `tokenpak dashboard --public` only as an advanced non-tunneled exposure
mode when you explicitly intend the dashboard URL to be reachable without SSH.

Or jump straight to a specific mode:

```
http://localhost:8766/dashboard?mode=cli
http://localhost:8766/dashboard?mode=tui
http://localhost:8766/dashboard?mode=tmux
http://localhost:8766/dashboard?mode=sdk
http://localhost:8766/dashboard?mode=ide
http://localhost:8766/dashboard?mode=cron
```

## Available modes

### `cli` — CLI mode

Shows **cost by working directory**, budget burn rate, and recent `tokenpak doctor` runs. Best for developers running Claude Code in a terminal.

### `tui` — TUI mode

Shows a **live cost meter** (auto-refreshes every 5 seconds), savings tape for the last 10 sessions, and current session token count. Best for interactive TUI sessions.

### `tmux` — tmux mode

Shows **per-agent attribution**, concurrent session count, and a fairness check so no single agent monopolises the day's budget. Best for multi-agent setups in tmux.

### `sdk` — SDK mode

Shows **API usage by model**, OTLP export status, and structured error count. Best for developers using the Python or REST SDK directly.

### `ide` — IDE mode

Shows **active workspace detection**, inline savings totals, and IDE-specific timeout events. Best for VS Code and JetBrains users.

### `cron` — Cron mode

Shows **job success/failure rate**, Telegram alerts sent, and hard-budget enforcement events. Best for automated cron or batch jobs.

## Shared header

Every mode shows a shared header strip with:

- **Active profile** — the current TokenPak compression profile
- **Total cost today** — aggregate cost across all requests
- **Cache hit rate** — percentage of tokens served from cache

## Default mode

When you visit `/dashboard` without a `?mode=` parameter, TokenPak auto-detects your environment:

| Signal | Detected mode |
|--------|--------------|
| `TOKENPAK_MODE` env var | that mode |
| `$TMUX` or `$TMUX_PANE` set | `tmux` |
| `$VSCODE_PID` or `$JETBRAINS_IDE` set | `ide` |
| `$TOKENPAK_JOB_NAME` or `$CRON_JOB` set | `cron` |
| Default | `cli` |

Override at any time by setting `TOKENPAK_MODE=<mode>` in your environment.
