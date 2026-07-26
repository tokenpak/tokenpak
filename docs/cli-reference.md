# CLI Reference

_Auto-generated from `tokenpak/cli.py` — do not edit by hand._
_To update: edit `tokenpak/cli.py` then run `python scripts/generate-cli-docs.py`._

---

## Group: Getting Started

### `tokenpak setup`

Guided first-run configuration. Prompts interactively when stdin is a terminal. For CI, containers, or any scripted run, state the choices explicitly: `tokenpak setup --profile balanced --yes`. Setup will not assume an answer from a closed stdin.

**Flags:**

- `--profile` — Compression profile to use, instead of prompting — choices: `minimal`, `balanced`, `aggressive`
- `--port` — Proxy port to configure (default 8766)
- `--yes` — Overwrite an existing config without confirming
- `--start` — Also start the proxy after writing config (default: config only)

### `tokenpak start`

Start the TokenPak proxy server, which routes LLM API requests through
Prompt Packing. The proxy listens on localhost:PORT and forwards
compressed requests to your configured LLM providers.

Example:
  tokenpak start --port 8888 --workers 4

(See also `tokenpak serve` for telemetry/ingest variants.)
The proxy reads config from tokenpak.yaml or ~/.tokenpak/config.yaml

**Flags:**

- `--port` — Port to listen on (default: 8766) (default: 8766)
- `--workers` — Number of worker processes (default: 2) (default: 2)
- `--log-level` — Logging level (default: info) (default: info) — choices: `debug`, `info`, `warning`, `error`

### `tokenpak stop`

Stop the proxy

### `tokenpak restart`

Restart the proxy

### `tokenpak demo`

See compression in action

**Flags:**

- `--list` — List all 50 baked-in recipes
- `--category` — Filter by category (general, python, javascript, markdown, config, common_patterns)
- `--recipe` — Show details for a specific recipe by name
- `--file` — Show which recipes match a given file path
- `--seed` — Populate dashboard with 500 realistic demo events (24h window)
- `--seed-count` — Number of demo events to generate (default: 500) (default: 500)
- `--seed-hours` — Time window in hours (default: 24) (default: 24)
- `--clear` — Remove all demo data from telemetry storage

### `tokenpak cost`

View API spend

**Flags:**

- `--week` — Show weekly totals
- `--month` — Show monthly totals
- `--by-model` — Break down by model
- `--export-csv` — Export as CSV

**Subcommands:**

- `show-budget`
  - `--config` — Path to tokenpak config file

### `tokenpak status`

Check proxy health

**Flags:**

- `--limit` — Max retry events to show (default: 20)
- `--full` — Expanded view with all details
- `--by-source` — Breakdown by request source (Claude Code, Codex, API, etc.)
- `--by-provider` — Breakdown by provider (Anthropic, OpenAI, Google, etc.)
- `--tip-cache` — Show compact TIP cache attribution only
- `--minimal` — One-line savings summary
- `--json` — Full JSON data dump
- `--no-meme` — Suppress tagline
- `--days` — Filter to last N days (combinable with --hours)
- `--hours` — Filter to last N hours (combinable with --days)
- `--fleet` — Fleet rollup view — reads rollup_daily
- `--since` — With --fleet: window in days, e.g. '7d' (default: 7d)

### `tokenpak logs`

Show recent logs

**Flags:**

- `--lines`, `-n` — Number of log lines to show (default: 50) (default: 50)

---

## Group: Indexing

### `tokenpak index`

Index a directory

**Flags:**

