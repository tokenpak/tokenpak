# tokenpak-claude-code plugin

A Claude Code plugin that adds corpus-aware context packing, skills, and safety hooks for [tokenpak](https://github.com/tokenpak/tokenpak) users. See the upstream docs at `tokenpak/docs/claude-code-plugin.md` for the full quickstart and reference.

---

## Before you install

### Required: `ENABLE_TOOL_SEARCH=true`

When `ANTHROPIC_BASE_URL` points at a non-Anthropic gateway (such as the tokenpak proxy on
`http://localhost:8766`), Claude Code silently drops MCP tool-use requests **unless**
`ENABLE_TOOL_SEARCH=true` is set in the environment where Claude Code is launched.

Set it once in your shell profile so every session picks it up automatically:

**bash** (`~/.bashrc` or `~/.bash_profile`):
```bash
export ENABLE_TOOL_SEARCH=true
export ANTHROPIC_BASE_URL=http://localhost:8766
```

**zsh** (`~/.zshrc`):
```zsh
export ENABLE_TOOL_SEARCH=true
export ANTHROPIC_BASE_URL=http://localhost:8766
```

**fish** (`~/.config/fish/config.fish`):
```fish
set -gx ENABLE_TOOL_SEARCH true
set -gx ANTHROPIC_BASE_URL http://localhost:8766
```

**PowerShell (Windows — best-effort):** add to your `$PROFILE`:
```powershell
$env:ENABLE_TOOL_SEARCH = "true"
$env:ANTHROPIC_BASE_URL = "http://localhost:8766"
```

After setting these, verify with:

```bash
python3 -m tokenpak doctor --claude-code
```

Expected output when correctly configured:

```
✅  ENABLE_TOOL_SEARCH   true — MCP tool-use enabled on http://localhost:8766
```

### IDE compatibility

| Environment | Plugin loads? | Notes |
|---|---|---|
| Claude Code CLI (terminal) | ✅ Yes | Standard path |
| Claude Code VSCode extension | ✅ Yes | `TERM_PROGRAM=vscode` |
| Cursor | ❌ No | Does not load `--plugin-dir` plugins; use the proxy directly or CCP-23 SDK helpers |
| Windsurf | ❌ No | Same as Cursor |

---

## Hooks

### `protect-paths` (PreToolUse: Edit | Write)

Always-on write guard that blocks edits to sensitive paths before they reach the filesystem.

**Default deny list**

| Pattern | What it protects |
|---|---|
| `.env*` | Environment files (`.env`, `.env.local`, `.env.production`, …) |
| `**/credentials*` | Credential files at any depth |
| `**/migrations/**` | Database migration directories |
| `**/secrets/**` | Secrets directories |
| `.git/**` | Git internals |

When a path matches, Claude Code sees:

```
protected path: <path> (override via .tokenpak-protected in project root)
```

and the edit is blocked (exit code 2).

**Overriding / extending the list**

Create a `.tokenpak-protected` file in your project root. Add one glob pattern per line. Lines starting with `#` and blank lines are ignored.

```
# .tokenpak-protected — project-local protected paths
infra/terraform/**
deploy/secrets/**
custom/**
```

Patterns are matched against the full file path and the file basename using standard shell glob semantics (`*` matches any string including path separators). There is no whitelist mechanism — to allow a default-blocked path, use `--allowedTools` in your Claude Code session settings instead.

---

### `post-edit-validation` (PostToolUse: Edit | Write) — **default OFF**

Runs a per-extension validator on the single edited file after every `Edit` or `Write` tool call. Purely advisory: a validator failure emits a warning to stderr (exit 1) but does **not** block the edit (unlike `protect-paths`, which exits 2).

**Why default-off?** Slow or mis-configured validators destroy IDE responsiveness faster than almost any other integration failure. Enable only when you have tested your validators locally and confirmed they run well under 2 seconds on your typical file sizes.

#### Enabling

Add or update `userConfig.enable_validation_hook` in `~/.claude/settings.json`:

```json
{
  "userConfig": {
    "enable_validation_hook": true
  }
}
```

The hook short-circuits silently (exit 0, no stderr) when this key is absent or `false`.

#### Configured validators (`validators.json`)

The hook reads `plugin/validators.json` to map file extensions to validator commands. The file path is always appended as the final argument.

| Extension | Default command | Requires |
|---|---|---|
| `.py` | `python3 -m py_compile` | Python 3 |
| `.json` | `jq . --` | jq |
| `.yaml` / `.yml` | `python3 -c 'import yaml,sys;...'` | python3 + pyyaml |
| `.sh` | `bash -n` | bash |
| `.md` | `markdownlint` | [markdownlint-cli](https://github.com/igorshubovych/markdownlint-cli) (`npm i -g markdownlint-cli`) |

Extensions with no entry in `validators.json` are silently skipped.

Per-validator timeout defaults to **5 seconds** — this is the hard upper bound, **not** a target. Any validator that routinely takes more than 2 seconds should be removed from synchronous use entirely (see warning list below).

To add or override a validator, edit `validators.json`:

```json
{
  ".ts": {
    "command": "npx tsc --noEmit --skipLibCheck",
    "timeout": 5
  }
}
```

#### Validators to never run synchronously

The following validators are explicitly excluded from synchronous use because they routinely exceed IDE latency thresholds and/or have large startup costs. Running any of these in the hook will make Claude Code feel broken for IDE users (VSCode, JetBrains):

- **Full test suites** (`pytest`, `jest`, `rspec`, etc.) — even with a single file filter, test frameworks load fixtures and may trigger imports
- **Full-repo `eslint`** — parses the entire project graph on every invocation
- **Full-repo `mypy --strict`** — type-checks all imports, not just the changed file
- **`pytest -x` on large test trees** — loading time alone exceeds the budget
- **Anything that touches the network** — linters that resolve remote schemas, dependency fetchers, license checkers
- **Database migration validators** (`alembic check`, `django check --deploy`) — spawn processes and may acquire locks
- **Docker-based validators** — container start time alone disqualifies them

If you need these checks, wire them into your CI pipeline or a pre-commit hook that runs outside the Claude Code session instead.
