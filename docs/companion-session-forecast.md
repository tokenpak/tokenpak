# Session forecasts in your terminal

The companion can keep a compact session display visible while you work.
It shows the session identity, guard state, forecast availability or estimated
remaining range, and spending or guard runway when space permits.

## Claude Code

```sh
tokenpak claude
```

An interactive companion launch adds a native footer through its temporary
settings overlay. Existing user or project status lines take precedence in
`auto` mode. To explicitly select the TokenPak footer for this launch:

```sh
tokenpak claude --status-surface=native
```

Install `jq` for this adapter. Without it the footer says
`TokenPak | status unavailable`; startup and `tokenpak doctor --claude-code`
report the missing dependency. The reader does not inspect transcripts or
make provider requests.
The footer refreshes every two seconds, including while idle, using Claude's
[status line refresh timer](https://code.claude.com/docs/en/statusline).

## Codex

Inside an existing tmux session, `tokenpak codex` opens a small forecast pane
below Codex. The pane closes when that companion exits. Outside tmux, request
a dedicated terminal session explicitly:

```sh
tokenpak codex --status-surface=tmux
```

This requires `tmux`. It starts a private tmux server with its own configuration;
existing servers, panes, and bindings are preserved. Detaching keeps the session
running and prints the command to reattach. This is a terminal pane, not an
extension of Codex's built-in `/statusline` fields.

The default `auto` mode never starts a multiplexer. In an ordinary terminal it
prints the panel option and leaves Codex's own interface in place. Noninteractive
commands such as `codex exec`, JSON output, and install-only runs create no panel.

## Read or disable a display

```sh
tokenpak status --line --session YOUR_SESSION_ID
tokenpak status --full --session YOUR_SESSION_ID
tokenpak status --json --session YOUR_SESSION_ID
tokenpak codex --status-surface=off
tokenpak claude --status-surface=off
```

`TOKENPAK_STATUS_SURFACE=off` disables both adapters. Supported surface values
are `auto`, `native` (Claude), `tmux` (Codex), and `off`. Native arguments after
`--` are forwarded without interpretation.

Use the exact native session ID when reading from a separate terminal. A managed
companion binds its own session on startup, resume, and clear; it does not borrow
the newest session from another terminal. A session needs completed requests in
the local proxy's ledger before its economics can be shown. Starting a display
does not backfill earlier traffic or invent missing usage measurements.

## Read the estimates correctly

- `est` and `~` identify estimates. Remaining ranges carry their 50% interval
  label; the 90% ceiling appears when there is room.
- `guard limit` is the estimated number of turns before a configured constraint,
  not the number of turns needed to finish your task.
- `learning`, `no data`, and `unavailable` are real states. Subscription traffic
  can show `subscription` rather than a fabricated dollar bill.
- `stale` means the cached observation expired. Expired numbers are hidden.
- Narrow terminals show fewer complete fields, preserving guard information.
  The display makes no numeric savings claim and triggers no session switch.

One process per managed display refreshes the explicit session's local snapshot
about every two seconds. The shell adapters read a pre-rendered cache with a
ten-second expiry. Cache files are private, live under the companion's own run
directory, and contain bounded session metadata rather than request bodies.
The snapshot JSON preserves field sources, timestamps, routing mode, and the
original session-economics contract. The optional Pro daemon is not required.