- `DIRECTORY` — Directory to index
- `--status` — Show indexed file count by type
- `--budget` — default: 8000
- `--workers`, `-w` — Parallel workers (default: 4) (default: 4)
- `--auto-workers` — Use hybrid calibration (static baseline + dynamic adjustment)
- `--recalibrate` — Run static calibration before indexing
- `--calibration-rounds` — Calibration rounds per candidate worker count (default: 2)
- `--max-workers` — Upper worker cap for auto/recalibration (default: 8)
- `--watch` — Watch directory and auto-reindex on file changes
- `--debounce` — Debounce delay in ms for watch mode (default: 500) (default: 500)
- `--no-treesitter` — Force regex-based code processing (skip tree-sitter)
- `--reindex-all` — Reindex every directory registered in ~/.tokenpak/vault.yaml
- `--reindex-path` — Reindex a single directory registered in ~/.tokenpak/vault.yaml

### `tokenpak search`

Search indexed content

**Flags:**

- `QUERY` — Search query
- `--budget` — default: 8000
- `--top-k` — default: 10
- `--gaps` — Path to gaps.json for miss-based retrieval expansion (default: .tokenpak/gaps.json)
- `--inject-refs` — Enable compile-time reference injection (GitHub, URLs)

---

## Group: Configuration

### `tokenpak route`

Manage routing rules

**Subcommands:**

- `list`
  - `--routes` — Path to routes.yaml
- `add`
  - `--model` — Model glob pattern (e.g. 'gpt-4*', 'openai/*')
  - `--prefix` — Prompt prefix match (case-insensitive)
  - `--min-tokens` — Minimum token count (inclusive)
  - `--max-tokens` — Maximum token count (inclusive)
  - `--target` — Target model/provider (e.g. 'anthropic/claude-3-haiku-20240307')
  - `--priority` — Rule priority (lower = higher priority, default 100) (default: 100)
  - `--description` — Optional description (default: )
  - `--routes` — Path to routes.yaml
- `remove`
  - `ID` — Rule ID to remove
  - `--routes` — Path to routes.yaml
- `test`
  - `PROMPT` — Prompt text to test (default: )
  - `--model` — Model name to test against (default: )
  - `--tokens` — Token count override (default: auto-estimated)
  - `--verbose`, `-v` — Show all rules and their match status
  - `--routes` — Path to routes.yaml
- `enable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml
- `disable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml

### `tokenpak goals`

Track savings goals

**Subcommands:**

- `list`
- `detail`
  - `GOAL_ID` — Goal ID
- `add`
  - `--name` — Goal name
  - `--type` — Goal type — choices: `savings`, `compression`, `cache`, `metric`
  - `--target` — Target value
  - `--start` — Start date (YYYY-MM-DD, default: today)
  - `--end` — End date (YYYY-MM-DD, default: 30 days from start)
  - `--description` — Goal description
  - `--metric` — Custom metric name (for metric type)
  - `--rolling-window` — Enable weekly pace tracking
- `edit`
  - `GOAL_ID` — Goal ID to edit
  - `--name` — New goal name
  - `--target` — New target value
  - `--description` — New description
  - `--end` — New end date (YYYY-MM-DD)
- `delete`
  - `GOAL_ID` — Goal ID to delete
- `update`
  - `GOAL_ID` — Goal ID
  - `VALUE` — New current value
- `export`
  - `--output`, `-o` — Output file (default: stdout)
- `history`
- `compare`

### `tokenpak config`

View and edit config

**Subcommands:**

- `sync`
  - `--source` — Config source: git (vault) or url (default: git) — choices: `git`, `url`
  - `--url` — URL for source=url
  - `--dry-run`
- `pull`
  - `--source` — default: git — choices: `git`, `url`
  - `--url` — URL for source=url
  - `--dry-run`
  - `--merge` — Merge strategy (default: merge) — choices: `replace`, `merge`, `diff`
- `validate`
  - `--config` — Path to proxy config file (JSON/YAML) to validate against schema
- `show`
  - `--json` — Output as JSON
- `init`
  - `--force` — Overwrite existing config
  - `--with-env-stub` — Also drop a placeholders-only .env.example under the TokenPak home
