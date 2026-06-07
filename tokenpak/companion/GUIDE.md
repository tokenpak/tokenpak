# tokenpak companion — Integration Guide

## Quick Start

```bash
pip install tokenpak         # or: pip install -e .
tokenpak claude              # launches Claude Code with companion active
tokenpak claude --print "Fix the auth bug in server.py"   # non-interactive
```

The companion starts, prints a startup banner to stderr, then execs into the
`claude` binary with MCP tools, a settings overlay, and a system prompt
fragment already wired in.

---

## Config Reference

All env vars use the `TOKENPAK_COMPANION_` prefix.  Set them in your shell
profile, `.env`, or CI environment.  Values are read once at launch via
`CompanionConfig.from_env()`.

| Env Var | Default | Description |
|---|---|---|
| `TOKENPAK_COMPANION_ENABLED` | `1` | Master switch. Set `0` to disable companion without removing the launcher. |
| `TOKENPAK_COMPANION_BUDGET` | `0` (unlimited) | Daily budget in USD. Set to e.g. `5.00` to cap daily spend. `0` = no cap. |
| `TOKENPAK_COMPANION_PROFILE` | `balanced` | Preset profile: `lean`, `balanced`, or `verbose`. Controls prune threshold and cost display. |
| `TOKENPAK_COMPANION_JOURNAL_DIR` | `~/.tokenpak/companion` | Directory for journal database, budget database, and capsule storage. |
| `TOKENPAK_COMPANION_HOOKS` | `1` | Enable/disable the `UserPromptSubmit` hook pipeline (token estimation, cost journaling, budget gate). |
| `TOKENPAK_COMPANION_MCP` | `1` | Enable/disable the MCP server. When disabled, no MCP tools are injected. |
| `TOKENPAK_COMPANION_SHOW_COST` | `1` | Print per-prompt token and cost estimates to stderr (visible in Claude Code TUI). |
| `TOKENPAK_COMPANION_PRUNE_THRESHOLD` | `50000` | Token count above which the hook suggests calling `prune_context`. |

### Profile presets

| Profile | Prune Threshold | Show Cost |
|---|---|---|
| `lean` | 20,000 | yes |
| `balanced` (default) | 50,000 | yes |
| `verbose` | 100,000 | yes |

### Examples

```bash
# $5/day budget, lean profile
export TOKENPAK_COMPANION_BUDGET=5.00
export TOKENPAK_COMPANION_PROFILE=lean
tokenpak claude

# Custom journal directory (e.g. project-scoped storage)
export TOKENPAK_COMPANION_JOURNAL_DIR=/my/project/.tokenpak
tokenpak claude

# Disable hooks (no cost tracking, no budget gate)
TOKENPAK_COMPANION_HOOKS=0 tokenpak claude
```

---

## Memory sources — bring your own knowledge base

The companion can surface lessons from your own Markdown notes, not just its
built-in memory schema. Any folder of `.md` / `.markdown` files works (scanned
recursively) — no special directory layout is required.

**Tell the companion where your notes live** with the
`TOKENPAK_COMPANION_MEMORY_DIRS` environment variable. It accepts an
OS-path-separator- or comma-separated list; `~` is expanded; missing or empty
entries are dropped (never fatal):

```bash
export TOKENPAK_COMPANION_MEMORY_DIRS=~/notes:~/work/journal
```

The configured directories are parsed into `CompanionConfig.memory_dirs` and
reported by the `session_info` MCP tool, which also surfaces a hint when no
memory source is set — so an empty result is self-explaining.

**Ingest with the library API.** `ingest_from_dir` ingests a single directory;
`ingest_sources` orchestrates every configured source and returns a per-source
status report:

```python
from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.memory.decision_memory import DecisionMemoryDB
from tokenpak.companion.memory.lesson_ingest import ingest_from_dir, ingest_sources

db = DecisionMemoryDB()

# Ingest a single directory of notes
n = ingest_from_dir("~/notes", db)

# Or ingest every directory from TOKENPAK_COMPANION_MEMORY_DIRS, with status
cfg = CompanionConfig.from_env()
report = ingest_sources(db, memory_dirs=cfg.memory_dirs)
# -> {"total": int, "sources": [{"path", "kind", "ingested", "reason"}, ...]}
```

Lessons are extracted from the `## Lessons Learned` / `## Notes` /
task-summary sections of each file. Missing or unreadable files are skipped,
never fatal.

---

## MCP Tools Reference

When the companion is active, Claude Code gains seven MCP tools served by
`tokenpak.companion.mcp.server`.  The server runs as a stdio MCP process.

