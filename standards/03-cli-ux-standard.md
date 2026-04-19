---
title: TokenPak CLI/UX Standard
type: standard
status: draft
depends_on: [00-product-constitution.md]
---

# TokenPak CLI / UX Standard

The `tokenpak` command is the primary surface of the product. This document governs its shape.

---

## 1. Command Grammar

`tokenpak <verb> [<noun>] [<flags>]`

- Verbs are **imperative and short**: `serve`, `integrate`, `cost`, `savings`, `demo`, `doctor`, `status`, `creds`, `cache`.
- Nouns, when used, are **singular and lowercase**: `tokenpak integrate claude-code`, `tokenpak creds list`.
- **Never noun-noun**. Prefer `tokenpak cache clear` over `tokenpak cache-admin clear`.
- Subcommands beyond one level of nesting are a smell. Two levels max.

**Canonical top-level commands:**

| Command | Purpose |
|---|---|
| `tokenpak serve` | Start the proxy at `127.0.0.1:8766`. |
| `tokenpak integrate <client>` | Wire a client to the proxy. `--apply` to write, `--dry-run` default. |
| `tokenpak cost` | Show spending per model/session/agent from `monitor.db`. |
| `tokenpak savings` | Show compression and cache savings with a `cache_origin` breakdown. |
| `tokenpak status` | One-screen health: proxy up, creds OK, last request, recent errors. |
| `tokenpak doctor` | Diagnostic — checks creds, proxy reachability, monitor DB integrity. |
| `tokenpak creds {list,doctor,add,remove}` | Credential router operations. |
| `tokenpak cache {stats,clear}` | TokenPak cache (not provider cache) operations. |
| `tokenpak demo` | Run a live compression demo against a built-in scenario. |
| `tokenpak index <path>` | Semantic-index a directory. Feeds companion memory. |
| `tokenpak replay <request-id>` | Re-run a past request with identical inputs. |

Adding a new top-level verb requires a PR note justifying why an existing verb doesn't fit.

## 2. Flags

- **Global flags** (accepted by every command): `--help`, `--version`, `--config <path>`, `--verbose`, `--quiet`, `--json`.
- **Long form is canonical.** Short forms only for `-h` (help), `-v` (verbose), `-q` (quiet), `-c` (config).
- **Boolean flags** are `--flag` / `--no-flag`, never `--flag=true`.
- **File paths** use `<path>` in help text, never `<file>`, `<dir>`, or `<filename>`.
- **Destructive operations** default to dry-run. `--apply`, `--yes`, or `--force` to execute.

## 3. Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success, including "no-op with nothing to report" |
| 1 | User-facing error (bad input, missing creds, network failure) |
| 2 | Usage error (wrong args, unknown verb) |
| 3 | Degraded — proxy ran but some requests failed |
| 4 | Config error |
| 5 | Internal error (bug) — also prints the issue tracker URL |

`TokenPakError.exit_code` drives this. The CLI entry point catches it and exits; uncaught exceptions map to 5 and print a stack trace only if `--verbose`.

## 4. Output Format

### 4.1 Human mode (default)

- **Panels for summaries**, plain lines for streaming output. Panels use the canonical box shape from the README demo:

  ```
  ┌──────────────────────────────────────────────────────┐
  │  TokenPak — <title>                                  │
  ├──────────────────────────────────────────────────────┤
  │  Label                        Value                  │
  ├──────────────────────────────────────────────────────┤
  │  Total                        <bold number>          │
  └──────────────────────────────────────────────────────┘
  ```

- **Numbers:** right-align integers and dollar values in panels. Percentages inline.
- **Colors:** reserved for state.
  - Green = success, savings, healthy.
  - Yellow = degraded, deprecated, approaching threshold.
  - Red = error, failed, over-budget.
  - Cyan = info / currently-happening.
  - Default gray = neutral labels.
  - No decorative color. If it doesn't carry state, it's gray.
- **Emoji:** sparingly, and only in status lines.
  - ✅ success
  - ⚠️ warning
  - ❌ error
  - 📦 package/install context
  - 🚀 first-run / demo only
  - No others. No per-command mascots.

### 4.2 `--json` mode

Every command that emits a summary must support `--json`. The schema is stable within a major version; fields only get added, never renamed or removed.

```bash
$ tokenpak savings --json
{
  "total_saved_tokens": 14920,
  "total_saved_usd": 0.0443,
  "by_origin": {"proxy": 8412, "client": 6508, "unknown": 0},
  "window": {"start": "2026-04-17T00:00:00Z", "end": "2026-04-18T20:30:00Z"}
}
```

### 4.3 `--quiet`

Prints only the primary result (one number, one line) or nothing. Useful in scripts. Exit code is still meaningful.

## 5. Prompts and Confirmation

- **Destructive action?** Default to dry-run + confirmation. Example:
  ```
  $ tokenpak integrate claude-code
  Planned changes:
    - ~/.claude/settings.json: add env.ANTHROPIC_BASE_URL
  Re-run with --apply to write these changes.
  ```
- **Interactive prompts** accept `y`/`n`/`yes`/`no`, case-insensitive. Default shown in brackets: `[Y/n]`.
- **Never prompt in non-interactive mode** (stdin not a TTY, or `TOKENPAK_NONINTERACTIVE=1`). Bail with exit code 1 and a message naming the flag to pass.
- **Never prompt from within `serve`**. A running proxy must not block on input.

## 6. Error Messages

Every user-facing error names three things: **what failed**, **why**, **what to do next**.

```
✗ tokenpak serve — port 8766 already in use.

  Another process is bound to 127.0.0.1:8766.
  Run: lsof -i :8766   (or)   tokenpak doctor --port 8766

  To use a different port: tokenpak serve --port 8767
```

- Lead with `✗` and the command that failed.
- Cause statement is one sentence.
- Next-step commands are copy-pasteable.
- Doc link only if there's a relevant troubleshooting entry.

## 7. `--help` Text

- One-line summary at the top, matching the table in §1.
- Usage line.
- One-line description per flag.
- One example, real, that works.

Help text is documentation. It gets reviewed like docs.

## 8. Progress Output

- **Long operations** (> 1s expected) show a spinner with a present-continuous verb: "Compressing...", "Scanning...", "Uploading...".
- **Indeterminate operations** get a spinner; **determinate operations** get a progress bar.
- **No progress output in `--quiet` or `--json` modes.**
- Progress never overwrites error messages. Errors break the line first.

## 9. First-Run Experience

`tokenpak` with no args or `tokenpak --help` on first run shows the 3-line quickstart from the README. The quickstart is generated from the README, not duplicated — a script regenerates it at release time.

## 10. Backwards Compatibility

- Deprecated verbs print a warning and still work for one minor version.
- Renamed flags keep the old name as an alias for one minor version.
- **Never silently change behavior.** If a default changes, the old default is preserved for one version with a warning.
