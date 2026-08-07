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
through `codex mcp add` so it shows up in `codex mcp list`, and installing its
hook pipeline and an AGENTS.md fragment into the selected Codex home.

### Running more than one Codex session at once

Run as many as you like. `tokenpak codex` uses your existing local Codex
history and does not require exclusive access to it, exactly like running
`codex` directly: Codex keeps its session state in write-ahead-logging SQLite
databases, which coordinate concurrent readers and a serialized writer across
processes. TokenPak never opens Codex's local state, so it has no reason to
serialize your sessions and does not try to.

Sessions started this way share one history lineage, so each will see work the
others have committed.

Advanced operators can still select an explicit compatibility mode for
diagnostics, recovery, or automation:

```bash
# Stable internal history per project.
TOKENPAK_CODEX_SESSION_MODE=workspace tokenpak codex

# New internal history for one invocation.
TOKENPAK_CODEX_SESSION_MODE=isolated tokenpak codex
```

Unlike the default, `workspace` allows only one session per project at a time —
TokenPak generates and provisions that home, so a second concurrent session in
the same project is refused rather than racing the first one's setup. Use the
default or `isolated` to run several at once.

The MCP server is the same stdio JSON-RPC program in both cases:
`python3 -m tokenpak.companion.mcp.server`. Only the discovery mechanism
differs between clients.

### Tools the companion exposes

| Tool | What it does |
|---|---|
| `estimate_tokens` | Estimate token count for text or a file. Returns a compact result: token and character counts plus a short estimator disclosure. |
| `check_budget` | Remaining cost budget for this session and today, with an explicit scope note (only TokenPak-routed traffic is counted). |
| `session_info` | Companion status, session stats, and configuration. |
| `journal_write` | Add a note to the session journal (decisions, milestones). |
| `journal_read` | Read journal entries for this or a past session. |
| `load_pak` | Load a Pak (compressed context bundle) from a prior session; omit the session id to list what is available. |
| `load_capsule` | Deprecated legacy alias of `load_pak` (capsule is the pre-rebrand name for a Pak). |
| `prune_context` | Compress verbose tool output / logs to cut token usage. |
| `vault_search` | BM25 search over your indexed vault, top-K with scores. |
| `vault_retrieve` | Fetch the full content of one vault block by id or path. |

Cost estimation, budget enforcement, and per-prompt journaling all happen
automatically in the hook pipeline, and every tool call re-sends the
conversation — so the injected guidance tells the model **not** to call tools
for routine accounting. `estimate_tokens` is for a go/no-go decision on very
large content; `check_budget` is for when you actually ask about budget.

On Claude Code, the settings overlay allowlists `mcp__tokenpak-companion__*`,
so companion tools run without permission prompts. On Codex, the launcher
writes no per-tool approval policy; Codex's own approval and sandbox settings
govern tool calls.

### Lean tool profile

Tool schemas are re-sent to the model with every request, so advertising all
ten tools has a recurring token cost. With `TOKENPAK_COMPANION_PROFILE=lean`,
`tools/list` advertises only the core tools — `load_pak`, `prune_context`,
`journal_read`, `journal_write`, `vault_search`, `vault_retrieve` — whose
value justifies that cost; hooks and the CLI cover the rest out-of-band.
Dispatch is never filtered: a call to an unadvertised tool still works.

---

## Supported MCP config shape

You normally do **not** hand-write this: the launcher generates it. The shapes
below are the canonical reference so you can confirm what is being registered
(and reproduce it if you wire a client manually).

### Claude Code

`tokenpak claude` writes an MCP config to
`~/.tpk/companion/run/mcp.json` (the legacy `~/.tokenpak/` home is still
honored when an older install holds state there; `TOKENPAK_HOME` overrides the
location) and passes it to Claude Code via `--mcp-config`:

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

`tokenpak codex` registers the server with `codex mcp add`, which stores it in
the selected Codex home's `config.toml` (`~/.codex/config.toml` in the default
`shared` mode):

```toml
[mcp_servers.tokenpak-companion]
command = "/path/to/python"
args = ["-m", "tokenpak.companion.mcp.server"]
```

Non-default companion settings — a daily budget, a non-default profile, or an
overridden journal directory — are forwarded as `env` entries on that same
table so the server subprocess sees them.

Registration is idempotent: when `codex mcp get tokenpak-companion` already
succeeds, the launcher leaves the existing entry untouched. To regenerate it
(for example after moving your Python environment), run
`codex mcp remove tokenpak-companion` and launch `tokenpak codex` again.

The launcher writes no tool policy keys: there is no generated
`enabled_tools` list and no per-tool approval table. The advertised tool set
comes from the server's own `tools/list` response at runtime, so it always
matches the registry — including the lean profile's thinner list.

---

## First-run cold start

The MCP server itself starts fast: importing
`tokenpak.companion.mcp.server` is a sub-second operation. Retrieval does not
happen in the server process at all — `vault_search`, `vault_retrieve`,
`prune_context`, and the other proxy-backed tools are thin HTTP calls to the
local TokenPak proxy, which owns the vault index and any heavy, optional ML
backends (`sentence_transformers` / `transformers` / `torch`).

Earlier builds imported `sentence_transformers` at module load, which
transitively pulled in `torch`: a cold import of roughly 13-18 seconds. That
delay could exceed Claude Code's MCP-connect window, so the client reported the
server as a failed setup even though nothing was actually broken.

### The durable fix

Retrieval moved out of the server process entirely: the proxy owns the index,
and the MCP server carries no heavy imports of its own. A fresh
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

This is a **workaround, not a fix**: it only widens the connect window. On
Codex, the equivalent knob is Codex's own `startup_timeout_sec` setting on the
server's `config.toml` entry — the launcher does not set it, and the server's
light startup should not need it.

If you want to skip the MCP server entirely (no tools injected), set:

```bash
TOKENPAK_COMPANION_MCP=0 tokenpak claude
```

---

## Verify the setup

```bash
# Companion + environment health (read-only).
tokenpak doctor

# Codex: end-to-end install verification (registration, hooks, AGENTS.md).
tokenpak codex doctor

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