| Tool | Description |
|---|---|
| `estimate_tokens` | Estimate token count for inline text or a file path. Call before including large content to decide if it's worth the cost. |
| `check_budget` | Return remaining cost budget for this session and today. Call before starting expensive multi-step tasks. |
| `load_capsule` | Load a memory capsule from a prior session. Omit `session_id` to list the 10 most recent available capsules. |
| `prune_context` | Compress verbose text (large tool outputs, error logs) by keeping the beginning and end and eliding the middle. Default target: 2,000 tokens. |
| `journal_read` | Read journal entries for the current or a named session. Omit `session_id` to list recent sessions with stats. |
| `journal_write` | Save a note, decision, or milestone to the current session journal for recall in future sessions. |
| `session_info` | Return companion version, session ID, call count, config summary, and session cost/request counts. |

### Tool parameters

**`estimate_tokens`**
```json
{ "text": "...", "file_path": "..." }   // one of text or file_path
```

**`load_capsule`**
```json
{ "session_id": "abc123" }   // omit to list available capsules
```

**`prune_context`**
```json
{ "text": "...", "max_tokens": 2000 }   // max_tokens is optional
```

**`journal_read`**
```json
{ "session_id": "abc123", "entry_type": "user", "limit": 20 }   // all optional
```
Valid `entry_type` values: `auto`, `user`, `milestone`, `cost`.

**`journal_write`**
```json
{ "content": "Decided to use sqlite for the budget store." }
```

---

## Budget Setup

The companion tracks spend in two windows: **session** (since `tokenpak claude`
launched) and **daily** (across all sessions today, persisted to SQLite).

### Setting a daily cap

```bash
export TOKENPAK_COMPANION_BUDGET=5.00   # $5.00/day
tokenpak claude
```

When the daily total reaches the cap, the `UserPromptSubmit` hook **blocks** the
next send and prints:

```
tokenpak: budget exceeded ($5.00 / $5.00 daily)
```

Claude Code surfaces this as a blocked prompt with the reason string.

### Checking budget interactively

Ask Claude to call the `check_budget` tool, or use `session_info`:

```
> check_budget
> session_info
```

### Model cost estimates

The companion uses a simplified cost table (per 1M tokens):

| Model tier | Input | Output | Cached input |
|---|---|---|---|
| `opus` | $15.00 | $75.00 | $1.50 |
| `sonnet` | $3.00 | $15.00 | $0.30 |
| `haiku` | $0.80 | $4.00 | $0.08 |

These are estimates.  The proxy's telemetry database is the source of truth for
actual billing.

---

## Capsule Management

Capsules are compressed session summaries (~500–2,000 tokens) built from
transcript content.  They let you inject past-session context without loading
the full transcript.

### Storage location

Capsules are stored as Markdown files in:
```
~/.tokenpak/companion/<session_id>.md    # default journal dir
```

Override with `TOKENPAK_COMPANION_JOURNAL_DIR`.

### Listing capsules

```
> load_capsule    # omit session_id → lists 10 most recent
```

### Loading a capsule

```
> load_capsule with session_id "2026-04-14-abc123"
```

The capsule is returned as a Markdown block with sections:
- **Context:** — what the session was about
- **Decisions:** — decisions made and why
- **Artifacts:** — files written or modified
- **Action items:** — outstanding work
- **Insights:** — surprising findings, gotchas

### Capsule anatomy

A capsule is built automatically when the session ends (via the transcript
parser in `tokenpak.companion.capsules.builder`).  It extracts structure from
the `.jsonl` transcript without an LLM call — deterministic and free.

---

## Troubleshooting

### MCP tools not available in Claude Code

The MCP server only loads if `--mcp-config` is passed at launch.  Always start
via `tokenpak claude`, not directly with `claude`.

Check: `TOKENPAK_COMPANION_MCP` must not be `0`.

### `claude: command not found`

`tokenpak claude` execs the `claude` binary.  Ensure Claude Code CLI is
installed and on `PATH`:
```bash
which claude    # should return a path
claude --version
```

### MCP server not showing in session

Run with verbose output to see the startup banner and MCP config path:
```bash
tokenpak claude 2>&1 | head
```
The banner prints: `tokenpak: companion ready (balanced, no budget cap)`.

If the banner appears but tools are missing, check that `claude` recognizes the
`--mcp-config` flag (requires Claude Code ≥ the version that added MCP stdio
support).

### Budget gate blocking sends unexpectedly

Check the current daily total:
```
> check_budget
```
To clear the daily counter, delete the budget database:
```bash
rm ~/.tokenpak/companion/budget.db
```

### `load_capsule` returns no capsules

Capsules are built from the session transcript.  If the transcript file was
not written (e.g. non-interactive `--print` mode), no capsule is created.

Check: `ls ~/.tokenpak/companion/*.md`

### Module override not taking effect

`TOKENPAK_COMPANION_PROFILE` is read once at launch.  Changing it mid-session
has no effect.  Restart `tokenpak claude` after changing env vars.