- `doctor`
  - `--json` — Output as JSON
  - `--quiet` — Print only the worst finding
  - `--verbose`, `-v` — Include per-check detail
- `env`
  - `--json` — Output as JSON
  - `--no-mask` — Show low-class values unmasked (secret-class values are still masked)
- `path`
- `migrate`
  - `--config-json` — Path to legacy config.json (default: resolved across the canonical and legacy homes)
  - `--dry-run` — Print what would change without writing
- `optimize`
  - `--plan` — Show the deterministic plan without writing (default)
  - `--apply` — Atomically apply the recomputed process-local plan
  - `--status` — Read managed artifacts and drift state without writing
  - `--rollback` — Restore the exact recorded preimage
  - `--profile` — Memory budget policy (default: balanced) — choices: `balanced`, `conservative`, `throughput`
  - `--mode` — Runtime behavior (default: auto) — choices: `auto`, `observe`, `off`
  - `--expect-hash` — With --apply, refuse unless the recomputed plan has this SHA-256
  - `--force` — With --rollback, restore the preimage despite external drift
  - `--json` — Emit machine-readable JSON

---

## Group: Versioning

### `tokenpak version`

Show current version

### `tokenpak update`

Update tokenpak

**Flags:**

- `--check` — Check for updates without installing
- `--force` — Force update even if already up to date
- `--core-only` — Update core only, skip config merge
- `--dry-run` — Show what would change without applying

### `tokenpak uninstall`

Un-route (--soft) or purge state + remove package (--hard)

**Flags:**

- `--soft` — Un-route only (reversible via `tokenpak setup`); keep config/state/package
- `--hard` — Soft + purge state (keeps journal/budget/capsules) + offer package removal
- `--dry-run` — Show the exact operations that would run, change nothing
- `--yes` — Skip confirmation (required for --hard in non-interactive use)
- `--keep-data` — Under --hard, also retain all ~/.tpk user data (config + dbs)
- `--json` — Emit a machine-readable receipt

---

## Group: Operations

### `tokenpak doctor`

Run diagnostics

**Flags:**

- `--fix` — Auto-fix issues where possible
- `--json` — Output results as machine-readable JSON
- `--fleet` — Check all agents in ~/.tokenpak/fleet.yaml
- `--deploy` — Push latest doctor to all agents (use with --fleet)
- `--verbose`, `-v` — Show extra detail for each check
- `--claude-code` — Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE detection)
- `--conformance` — Run TIP self-conformance checks (alias for `tokenpak tip conformance`)
- `--lifecycle` — Show only the compact lifecycle summary (installed/setup/routed/proxy/update)

### `tokenpak dashboard`

Live dashboard

**Flags:**

- `--fleet` — Show fleet-wide summary (TUI)
- `--json` — Export dashboard as JSON (non-interactive)
- `--layout` — Select read-only cockpit layout for terminal or JSON output (default: home) — choices: `home`, `dispatch`, `spend`, `debug`, `fleet`
- `--public` — Advanced: show public URL with token for non-tunneled access
- `--show-token` — Display current dashboard token
- `--new-token` — Regenerate dashboard token

**Subcommands:**

- `connect` — Open a remote dashboard through an SSH local tunnel.
  - `HOST` — SSH host or user@host to connect to
  - `--remote-port` — Remote dashboard port (default: 8766)
  - `--local-port` — Local listener port, or 'auto' to start at 8766 and choose the next free port (default: auto)
  - `--ssh-user` — SSH username when HOST does not include user@
  - `--open` — Open the dashboard URL in the default browser
  - `--no-open` — Print the dashboard URL without opening a browser
  - `--health-timeout` — Seconds to wait for /health to report OK (default: 20.0)
  - `--json` — Output connection result as JSON
- `disconnect` — Close a dashboard SSH local tunnel.
  - `HOST` — SSH host or user@host to disconnect
  - `--ssh-user` — SSH username when HOST does not include user@
  - `--json` — Output disconnect result as JSON

