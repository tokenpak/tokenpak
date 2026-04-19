---
title: TokenPak Staging Validation Checklist
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 12-environments-and-promotion-rules.md, 10-release-quality-bar.md]
---

# TokenPak Staging Validation Checklist

The gate between staging/RC and production. If any check fails, the release does not promote.

Every box carries an **owner**, a **pass/fail**, and **evidence** — a log URL, a command output pasted into the release log, or a short note. "Looks fine" is not evidence.

Copy this checklist into the release log entry (19) for each release. Do not check boxes in this file directly.

---

## Release under test

- **Version:** `vX.Y.ZrcN`
- **Commit SHA:** `<40-char SHA>`
- **Test PyPI URL:** `https://test.pypi.org/project/tokenpak/X.Y.ZrcN/`
- **Owner:** `<release owner>`
- **Staging started:** `<UTC timestamp>`

---

## 1. Build / install validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 1.1 | `python -m build` produced both sdist and wheel on the tagged commit. | | | |
| 1.2 | Wheel filename matches `tokenpak-X.Y.ZrcN-*.whl`. | | | |
| 1.3 | `twine check dist/*` passes with no warnings. | | | |
| 1.4 | Test PyPI page renders (title, version, description, classifiers). | | | |
| 1.5 | `pip install -i https://test.pypi.org/simple/ tokenpak==X.Y.ZrcN` on a clean venv completes without errors. | | | |
| 1.6 | Installed version matches the tag (`tokenpak --version`). | | | |

## 2. Startup validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 2.1 | `tokenpak --help` lists every top-level verb from `03-cli-ux-standard.md §1`. | | | |
| 2.2 | `tokenpak serve` binds `127.0.0.1:8766` cleanly and logs the expected startup lines. | | | |
| 2.3 | `tokenpak serve` emits no `WARNING` or `ERROR` lines on boot. | | | |
| 2.4 | `tokenpak serve --port 8767` uses the alternate port (no hardcoding). | | | |
| 2.5 | Shutting down with SIGTERM exits cleanly within 5s. | | | |
| 2.6 | `tokenpak doctor` returns exit code 0 on a fresh install. | | | |

## 3. Config / env validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 3.1 | Default config loads from `tokenpak/config/defaults.yaml` (no user config present). | | | |
| 3.2 | User config `~/.tokenpak/config.yaml` overrides defaults when present. | | | |
| 3.3 | Project config `./tokenpak.yaml` overrides user config. | | | |
| 3.4 | `TOKENPAK_*` env vars override all file-based config. | | | |
| 3.5 | CLI flags override env vars. | | | |
| 3.6 | No TokenPak code path reads config from any path not listed in `01-architecture-standard.md §6`. | | | |

## 4. Core flow validation — proxy

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 4.1 | A request forwarded through the proxy reaches the provider and returns a response matching what the provider would have returned without TokenPak. | | | |
| 4.2 | Byte-fidelity preserved on the Claude Code passthrough path: request body bytes into the proxy equal request body bytes to the provider (Constitution §5.2). | | | |
| 4.3 | Provider response bytes returned unchanged to the client. | | | |
| 4.4 | A request for an unknown provider produces a typed error with a next-step message (`02-code-standard.md §3`). | | | |

## 5. Compression / cache / routing validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 5.1 | `tokenpak demo` runs and produces a savings panel with a non-zero token reduction. | | | |
| 5.2 | Compression stages fire in the canonical order (dedup → alias → segmentize → directives). | | | |
| 5.3 | A second identical request within the cache TTL hits the TokenPak cache. | | | |
| 5.4 | A cache hit reports `cache_origin=proxy` in the monitor DB. | | | |
| 5.5 | A provider cache hit (e.g., Anthropic `cache_control`) reports `cache_origin=client`. | | | |
| 5.6 | Ambiguous cache evidence reports `cache_origin=unknown` — never inferred optimistically. | | | |
| 5.7 | Routing rules resolve in the documented order; fallback engages on a simulated provider error. | | | |
| 5.8 | Streaming responses pass through with no buffering that would break clients. | | | |

