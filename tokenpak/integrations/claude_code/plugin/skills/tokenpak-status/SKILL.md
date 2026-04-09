---
name: tokenpak-status
description: "tokenpak-status — show the tokenpak Claude Code plugin's active version, MCP tools, hooks, vault root, and proxy status. Run this first to understand what the plugin is doing for you."
allowed-tools:
  - mcp__tokenpak-claude-code__search_corpus
  - mcp__tokenpak-claude-code__extract_structured_fields
  - mcp__tokenpak-claude-code__summarize_related_issues
  - mcp__tokenpak-claude-code__build_context_pack
  - mcp__tokenpak-claude-code__prepare_review_packet
user-invocable: true
disable-model-invocation: false
---

# tokenpak-status

Run a full status check of the tokenpak Claude Code plugin. Report each section below.
Execute `${CLAUDE_SKILL_DIR}/check.sh` first to gather shell-level facts, then call the
MCP tools to verify tool availability. Use `$ARGUMENTS` for any user-supplied flags
(e.g. `--verbose`). Session: `${CLAUDE_SESSION_ID}`.

---

## Version

Run: `python -m tokenpak.integrations.claude_code.mcp_server --self-test 2>&1 | head -2`
or read `plugin.json` at `${CLAUDE_PLUGIN_ROOT}/plugin.json` to get the plugin version.
Also show the tokenpak package version via `python -c "import tokenpak; print(tokenpak.__version__)"`.

Report as:
```
plugin version : <version or "unknown">
package version: <tokenpak.__version__ or "unknown">
```

---

## MCP Tools

Call `mcp__tokenpak-claude-code__search_corpus` with `{"query": "status check", "top_k": 1}`.
Do NOT interpret the result — only report whether the call succeeded or returned `no-corpus`.

Report all five tools and their availability:
```
search_corpus             : ok | no-corpus | error
extract_structured_fields : ok | error
summarize_related_issues  : ok | no-corpus | error
build_context_pack        : ok | no-corpus | error
prepare_review_packet     : ok | no-corpus | error
```

If `ENABLE_TOOL_SEARCH` is not set to `true`, tool calls may silently fail on non-first-party
gateways. Run `tokenpak doctor --claude-code` to diagnose.

---

## Hooks

Read `${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json` (if it exists). List each declared hook with its
enabled/disabled status. If the file is absent, report `hooks.json: not found`.

Expected hooks (declared in hooks.json):
```
protect-paths        : enabled | disabled | not found
post-edit-validation : enabled | disabled | not found  (default off)
telemetry-stamp      : enabled | disabled | not found
review-prep          : enabled | disabled | not found  (Pro-only)
session-start-banner : enabled | disabled | not found
```

---

## Vault

Check `check.sh` output for `VAULT_ROOT`. Report:
```
vault root: <resolved path> | unset
index     : present | absent | unset
```

If vault is unset, MCP tools that require corpus access will return `status: no-corpus`.
Run `tokenpak index <path>` after setting vault_root in plugin config.

---

## Proxy

Check `check.sh` output for proxy ping. Report:
```
proxy url : <url from ANTHROPIC_BASE_URL or pluginConfig> | unconfigured
proxy ping: ok (<ms>ms) | offline | unconfigured
```

If proxy is offline but `ANTHROPIC_BASE_URL` points at localhost:8766, run `tokenpak start`.
Proxy is optional — the MCP tools work without it.

---

## Mode

Detect the active consumption mode (best-effort from environment):

1. Check `$TERM_PROGRAM`:
   - `vscode` → **IDE-VSCode** (plugin loads normally)
   - `cursor` or `Windsurf` → **IDE-unsupported** ⚠️
     > Cursor and Windsurf do not load Claude Code plugins. Use the proxy directly
     > (`ANTHROPIC_BASE_URL=http://localhost:8766`) or the CCP-23 SDK helpers instead.
2. Check `$TMUX`:
   - Set → **TMUX** ⚠️
     > TMUX multi-pane mode detected — vault index access uses shared file locks (CCP-06).
     > Avoid running concurrent `tokenpak index` operations from different panes.
3. Check whether stdin is a TTY (`[ -t 0 ]`):
   - Not a TTY → **non-interactive / `-p` mode** ⚠️
     > Running in `claude -p` non-interactive mode — the `/menu` is unavailable.
     > tokenpak skills are auto-invoked by Claude based on intent matching.
4. Check `$CRON_INVOCATION` or absence of `$HOME`:
   - Set → **cron / scheduled**
5. Default → **CLI / TUI**

Report:
```
mode: <CLI | TUI | TMUX | IDE-VSCode | IDE-unsupported | non-interactive | cron>
```

For the full per-mode behavior matrix, see `MODES.md` (CCP-22) in the plugin docs.

---

## Summary

After gathering all sections, print a compact summary:

```
tokenpak plugin status
======================
version : <plugin-version>
tools   : <N>/5 available
hooks   : <N> declared
vault   : <resolved | unset>
proxy   : <ok | offline | unconfigured>
mode    : <detected mode>
```

If `$ARGUMENTS` contains `--verbose`, include the full section output above the summary.
