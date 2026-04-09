# tokenpak Claude Code plugin — per-mode behavior matrix

> **⚠️ FUTURE-DEFAULT WARNING — READ FIRST**
>
> `claude -p --bare` will become the default for `-p` in a future Claude Code release.
> When that flip happens, plugin discovery (hooks, skills, MCP, memory, CLAUDE.md) is **silently
> skipped** unless you pass `--plugin-dir <path>` explicitly. The `test_modes.sh` smoke-test
> harness is the canary: if the `cli-default` row starts failing while `cli-bare` passes, the
> default flip has shipped and CLI/cron users need `--plugin-dir` added to their invocations.
>
> Source: [Claude Code headless docs](https://code.claude.com/docs/en/headless)

---

## Mode definitions

| Mode ID | Invocation | Description |
|---------|-----------|-------------|
| **TUI** | `claude` (interactive terminal) | Full interactive TUI. All plugin features available. |
| **CLI `-p`** | `claude -p "…"` (headless, no `--bare`) | Headless one-shot. Plugin loaded. `/menu` unavailable. `PermissionRequest` hook skipped. |
| **CLI `--bare`** | `claude -p --bare "…"` | Headless, zero auto-discovery. Plugin **not** loaded. Future default for `-p`. |
| **TMUX** | `claude` in ≥2 concurrent tmux panes | Same as TUI per-pane. Concurrent file writes (telemetry JSONL) require shared file locks. |
| **IDE-VSCode** | Claude Code VSCode extension | Plugin auto-discovered. `PermissionRequest` handled by IDE UI; may behave differently. |
| **Cron** | `claude -p "…"` from a non-TTY cron job | Non-interactive. Same as CLI `-p` — plugin loaded, `PermissionRequest` skipped. |
| **SDK** | Anthropic Agent SDK (`from claude_agent_sdk import …`) | Filesystem plugins at `~/.claude/plugins/` are **not** auto-loaded. Plugin is a no-op without CCP-23 helpers. |
| **Cursor / Windsurf** | Cursor or Windsurf IDE | No Claude Code plugin system. Plugin is a no-op. No workaround. |

---

## Per-mode behavior matrix

Each cell uses: ✅ works as expected · ⚠️ partial / conditional · ❌ not available

| Component | TUI | CLI `-p` | CLI `--bare` | TMUX | IDE-VSCode | Cron | SDK | Cursor/Windsurf |
|-----------|-----|----------|--------------|------|------------|------|-----|----------------|
| **Plugin discovery** | ✅ auto | ✅ auto | ❌ skipped [^1] | ✅ auto | ✅ auto | ✅ auto | ❌ no-op [^2] | ❌ no system |
| **MCP server (5 OSS tools)** | ✅ via `.mcp.json` | ✅ via `.mcp.json` | ❌ not loaded [^1] | ✅ per-pane instance | ✅ via extension | ✅ via `.mcp.json` | ❌ no-op [^2] | ❌ no system |
| **Skills — auto-invoke** | ✅ description-match | ✅ description-match [^3] | ❌ not loaded | ✅ per-pane | ✅ description-match | ✅ description-match | ❌ no-op | ❌ no system |
| **Skills — `/menu`** | ✅ `/tokenpak-status` etc. | ❌ non-interactive [^4] | ❌ not loaded | ✅ interactive pane | ✅ IDE panel | ❌ non-interactive | ❌ no-op | ❌ no system |
| **SessionStart banner** | ✅ fires | ✅ fires [^5] | ❌ hooks not loaded | ✅ per-pane | ✅ fires | ✅ fires | ❌ hooks not loaded | ❌ no system |
| **post-edit-validation hook** (CCP-15, default-off) | ⚠️ opt-in only | ⚠️ opt-in only [^5] | ❌ hooks not loaded | ⚠️ opt-in only | ⚠️ opt-in only | ⚠️ opt-in only | ❌ hooks not loaded | ❌ no system |
| **protect-paths hook** (CCP-16, default-on) | ✅ PreToolUse blocks | ✅ PreToolUse blocks [^5] | ❌ hooks not loaded | ✅ per-pane | ✅ PreToolUse blocks | ✅ PreToolUse blocks | ❌ hooks not loaded | ❌ no system |
| **telemetry-stamp hook** (CCP-17, default-on) | ✅ JSONL per tool call | ✅ JSONL per tool call | ❌ hooks not loaded | ⚠️ concurrent-safe write required [^6] | ✅ JSONL per tool call | ✅ JSONL per tool call | ❌ hooks not loaded | ❌ no system |
| **review-prep hook** (Pro, CCP-18) | ⚠️ Pro + license key | ⚠️ Pro; no PermissionRequest [^7] | ❌ hooks not loaded | ⚠️ Pro + license key | ⚠️ Pro + license key | ⚠️ Pro; no PermissionRequest | ❌ hooks not loaded | ❌ no system |
| **research-analyst subagent** (OSS, planned) | ✅ natural language | ✅ auto-invoked | ❌ not loaded | ✅ per-pane | ✅ | ✅ auto-invoked | ❌ no-op | ❌ no system |
| **migration-planner subagent** (Pro) | ⚠️ Pro + license key | ⚠️ Pro + license key | ❌ not loaded | ⚠️ Pro + license key | ⚠️ Pro + license key | ⚠️ Pro + license key | ❌ no-op | ❌ no system |
| **security-reviewer subagent** (Pro) | ⚠️ Pro + license key | ⚠️ Pro + license key | ❌ not loaded | ⚠️ Pro + license key | ⚠️ Pro + license key | ⚠️ Pro + license key | ❌ no-op | ❌ no system |

---

## Footnotes

[^1]: **`--bare` skips all auto-discovery.** Hooks, skills, plugins, MCP servers, auto memory, and
`CLAUDE.md` are not loaded. Use `--plugin-dir <path>` to load the plugin explicitly.
Source: [Claude Code headless](https://code.claude.com/docs/en/headless)

[^2]: **Agent SDK does not auto-load filesystem plugins.** `~/.claude/plugins/` is not scanned.
Plugins must be passed via the `plugins` option in `ClaudeAgentOptions` (Python) or `options`
(TypeScript). See CCP-23 for the SDK helper module that bridges this gap.
Source: [Agent SDK overview](https://platform.claude.com/docs/en/api/agent-sdk/overview)

[^3]: **Auto-invoke in CLI `-p` mode is the only entry point for skills.** User-typed `/skill-name`
is not available in non-interactive mode. Skills with `disable-model-invocation: true` cannot be
accessed at all in `-p` mode. Design skills as standing instructions, not interactive commands.
Source: [Claude Code skills](https://code.claude.com/docs/en/skills)

[^4]: **`/menu` is interactive-only.** The `/skill-name` shortcut requires an interactive terminal
(TUI or IDE panel). In `-p` mode, describe the task in natural language and Claude selects the
appropriate skill automatically via the description field.
Source: [Claude Code skills](https://code.claude.com/docs/en/skills)

[^5]: **Most hooks fire in CLI `-p` mode.** `SessionStart`, `PreToolUse`, `PostToolUse`, and
`PostToolUseFailure` all fire in headless mode. **`PermissionRequest` does NOT fire** — use
`PreToolUse` with an exit-code-2 block response for permission decisions in non-interactive mode.
Source: [Claude Code hooks](https://code.claude.com/docs/en/hooks-guide)

[^6]: **TMUX multi-pane concurrent telemetry writes.** When two or more panes run `claude`
simultaneously, both telemetry-stamp hook instances write to the same daily JSONL file. The hook
implementation (CCP-17) uses shared file locks to prevent torn writes. Verify with
`test_modes.sh::test_tmux`.
Source: CCP-06 (atomic JSONL append), CCP-17 (telemetry-stamp hook)

[^7]: **`PermissionRequest` does not fire in non-interactive mode.** The review-prep hook (CCP-18)
uses `PreToolUse` for blocking in CLI `-p` and cron contexts, not `PermissionRequest`. The TUI
path uses `PermissionRequest` for the richer interactive approval UI.
Source: [Claude Code hooks](https://code.claude.com/docs/en/hooks-guide)

---

## Unsupported modes

These modes do not support the Claude Code plugin system. The plugin is a no-op unless an explicit
workaround is applied.

| Mode | Why unsupported | Workaround |
|------|----------------|------------|
| **`claude -p --bare`** | `--bare` skips all auto-discovery by design. | Pass `--plugin-dir "${CLAUDE_PLUGIN_ROOT}"` explicitly, or set `CLAUDE_PLUGIN_DIR` env var. |
| **Agent SDK** | SDK does not scan `~/.claude/plugins/`. Plugin must be registered programmatically. | Use CCP-23 SDK helper module (`tokenpak.integrations.claude_code.sdk`). |
| **Cursor / Windsurf** | These IDEs do not implement the Claude Code plugin specification. | No workaround. Access tokenpak features directly via the proxy API (`http://localhost:8766`). |

---

## Quick reference: which hooks fire in which mode?

| Hook event | TUI | CLI `-p` | CLI `--bare` | Notes |
|------------|-----|----------|--------------|-------|
| `SessionStart` | ✅ | ✅ | ❌ | Fires in all loaded-plugin modes |
| `PreToolUse` | ✅ | ✅ | ❌ | Use for permission decisions in non-interactive mode |
| `PostToolUse` | ✅ | ✅ | ❌ | Used by telemetry-stamp and post-edit-validation hooks |
| `PermissionRequest` | ✅ | ❌ | ❌ | **TUI-only.** Not fired in headless or non-interactive contexts. |
| `PostToolUseFailure` | ✅ | ✅ | ❌ | |
| `SessionEnd` | ✅ | ✅ | ❌ | |

Source: [Claude Code hooks guide](https://code.claude.com/docs/en/hooks-guide)

---

## Smoke-test harness

The companion harness at `tests/test_modes.sh` verifies that the plugin loads correctly in
supported modes and that negative assertions hold (e.g., `--bare` does NOT load the plugin).
Run it after any Claude Code upgrade to detect silent behavior changes:

```bash
bash tokenpak/tokenpak/integrations/claude_code/tests/test_modes.sh
```

A non-zero exit code means at least one mode has regressed. See the harness comments for how to
wire it into CI.

---

*Matrix derived from Claude Code docs as of 2026-04-08. Each claim cites its upstream source.
Verify against live docs if behavior appears to have changed: check the smoke-test harness first.*