## 6. CLI / dashboard validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 6.1 | Every command in `03-cli-ux-standard.md §1` responds to `--help` with accurate text. | | | |
| 6.2 | `--json` on every summary command returns parseable JSON with stable schema. | | | |
| 6.3 | Exit codes match `03 §3` for success, user error, usage error, config error. | | | |
| 6.4 | Destructive commands default to dry-run (03 §5). | | | |
| 6.5 | Dashboard loads in under 500ms on the validation machine (`04 §11`). | | | |
| 6.6 | Dashboard landing page order matches `04 §2`. | | | |
| 6.7 | Empty-state copy matches `04 §10` when no requests have been recorded. | | | |

## 7. Telemetry / logging validation

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 7.1 | Every proxied request writes one row to `monitor.db` with a non-NULL `cache_origin`. | | | |
| 7.2 | `tokenpak savings --json` numbers match the monitor DB rows for the validation window. | | | |
| 7.3 | Log levels match `02-code-standard.md §4`; no DEBUG by default. | | | |
| 7.4 | No credential material appears in any log line (grep the log file for known fragments of the validation creds). | | | |
| 7.5 | Opt-in telemetry is off by default; enabling it sends traffic only to the documented endpoint. | | | |

## 8. Demo path validation

The demo path is the user's first-impression contract. A broken demo blocks release regardless of other passes.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 8.1 | `pip install tokenpak==X.Y.ZrcN` → `tokenpak serve` → `tokenpak integrate claude-code --apply` → `tokenpak demo` completes in under 60 seconds on a clean machine. | | | |
| 8.2 | The demo panel shows token savings matching the README's published example within ±5%. | | | |
| 8.3 | A user following only `docs/quickstart.md` (no oral coaching) reaches a savings panel. | | | |

## 9. Upgrade-path validation

For minor or major releases only. Patches may skip if no user-facing surface changed.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 9.1 | `pip install --upgrade tokenpak` from the previous release succeeds. | | | |
| 9.2 | Existing `monitor.db` from the previous release opens without corruption; schema migration runs if present. | | | |
| 9.3 | Existing `~/.tokenpak/config.yaml` from the previous release is read without error; any deprecated keys emit a warning naming the replacement. | | | |
| 9.4 | Existing integrations (`~/.claude/settings.json`, etc.) still work without re-running `tokenpak integrate`. | | | |

## 10. Regression checklist

Run the full fast test suite and the reference benchmark against the installed `rc` build, not the checkout.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 10.1 | `pytest` fast suite passes against the installed package. | | | |
| 10.2 | Benchmarks within ±5% of the prior release on the Claude Code passthrough scenario. | | | |
| 10.3 | `mypy --strict` passes on required subsystems (`02 §2`). | | | |
| 10.4 | No new bandit High/Medium findings vs `bandit-baseline.json`. | | | |

## 11. Known issues review

Every open item in the `09-audit-rubric.md` findings log is either:

- [ ] Not regressed (still the same severity it was before this release), OR
- [ ] Resolved in this release (linked to its closing commit), OR
- [ ] Consciously waived with a paragraph in the release log.

## 12. Sign-off

| Role | Name | Timestamp | Notes |
|---|---|---|---|
| Release owner | | | |
| Reviewer | | | |
| Rollback decider | | | |

Once all three are signed, the go / no-go (11 §7) is answerable.

---

## Using this checklist

- **Copy — don't edit.** Paste a fresh copy into the release log (19) for each release.
- **Evidence beats opinion.** If a check can't be backed by a log or a URL, the check doesn't count.
- **Failures don't erase.** A failed check becomes an entry in the release log with remediation. Re-running the check after a fix re-opens the row rather than overwrites history.
- **"Not applicable" is valid.** Write N/A with a one-sentence reason; never leave blank.
