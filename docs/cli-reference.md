# CLI Reference

_Auto-generated from `tokenpak/cli.py` — do not edit by hand._
_To update: edit `tokenpak/cli.py` then run `python scripts/generate-cli-docs.py`._

---

## Group: Getting Started

### `tokenpak setup`

Guided first-run setup

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

### `tokenpak upgrade`

Open the canonical TokenPak Pro upgrade page in your default browser. Target URL is https://tokenpak.ai/pro (override with TOKENPAK_UPGRADE_URL).

**Flags:**

- `--print-url` — Print the upgrade URL to stdout instead of opening a browser

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

### `tokenpak recipe`

Manage compression recipes

**Subcommands:**

- `list`
  - `--category` — Filter by category (general, python, javascript, markdown, config, common_patterns)
- `create`
  - `NAME` — Recipe name (e.g. my-legal-cleanup)
  - `--output-dir` — Directory to write the recipe file (default: current dir) (default: .)
  - `--category` — Recipe category: python, markdown, legal, medical, etc. (default: general)
  - `--description` — Short description (default: )
  - `--match-mode` — Pattern match mode: any|extension|filename|content|path_pattern (default: extension)
  - `--ext` — File extension hint (for extension match mode) (default: txt)
  - `--domain-example` — Use a domain-specific template: legal | medical
- `validate`
  - `FILE` — Path to recipe YAML file
- `test`
  - `FILE` — Path to recipe YAML file
  - `--input-text` — Raw text to test against
  - `--input-file` — Path to a file to use as test input
  - `--filename-hint` — Filename to check pattern matching against (e.g. script.py) (default: )
- `benchmark`
  - `FILE` — Path to recipe YAML file
  - `--samples-file` — JSON file with list of sample strings (default: auto-generated)
  - `--runs` — Repetitions per sample for timing (default: 5) (default: 5)

### `tokenpak template`

Manage prompt templates

**Subcommands:**

- `list`
- `add`
  - `NAME` — Template name
  - `--content` — Template content (use {{var}} for variables)
- `show`
  - `NAME` — Template name
- `remove`
  - `NAME` — Template name
- `use`
  - `NAME` — Template name
  - `--var` — Variable substitution (repeatable) (default: [])

### `tokenpak budget`

Set budget limits

**Subcommands:**

- `set`
  - `--daily` — Daily spend limit in USD
  - `--monthly` — Monthly spend limit in USD
  - `--alert-at` — Alert threshold %% (default 80)
  - `--hard-stop` — Block requests when limit exceeded
- `status`
- `show`
- `history`
  - `--limit` — default: 20
  - `--month` — Show this month

### `tokenpak alerts`

Manage alert channels

**Subcommands:**

- `test`
  - `--channel` — Channel type to test — choices: `webhook`, `slack`
  - `--url` — Webhook URL (for --channel webhook)
  - `--webhook` — Slack incoming-webhook URL (for --channel slack)

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
  - `--config-json` — Path to legacy config.json (default: ~/.tokenpak/config.json) (default: ~/.tokenpak/config.json)
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

### `tokenpak explain`

Explain workflow profiles

**Flags:**

- `--profile` — Profile name (safe|balanced|aggressive|agentic); omit to show all

### `tokenpak permissions`

Manage the TokenPak permission tier system.

Persistent tiers (strict/standard/auto) are written into the client's
own config (Claude Code settings.json / Codex config.toml). Launcher
defaults are TokenPak-scoped only: `tokenpak claude` / `tokenpak codex`
inject session arguments and print a warning — client configs are never
modified by launcher defaults.

Examples:
  tokenpak permissions show                      # tiers + launcher defaults
  tokenpak permissions set auto                  # both clients
  tokenpak permissions set strict --client codex # one client
  tokenpak permissions launcher approval-bypass --client codex
  tokenpak permissions launcher sandbox-bypass --client codex
  tokenpak permissions launcher full-bypass --client both
  tokenpak permissions launcher inherit --client both
  tokenpak permissions set fleet                 # legacy full-bypass alias

**Subcommands:**

- `show`
  - `--json` — Output one schema-versioned JSON object
  - `--quiet` — Suppress normal output; safety warnings still go to stderr
- `set`
  - `TIER` — Tier to apply ('fleet' is a legacy full-bypass alias and requires --client both) — choices: `strict`, `standard`, `auto`, `fleet`
  - `--client` — Which client to configure (default: both) — choices: `claude-code`, `codex`, `both`
  - `--yes` — Confirm the `permissions set fleet` full-bypass alias non-interactively
