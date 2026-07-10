# Companion & MCP Setup (First Run)

The **tokenpak companion** runs Claude Code or Codex with tokenpak wired in: a
small set of MCP tools, a per-prompt cost line, an optional daily budget gate,
and direct search over your indexed vault. This page explains what the
companion is, the **exact supported MCP config shape**, how to verify a clean
first run, and what to do if the MCP server ever times out on connect.

The companion is **local-first**: every tool reads and writes on your own
machine. No companion data is transmitted anywhere.

---

## What the companion is

The companion is a launcher. You run:

```bash
pip install tokenpak
tokenpak claude          # launches Claude Code with the companion active
tokenpak codex           # launches Codex with the companion active
```

`tokenpak claude` prints a short startup banner, then execs into your real
`claude` binary with three things already wired in:

- an **MCP server** exposing tokenpak tools (see below),
- a **settings overlay** (permissions + hooks) layered on top of your existing
  `~/.claude/settings.json`, and
- a **system-prompt fragment** describing the tools.

`tokenpak codex` does the equivalent for Codex, registering the same MCP server
through `codex mcp add` so it shows up in `codex mcp list`.

The MCP server is the same stdio JSON-RPC program in both cases:
`python3 -m tokenpak.companion.mcp.server`. Only the discovery mechanism
differs between clients.

### Tools the companion exposes

| Tool | What it does |
|---|---|
| `estimate_tokens` | Estimate token count for text or a file before you include it. |
| `check_budget` | Remaining cost budget for this session and today. |
| `session_info` | Companion status, session stats, and configuration. |
| `journal_write` | Add a note to the session journal (decisions, milestones). |
| `journal_read` | Read journal entries for this or a past session. |
| `load_capsule` | Load a memory capsule from a prior session. |
| `prune_context` | Compress verbose tool output / logs to cut token usage. |
| `vault_search` | BM25 search over your indexed vault, top-K with scores. |
| `vault_retrieve` | Fetch the full content of one vault block by id or path. |

`journal_write` and `prune_context` **mutate** companion state, so on Codex
they are configured to prompt for approval; the read-shaped tools run without a
prompt.

---

## Supported MCP config shape

You normally do **not** hand-write this: the launcher generates it. The shapes
below are the canonical reference so you can confirm what is being registered
(and reproduce it if you wire a client manually).

### Claude Code

`tokenpak claude` writes an MCP config to
`~/.tokenpak/companion/run/mcp.json` and passes it to Claude Code via
`--mcp-config`:

```json
{
  "mcpServers": {
    "tokenpak-companion": {
      "type": "stdio",
      "command": "/path/to/python",
      "args": ["-P", "-m", "tokenpak.companion.mcp.server"]
    }
  }
}
```

- `command` is the **current interpreter** (`sys.executable`), so the server
  always runs under the same Python that has tokenpak installed.
- `-P` (PYTHONSAFEPATH) keeps the launch directory off `sys.path`. Without it, a
  `tokenpak` directory or symlink in your working directory can shadow the
  installed package as a namespace package and break the server on import.

### Codex

`tokenpak codex` registers the server with `codex mcp add` and writes an
explicit policy block into `~/.codex/config.toml`:

```toml
[mcp_servers.tokenpak-companion]
command = "/path/to/python"
args = ["-P", "-m", "tokenpak.companion.mcp.server"]
startup_timeout_sec = 30
tool_timeout_sec = 60
enabled_tools = ["estimate_tokens", "check_budget", "load_capsule", "prune_context", "journal_read", "journal_write", "session_info", "vault_search", "vault_retrieve"]
default_tools_approval_mode = "auto"
tool_approvals = { journal_write = "prompt", prune_context = "prompt" }
```

The companion owns the policy keys (`startup_timeout_sec`, `tool_timeout_sec`,
`enabled_tools`, `default_tools_approval_mode`, `tool_approvals`) and rewrites
them whenever you re-run `tokenpak codex`. Everything else in the table:
`command`, `args`, `env`, is preserved verbatim. `enabled_tools` is generated
from the canonical tool registry, never hand-maintained.

---

## First-run cold start

The MCP server itself starts fast: importing
`tokenpak.companion.mcp.server` is a sub-second operation, and the heavy,
optional ML backends (`sentence_transformers` / `transformers` / `torch`) are
**lazy**. They load only when a retrieval tool is actually called, not at
server startup.

Earlier builds imported `sentence_transformers` at module load, which
transitively pulled in `torch`: a cold import of roughly 13-18 seconds. That
delay could exceed Claude Code's MCP-connect window, so the client reported the
server as a failed setup even though nothing was actually broken.

### The durable fix

The retrieval backend is now lazy-loaded. Availability is detected cheaply at
import, and the model is imported only on first retrieval. A fresh
`tokenpak claude` or `tokenpak codex` should connect without tripping the
timeout. No configuration is required to get this behavior; it is the default.

### Safe workaround

If you are on a constrained machine, a cold filesystem cache, or a very tight
client timeout and you still see an MCP connect timeout on the first launch,
raise Claude Code's MCP startup timeout for that session:

```bash
# Claude Code client-side env var; value is in milliseconds.
MCP_TIMEOUT=30000 tokenpak claude
```

This is a **workaround, not a fix**: it only widens the connect window. On Codex
the equivalent cushion (`startup_timeout_sec = 30`) is already written into the
config block above, so no manual step is needed there.

If you want to skip the MCP server entirely (no tools injected), set:

```bash
TOKENPAK_COMPANION_MCP=0 tokenpak claude
```

---

## Verify the setup

```bash
# Companion + environment health (read-only).
tokenpak doctor

# Codex: confirm the server is registered.
codex mcp list        # tokenpak-companion should appear

# Claude Code: in the TUI, run
/mcp                  # tokenpak-companion should be "connected"
```

A connected server plus a clean `tokenpak doctor` means first run succeeded. If
`/mcp` shows the server as failed, re-run with the `MCP_TIMEOUT` workaround
above and, if it then connects, file the slow first-launch so it can be tuned.
The durable path should not need the cushion.

---

## See also

- [Getting Started](getting-started.md) — Day 1 proxy + client setup.
- [Onboarding Guide](onboarding.md) — Day 1 to Day 30 journey.
- [CLI Reference](cli-reference.md) — full command reference.