### `tokenpak models`

Per-model breakdown

**Flags:**

- `MODEL` — Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')
- `--raw` — Output as JSON

---

## Group: Companion

### `tokenpak claude`

Launch Claude Code with tokenpak companion active.

All arguments are forwarded verbatim to the claude binary.

Examples:
  tokenpak claude
  tokenpak claude --budget 5.00
  tokenpak claude --print "Fix the bug"
  tokenpak claude --model claude-sonnet-4-6 --print "Review this PR"

**Flags:**

- `--budget` — Daily spend cap in USD; sets TOKENPAK_COMPANION_BUDGET env var
- `ARGS` — Arguments forwarded verbatim to claude

### `tokenpak codex`

Launch OpenAI Codex CLI with tokenpak companion active.

Registers the MCP server, installs hooks, and writes AGENTS.md,
then launches Codex with any user-provided arguments.

Examples:
  tokenpak codex
  tokenpak codex --install-only    # set up without launching Codex
  tokenpak codex doctor            # verify installation
  tokenpak codex uninstall         # clean selected home; preserve shared skills in use
  tokenpak codex --budget 5.00
  tokenpak codex "Fix the login bug"
  tokenpak codex --model o3 -s workspace-write

**Flags:**

- `--budget` — Daily spend cap in USD; sets TOKENPAK_COMPANION_BUDGET env var
- `--install-only` — Run setup (MCP, hooks, AGENTS.md, skills) and exit without launching codex
- `--receipt-only` — Launch vanilla Codex and write a no-body receipt without installing or activating companion setup
- `--receipt-out` — Write a no-body accounting receipt for this Codex process
- `--run-id` — Stable run identifier to include in the accounting receipt
- `ARGS` — Arguments forwarded verbatim to codex (or `doctor` / `uninstall`)

---

## Group: Advanced

### `tokenpak validate`

Validate JSON files

**Flags:**

- `FILE` — Path to the .json TokenPak file
- `--verbose`, `-v` — Show quality hints in addition to errors/warnings
- `--json` — Output validation result as JSON

### `tokenpak diff`

Show context changes

**Flags:**

- `--verbose`, `-v` — Show token counts per block
- `--json` — Output as JSON
- `--since` — Diff from specific time

### `tokenpak stats`

Registry stats

### `tokenpak serve`

Start proxy server

**Flags:**

- `--port` — default: 8766
- `--telemetry` — Start telemetry ingest server
- `--ingest` — Start Phase 5A ingest API server
- `--workers` — Number of uvicorn workers
- `--profile` — Workflow profile for this proxy process (default: TOKENPAK_PROFILE or balanced) — choices: `safe`, `balanced`, `aggressive`, `agentic`, `transparent`
- `--stats-footer` — Print a per-request token-savings receipt (estimated dollars) in the proxy terminal (default: off)
- `--shutdown-timeout` — Seconds to wait for in-flight requests to complete before forcing shutdown (default: 30, or TOKENPAK_SHUTDOWN_TIMEOUT env var)
- `--safe` — Disable compression defaults (restore pre-1.1 passthrough behavior). Equivalent to TOKENPAK_COMPACT=0.

---

## Additional Commands

### `tokenpak activate`

**Flags:**

- `KEY` — Your license key (default: )
- `--email` — Optional email for the license (default: )

### `tokenpak compress`

Compress a piece of text, JSON, or code using TokenPak's compression.
Shows token savings and compressed output.

Note: The proxy handles compression automatically for API requests.
Use this command to test compression on arbitrary content.

Example:
  tokenpak compress < myfile.json
  echo '{"data": "...large JSON..."}' | tokenpak compress --verbose

**Flags:**

- `--file`, `-f` — Input file path (reads from stdin if omitted)
- `--verbose`, `-v` — Show compression blocks
- `--json` — Output as machine-readable JSON