- `reset`
  - `--client` — Which client to reset (default: both) — choices: `claude-code`, `codex`, `both`
- `launcher` — Set session-only permission defaults for `tokenpak claude` and
`tokenpak codex`. These settings never modify client config files.
Every bypass mode requires confirmation and prints a warning on each
affected launch. Managed administrator policy can still constrain or
reject the client launch.

Modes:
  inherit          inject nothing; use client and managed policy
  approval-bypass disable prompts; keep sandbox limits (Codex only)
  sandbox-bypass  disable sandbox; keep approvals (Codex only)
  full-bypass     disable local prompts and sandbox/permission checks

Use `launcher <mode> --client <client>` to configure. Choose `inherit`
to disable a launcher override. Use `permissions show` to inspect.
  - `LAUNCHER_MODE` — inherit | approval-bypass | sandbox-bypass | full-bypass (partial bypass modes are Codex-only) — choices: `inherit`, `approval-bypass`, `sandbox-bypass`, `full-bypass`
  - `--client` — Client scope; explicit selection is required for safety — choices: `claude-code`, `codex`, `both`
  - `--yes` — Confirm a bypass mode non-interactively; warnings still print
  - `--json` — Output one schema-versioned result object
  - `--quiet` — Suppress success output; safety warnings still go to stderr

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

### `tokenpak benchmark`

Run benchmarks

**Flags:**

- `DIRECTORY` — Directory to benchmark (used with --latency mode)
- `--file` — Benchmark a specific file
- `--samples` — Use built-in sample data (default when no file/directory given)
- `--json` — Output results as JSON
- `--latency` — Run latency/indexing benchmark instead of compression benchmark
- `--iterations` — Iterations for latency benchmark (default: 3) (default: 3)
- `--compare` — Compare baseline vs optimized (latency mode only)

### `tokenpak calibrate`

Calibrate workers

**Flags:**

- `DIRECTORY` — Directory to sample for calibration
- `--max-workers` — default: 8
- `--rounds` — default: 2

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

### `tokenpak diagnose`

Full health check

**Flags:**

- `--json` — Output as JSON
- `--verbose` — Verbose output

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

### `tokenpak timeline`

View savings trend

**Flags:**

- `--days` — Number of days (default 7) (default: 7)
- `--chart` — Show ASCII sparkline chart
- `--json` — JSON output

### `tokenpak attribution`

Savings by source

**Flags:**

- `--days` — Number of days (default 7) (default: 7)
- `--agent` — Filter by agent name
- `--model` — Filter by model
- `--json` — JSON output

### `tokenpak recommendations`

Show ranked, telemetry-backed recommendations from the local TokenPak telemetry store. Reads only — never modifies traffic.

**Flags:**

- `--window` — Rolling window (e.g. 24h, 7d). Default: 24h (default: 24h)
- `--model` — Filter recommendations to a single model name
- `--platform` — Filter recommendations to a single platform (matched against agent_id and payload)
- `--json` — Emit machine-readable JSON output
- `--db-path` — Override telemetry DB path (default: resolved via tokenpak.core.paths.get_db_path)

### `tokenpak models`

Per-model breakdown

**Flags:**

- `MODEL` — Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')
- `--raw` — Output as JSON

### `tokenpak forecast`

Cost projections

**Flags:**

- `--period` — Analysis window (default: 7d) (default: 7d) — choices: `7d`, `30d`, `90d`
- `--alert` — Alert if monthly projection exceeds this USD amount

### `tokenpak debug`

Toggle debug logging

**Subcommands:**

- `on`
- `off`
- `status`
- `list`
  - `--json` — Output as JSON
- `export`
  - `TRACE_ID` — Trace ID to export
  - `--json` — Output as JSON
- `receipt`
  - `REQUEST_ID` — Request ID to render a receipt for (omit to print the support-bundle pointer)
  - `--raw` — Show the receipt without redaction (default: redaction-safe)

### `tokenpak learn`

View learned patterns

**Subcommands:**

- `status`
- `reset`

### `tokenpak vault-health`

Check the health of your vault index and repair stale or corrupted entries.
The vault index stores compressed context blocks and metadata about requests.

Subcommands:
  repair     Check and rebuild stale vault index entries

Example:
  tokenpak vault repair    # Auto-fix corrupted entries
  tokenpak vault-health repair  # Same via alias

**Subcommands:**

- `repair`

### `tokenpak fleet`

Fleet status

**Flags:**

- `--json` — Output as JSON
- `--compact` — Compact one-line output

**Subcommands:**

- `init`

### `tokenpak aggregate`

Aggregate ledger

**Flags:**

- `--since` — Time window, e.g. 7d, 24h, 30m, or ISO date (default: 7d)
- `--json` — JSON output

### `tokenpak requests`

Live request explorer

**Flags:**

- `ACTION` — tail | show | <request_id> (default: tail)
- `REQUEST_ID` — Request id (for show)
- `--limit`, `-n` — Number of rows to show (default: 10)
- `--once` — Print once and exit

### `tokenpak dispatch`

TokenPak Dispatch — scoped, station-based, resumable, gated work packages with a Decision Inbox and delivery receipts (OSS, v0.1-alpha preview — not yet in a released pip package; available on the project main branch; CLI-first).

**Subcommands:**

- `run`
  - `REQUEST` — The request text to dispatch
  - `--route` — Force an explicit Route (e.g. code_task); overrides auto-routing
  - `--autonomy` — Autonomy mode override (default depends on caller) — choices: `advisory`, `draft`, `dispatch_with_approval`, `auto_dispatch_limited`
  - `--ci` — CI/automation caller; default autonomy = auto_dispatch_limited
  - `--dry-run` — Draft only; default autonomy = draft. Performs intake + route selection without persisting anything (no ledger writes)
  - `--confirm` — Treat an approval-gated route as approved (record the bound route)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `status`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `inspect`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--late` — Include late results (post-cancellation TIP output)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `decisions`
  - `--job` — Filter to one job id
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `approve`
  - `DECISION_ID` — Decision id (decision_…)
  - `--option` — Selected option id (default: the recommended option)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `reject`
  - `DECISION_ID` — Decision id (decision_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `pause`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `resume`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `cancel`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `discard-late`
  - `STATION_RUN_ID` — Station run id (stationrun_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `delivery`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `receipt`
  - `JOB_ID` — Dispatch job id (job_…)
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `routes`
  - `--json` — Emit machine-readable JSON instead of human-readable output
- `workers`
  - `--json` — Emit machine-readable JSON instead of human-readable output

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

### `tokenpak creds`

Inspect, manage, and dry-run-route credentials tokenpak can see from
all registered providers (Codex CLI, Claude CLI, env vars,
~/.tokenpak/credentials.toml, and external client profiles).

Proxy fast-path integration still deferred — `creds route` is a
dry-run (what would I pick) with no side effects.

Examples:
  tokenpak creds list                                  # show all
  tokenpak creds doctor                                # hazards
  tokenpak creds add                                   # BYOK (interactive)
  tokenpak creds add --id openai-work --platform openai \
       --kind api_key --key sk-...
  tokenpak creds remove openai-work
  tokenpak creds test openai-work                      # cheap live probe
  tokenpak creds route api.anthropic.com               # what'd I pick?
  tokenpak creds route api.openai.com --caller my-app:profile:* \
       --tag work

**Flags:**

- `ARGS` — Subcommand + args (list | doctor)

### `tokenpak pak`

MultiPak Pro Phase 1 OSS surface. Read-only Vault Pak operations work without Pro; other Pak subtypes require the tokenpak-paid daemon.

**Subcommands:**

- `inspect`
  - `PAK_REF` — Pak ID (e.g. 'vault:path#hash') or path to a Pak file
  - `--json` — Emit JSON instead of text
- `export`
  - `PAK_REF` — Pak ID to export
  - `--output`, `-o` — Output directory
- `create` — Package a directory into a Pak JSON file. The Pak captures anchor file content, objective/summary metadata, and a sha256 checksum. Encrypted Pak archives + capture pipeline are Pro features; plain JSON Paks are OSS Beta 1.
  - `SOURCE_DIR` — Directory to package
  - `--output`, `-o` — Output Pak file path
  - `--title` — Pak title (default: directory name) (default: )
  - `--objective` — Pak objective (free-form) (default: )
  - `--summary` — Pak summary (free-form) (default: )
  - `--ttl` — Pak TTL hint (free-form, e.g. '7d') (default: )
  - `--continuation-notes` — Notes for continuation (free-form) (default: )
  - `--include-content` — Embed file content in the Pak (default: on; use --no-include-content to omit)
  - `--no-include-content` — Omit file content; only record paths + per-file sha256
  - `--max-bytes` — Skip files larger than this when embedding content (default: 2 MiB) (default: 2000000)
- `import` — Copy a Pak file into the local Pak store under <TOKENPAK_HOME>/paks/ so it is discoverable by `pak inspect <id>`. Pro daemon adds encryption-at-rest + capture pipeline; OSS import is a plain copy with checksum verification.
  - `PAK_FILE` — Path to a Pak file to install
  - `--force` — Overwrite if a Pak with the same id is already installed
- `status`
  - `--json` — Emit JSON instead of text

### `tokenpak test`

Launch an interactive test that auto-detects your available
platforms, providers, and models, then runs a 5-turn A/B
comparison (with vs without tokenpak) with live display.

Run: tokenpak test

### `tokenpak prove`

Run the same multi-turn prompt scenario through direct API and through
tokenpak, then compare metrics side-by-side.

Scenarios are .md files with YAML frontmatter and ## Turn headings.
Create your own at: ~/.tokenpak/prove/scenarios/<name>.md

Examples:
  tokenpak prove run                       # run default scenario
  tokenpak prove run my-scenario            # run custom scenario
  tokenpak prove run default --model gpt-4o # override model
  tokenpak prove list                       # list all scenarios
  tokenpak prove show prf_a1b2c3d4          # show past result
  tokenpak prove create --name my-test      # create new scenario

**Subcommands:**

- `run`
  - `SCENARIO` — Scenario name (default: 'default') (default: default)
  - `--model`, `-m` — Override model from scenario
  - `--provider` — Override provider (anthropic|openai)
  - `--no-live` — Skip launching live display windows
- `list`
- `show`
  - `PROOF_ID` — Proof ID (e.g. prf_a1b2c3d4)
- `create`
  - `--name` — Scenario name
  - `--model` — Model to use (default: claude-sonnet-4-6)
  - `PROMPTS` — Turn prompts (one per positional arg)
- `providers`

---

## Group: Advanced

### `tokenpak trigger`

Manage event triggers

**Subcommands:**

- `list`
  - `--json` — Output raw JSON
- `add`
  - `--event` — Event pattern (e.g. file:changed:*.py, git:commit, cost:daily>5)
  - `--action` — Action: tokenpak sub-command or shell script path
  - `--json` — Output raw JSON
- `remove`
  - `ID` — Trigger ID
  - `--json` — Output raw JSON
- `test`
  - `--event` — Event string to test
  - `--json` — Output raw JSON
- `log`
  - `--limit` — default: 20
  - `--json` — Output raw JSON
- `daemon`
- `fire`
  - `EVENT` — Event string to fire (e.g. git:push, agent:finished:agent-1)
- `hook`
- `watch`
  - `PATHS` — Paths to watch (default: .)

### `tokenpak macro`

Manage macros

**Subcommands:**

- `list`
- `create`
  - `--name` — Macro name (e.g., my-deploy)
  - `--description` — Short description (default: )
  - `--step` — Add a step (repeatable). Format: 'Label:command'
  - `--var` — Default variable (repeatable). Format: KEY=VALUE
  - `--continue-on-error` — Keep running if a step fails (default: fail-fast)
  - `--file` — Load macro definition from a YAML file
  - `--overwrite` — Overwrite an existing macro with the same name
- `run`
  - `NAME` — Macro name
  - `--dry-run` — Print commands without executing them
  - `--continue-on-error` — Keep running if a step fails
  - `--var` — Runtime variable override (repeatable)
  - `--json` — Output raw JSON
- `show`
  - `NAME` — Macro name
  - `--json` — Output raw JSON
- `delete`
  - `NAME` — Macro name
  - `--yes`, `-y` — Skip confirmation prompt
- `install`
  - `NAME` — Macro name (morning-standup, pre-deploy, weekly-report)
- `hooks`

### `tokenpak fingerprint`

Fingerprint management

**Subcommands:**

- `sync`
  - `TEXT` — Prompt text (or omit to read from stdin)
  - `--file`, `-f` — Read prompt from file
  - `--messages` — OpenAI messages JSON file
  - `--dry-run` — Show what would be sent without transmitting
  - `--privacy` — default: standard — choices: `minimal`, `standard`, `full`
  - `--ttl` — Cache TTL in seconds (default 3600) (default: 3600)
  - `--skip-cache`
  - `--json`
- `cache`
  - `--json`
- `clear-cache`
  - `--id` — Clear only this fingerprint ID (default: all)
  - `--yes`, `-y` — Skip confirmation prompt

### `tokenpak agent`

Agent coordination

**Subcommands:**

- `lock`
  - `PATH` — File path to lock
  - `--timeout` — Lock TTL in seconds (default 600) (default: 600)
  - `--agent` — Agent id override
- `unlock`
  - `PATH` — File path to unlock
  - `--agent` — Agent id override
- `locks`
  - `--agent` — Filter by agent id
- `list`
  - `--all` — Include stale agents
  - `--json` — JSON output
- `register`
  - `NAME` — Agent name (e.g., agent-1, agent-2)
  - `--hostname` — Hostname (default: auto-detect)
  - `--gpu` — Has GPU
  - `--memory` — Memory in GB (default: 4.0)
  - `--specialties` — Specialties (e.g., code research) (default: [])
  - `--providers` — Provider access (default: ['anthropic'])
  - `--json` — JSON output
- `deregister`
  - `AGENT_ID` — Agent ID to remove
- `heartbeat`
  - `AGENT_ID` — Agent ID
  - `--status` — Update status — choices: `active`, `busy`, `draining`
  - `--task` — Current task name
- `match`
  - `--gpu` — Require GPU
  - `--memory` — Minimum memory GB
  - `--specialty` — Required specialties (default: [])
  - `--provider` — Required providers (default: [])
  - `--json` — JSON output
- `prune`
- `handoff`

### `tokenpak lock`

File lock management

**Subcommands:**

- `claim`
  - `PATH` — File or directory path to lock
  - `--timeout` — Lock TTL in seconds (default 1800 = 30 min) (default: 1800)
  - `--agent` — Agent id override
- `release`
  - `PATH` — File or directory path to release
  - `--agent` — Agent id override
- `query`
  - `PATH` — File or directory path to query
  - `--agent` — Agent id override (for manager context)
- `list`
  - `--agent` — Filter by agent id (display context only)
- `renew`
  - `PATH` — File or directory path to renew
  - `--timeout` — New TTL in seconds (default 1800 = 30 min) (default: 1800)
  - `--agent` — Agent id override

### `tokenpak run`

Schedule macro runs

**Subcommands:**

- `cron`
  - `NAME` — Macro name
  - `--cron` — Cron expression e.g. "0 9 * * 1-5"
  - `--description` — Optional description (default: )
- `at`
  - `NAME` — Macro name
  - `--at` — Time string e.g. "2026-03-06 09:00" or "now + 1 hour"
  - `--description` — Optional description (default: )
- `list`
- `cancel`
  - `ID` — Schedule ID to cancel

### `tokenpak replay`

Replay captured sessions

**Subcommands:**

- `list`
  - `--limit` — Max entries to show (default 20) (default: 20)
  - `--provider` — Filter by provider
- `show`
  - `ID` — Replay entry ID
  - `--messages` — Print captured message content
- `run`
  - `ID` — Replay entry ID
  - `--model` — Label as a different model
  - `--no-compress` — Simulate sending uncompressed
  - `--aggressive` — Apply aggressive compression mode
  - `--diff` — Show unified diff of original vs compressed messages
- `clear`

### `tokenpak audit`

Audit log surface (Pro/Enterprise)

### `tokenpak compliance`

Compliance report surface (Pro/Enterprise)

### `tokenpak validate`

Validate JSON files

**Flags:**

- `FILE` — Path to the .json TokenPak file
- `--verbose`, `-v` — Show quality hints in addition to errors/warnings
- `--json` — Output validation result as JSON

### `tokenpak config-check`

Validate config

**Flags:**

- `FILE` — Path to config file (JSON)

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

### `tokenpak retrieval`

Test search retrieval

**Flags:**

- `--json` — Output as JSON

**Subcommands:**

- `status`
  - `--json` — Output as JSON
- `test`
  - `QUERY` — Query string to test
  - `--top-k` — Number of results (default: 5) (default: 5)
  - `--json` — Output as JSON

---

## Additional Commands

### `tokenpak activate`

**Flags:**

- `KEY` — Your license key (default: )
- `--email` — Optional email for the license (default: )

### `tokenpak check-alerts`

Evaluate alert rules and return exit code 1 if any fired.

### `tokenpak compare`

Show before/after cost comparison for last N requests.

**Flags:**

- `--last` — Show last N requests (default: 1)

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
- `--tier` — Filter to a specific tier: free|pro|team|enterprise

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

### `tokenpak last`

Display details about the most recent request processed by the proxy.
Includes compression ratio, token savings, latency, and provider info.

Example:
  tokenpak last                    # Show last request
  tokenpak last --json             # Export as JSON
  tokenpak last --limit 5          # Show last 5 requests

**Flags:**

- `--limit` — Show last N requests (default: 1)
- `--json` — Output as JSON
- `--verbose`, `-v` — Show full request/response bodies

### `tokenpak leaderboard`

Show per-model efficiency ranking.

**Flags:**

- `--days` — Rolling window in days (default: today)

### `tokenpak license`

**Flags:**

- `--json` — Machine-readable JSON output

### `tokenpak menu`

### `tokenpak monitor`

Start the live monitor dashboard.

**Flags:**

- `--port` — Dashboard port (default: 8767) (default: 8767)


### `tokenpak optimize`

Analyze and optimize a prompt for better Prompt Packing efficiency.
Suggests rewording and restructuring to reduce compressed token count.

Example:
  tokenpak optimize < myprompt.txt
  tokenpak optimize --strategy aggressive myfile.txt

**Flags:**

- `--file`, `-f` — Input file path (reads from stdin if omitted)
- `--strategy` — Optimization aggressiveness (default: balanced) (default: balanced) — choices: `conservative`, `balanced`, `aggressive`
- `--show-diff` — Show before/after token counts
- `--json` — Machine-readable JSON output

### `tokenpak pakplan`

Read-only consumer surface over the PAKPlan recall foundation. Scoring + capture pipeline are Pro.

**Subcommands:**

- `preview`
  - `--limit` — Max Paks to surface (default: 10) (default: 10)
  - `--json` — Emit JSON
- `explain`
  - `PAK_ID` — Pak id (e.g. pak:abcd1234…)
  - `--json` — Emit JSON
- `report`
  - `--json` — Emit JSON

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

### `tokenpak prune`

Remove low-priority blocks from the compression store.
Blocks below the quality threshold are listed and optionally deleted.

Example:
  tokenpak prune                     # interactive review
  tokenpak prune --dry-run           # preview without changes
  tokenpak prune --auto              # prune without confirmation
  tokenpak prune --threshold 0.3     # custom quality threshold

**Flags:**

- `--auto` — Auto-prune without confirmation
- `--dry-run` — Show what would be pruned (no changes made)
- `--threshold` — Quality score below which blocks are pruned (default: 0.4) (default: 0.4)
- `--json` — Output raw JSON

### `tokenpak report`

Generate and display daily savings report.

**Flags:**

- `--markdown` — Output markdown format (for messaging)
- `--json` — Output JSON format

### `tokenpak savings`

Show compression savings summary.

**Flags:**

- `--days` — Rolling window in days (default: 30)

### `tokenpak telemetry`

**Subcommands:**

- `export`
  - `--format` — Output format (default: json) (default: json) — choices: `json`, `csv`
  - `--since` — Only include events on or after this date
  - `--until` — Only include events on or before this date
  - `--provider` — Filter to a specific provider name

### `tokenpak tip`

TIP is the protocol layer that adapter providers and platform integrations declare against. This verb family exposes the OSS-side validation, inspection, and self-conformance surface.

**Subcommands:**

- `inspect`
  - `--json` — Emit JSON instead of text
- `validate`
  - `REF` — Either a capability label (e.g. 'tip.compression.v1') or a filesystem path to a JSON document to check
  - `--schema` — Schema name (e.g. 'tip-capabilities.v1') when validating a JSON file. Required for file mode.
  - `--json` — Emit JSON result
- `conformance`
  - `--json` — Emit JSON result envelope
- `doctor`
  - `--json` — Emit JSON result envelope
- `scaffold-adapter`
  - `NAME` — Adapter name (e.g. 'my-platform')
  - `--output`, `-o` — Output file path (default: ./<name>_adapter.py)

### `tokenpak usage`

Show model token usage summary.

**Flags:**

- `--days` — Rolling window in days (default: 30)

### `tokenpak validate-config`

CLI wrapper for tokenpak validate-config.

**Flags:**

- `FILE` — Path to config file (YAML or JSON)

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

### `tokenpak watch`

---