### `tokenpak deactivate`

### `tokenpak features`

Show every feature TokenPak knows about and whether the current license entitles you to use it. Use `tokenpak features explain <feature>` for a single-feature breakdown.

**Flags:**

- `--json` — Emit JSON instead of text
- `--tier` — Filter to a specific tier: free|pro

**Subcommands:**

- `explain`
  - `FEATURE` — Feature key (e.g. T9_replay_system)
  - `--json` — Emit JSON

### `tokenpak help`

Show tier-aware help. Pass a command name for details, or --minimal for compact list.

**Flags:**

- `CMD_NAME` — Command name for detailed help
- `--more` — Show essential + intermediate commands
- `--all` — Show all commands
- `--minimal` — Show compact one-line command list

### `tokenpak home`

Inspect, validate, and migrate the TokenPak home directory. All paths resolve through tokenpak._paths so subcommands honor TOKENPAK_HOME and the canonical ~/.tpk/ boundary.

**Subcommands:**

- `path`
  - `--json`
- `init`
  - `--force` — Overwrite an existing config.json
- `validate`
  - `--json`
- `explain`
  - `--json`
- `migrate` — Copy the legacy ~/.tokenpak/ tree to the canonical ~/.tpk/ location. The legacy tree is left in place as a safety backup; you can prune it manually once satisfied.
  - `--dry-run` — Show what would be copied without writing anything
  - `--force` — Allow merging into an existing ~/.tpk/ (default: refuse and report what to do manually)

### `tokenpak init`

Guided first-run setup wizard: API key, port, vault path.

### `tokenpak integrate`

Show one-step setup instructions for pointing your LLM client at tokenpak.

Examples:
  tokenpak integrate                # list detected clients + SDKs
  tokenpak integrate cursor         # show Cursor setup
  tokenpak integrate claude-code    # show Claude Code setup
  tokenpak integrate --all          # dump instructions for every client

**Flags:**

- `CLIENT` — Client key: claude-code | cursor | cline | continue | aider | codex | openai-sdk | anthropic-sdk | litellm
- `--all` — Show instructions for every supported client
- `--proxy-url` — Override the printed proxy URL (default: $TOKENPAK_PROXY_URL or http://localhost:8766)
- `--apply` — Auto-write config files for the given client (headless / scripted path)
- `--revert` — Restore the most recent backup for the given client (undoes --apply)
- `--tier` — Permission tier to apply with --apply (claude-code / codex only; default: standard). 'fleet' is the legacy full-bypass alias for both TokenPak launchers and never persists into client config. — choices: `strict`, `standard`, `auto`, `fleet`
- `--yes` — Confirm dangerous choices non-interactively (required for legacy --tier fleet)

### `tokenpak license`

**Flags:**

- `--json` — Machine-readable JSON output

### `tokenpak menu`

### `tokenpak plan`

**Flags:**

- `--json` — Machine-readable JSON output

### `tokenpak preview`

Preview compression result for input text (dry-run).

**Flags:**

- `INPUT` — Input text to preview (or reads from stdin)
- `--file` — Read input from file instead of command line
- `--raw` — Show raw compression output (no formatting)
- `--verbose` — Show detailed block breakdown
- `--json` — Output as JSON (machine-readable)

### `tokenpak report`

Generate and display daily savings report.

**Flags:**

- `--markdown` — Output markdown format (for messaging)
- `--json` — Output JSON format

### `tokenpak savings`

Show compression savings summary.

**Flags:**

- `--days` — Rolling window in days (default: 30)

### `tokenpak vault`

Check the health of your vault index and repair stale or corrupted entries.
The vault index stores compressed context blocks and metadata about requests.

Subcommands:
  repair     Check and rebuild stale vault index entries

Example:
  tokenpak vault repair    # Auto-fix corrupted entries
  tokenpak vault-health repair  # Same via alias

**Subcommands:**

- `repair`

---

