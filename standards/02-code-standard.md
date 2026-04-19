---
title: TokenPak Code Standard
type: standard
status: draft
depends_on: [00-product-constitution.md, 01-architecture-standard.md]
---

# TokenPak Code Standard

How Python code inside `tokenpak/` is written. Descends from the Architecture Standard.

---

## 1. Naming

| Kind | Convention | Example |
|---|---|---|
| Modules | `snake_case`, singular nouns for types, verbs for pipelines | `compression.py`, `dedup.py`, `serve.py` |
| Classes | `PascalCase`, nouns | `CompressionPipeline`, `CredsRouter` |
| Functions | `snake_case`, verb-first | `compress_payload`, `resolve_provider` |
| Private | leading `_` | `_normalize_headers`, `_internal.py` |
| Constants | `UPPER_SNAKE_CASE` in module scope only | `DEFAULT_PORT = 8766` |
| Test files | `test_<module>.py` | `test_dedup.py` |
| Test functions | `test_<behavior_under_condition>` | `test_dedup_preserves_cache_control_order` |

Follow the Glossary ([08-naming-glossary.md](08-naming-glossary.md)) for domain terms. Do not invent a new name for an existing concept.

## 2. Typing

- **Type hints required** on all public functions and methods. Private helpers may omit for brevity.
- Use `from __future__ import annotations` at the top of every file; prefer forward-compatible annotation syntax.
- Prefer concrete types (`list[str]`, not `Sequence[str]`) unless a protocol is actually called for.
- `mypy --strict` passes on `tokenpak/core/`, `tokenpak/proxy/`, `tokenpak/creds/`, `tokenpak/compression/`, `tokenpak/cache/`. Other subsystems are permissive; new code defaults to strict unless there's a reason.
- No `# type: ignore` without a comment explaining why. `# type: ignore[attr-defined]` is not self-documenting.

## 3. Errors

- **Exception per concern.** Define one subclass of `Exception` per subsystem in `<subsystem>/errors.py` (e.g., `ProxyError`, `CredsError`). Specialize from there.
- **Errors teach** (Constitution §6). Every `raise` includes: what failed, the observable cause, and the next step.

  ```python
  # bad
  raise ValueError("invalid input")

  # good
  raise CompressionError(
      f"recipe {recipe_name!r} declared stage {stage!r} which is not registered. "
      f"Available stages: {sorted(Stage.registry())}. "
      f"See docs/recipes.md#registering-stages."
  )
  ```
- **Never catch and swallow.** `except Exception: pass` is a code review block. If you're catching broadly, log at `warning` with the exception and context, then re-raise or return a typed result.
- **User-facing errors** (surfaced by CLI) must be wrapped in a `TokenPakError` with `.exit_code` set; the CLI entry point translates these to non-zero exit codes. See §3 of `03-cli-ux-standard.md`.

## 4. Logging

- **One logger per module**: `logger = logging.getLogger(__name__)` at the top. Logger names mirror the import path (`tokenpak.proxy.server`).
- **Levels:**
  - `DEBUG` — internal step-by-step. Off by default. Free to be verbose.
  - `INFO` — lifecycle events the operator cares about: startup, shutdown, config reload, credential rotation.
  - `WARNING` — degraded but continuing. Fallback engaged, retry succeeded, partial cache miss.
  - `ERROR` — a user-facing operation failed. Paired with an exit or a structured error response.
  - `CRITICAL` — the process cannot continue. Almost never used.
- **Structured where possible.** Prefer `logger.info("proxy start", extra={"port": port, "backend": backend})` over f-strings so log aggregators can filter.
- **Never log credentials.** The `creds/` subsystem has a redaction helper; use it.

## 5. Tests

- **Unit tests live in two places.** `tokenpak/<subsystem>/tests/` for co-located fast tests; `tests/<subsystem>/` for cross-subsystem and slow tests.
- **Every public function has at least one test** covering the happy path + one edge case.
- **Bug fixes land with a regression test.** If the fix is one line, the test is required. The test's docstring cites the bug: `"""Regression: cache_control TTL ordering (2026-04-07)."""`.
- **No network in unit tests.** Use `respx` for HTTP mocks, in-memory SQLite for DB tests.
- **Fixtures over setUp.** Pytest fixtures. No `unittest.TestCase` in new code.
- **Fast suite under 60 s.** `make test` runs only fast tests. Slow/integration tests are opt-in via `make test-slow`.

## 6. Comments and Docstrings

Defaults follow from Constitution §10 ("no comments unless the why is non-obvious"):

- **Module docstring** required. One paragraph: what this module owns, one paragraph: any important invariants or hazards.
- **Public function/class docstring** required. Google or NumPy style; pick one and stay consistent (Google for new code).
- **Inline comments** only when the *why* is non-obvious. Never `# increment counter`. Useful comments: known-bug workarounds, cited performance decisions, cross-subsystem invariants.
- **No version comments** (`# added in v1.2`, `# removed in v2.0`). Git has that; leaving it in the code rots.

## 7. Performance-Sensitive Code

The hot path is: HTTP request in → compression → proxy forward → response out. Its budget is **< 50ms** of overhead on a typical agent prompt.

- Functions on the hot path go in `proxy/_fast.py` or `compression/_fast.py`. The `_fast` naming flags to reviewers and profilers.
- No allocation per byte. Compression operates on `bytes` slices, not `str`.
- No JSON re-serialization on passthrough paths (Constitution §5.2). Byte splicing only.
- Bench with `make bench` before merging hot-path changes. Regressions >5% require a written justification in the PR.

## 8. Concurrency

- `asyncio` for I/O-bound code (proxy, HTTP clients).
- Threads for the monitor DB writer only (SQLite + a queue).
- No `multiprocessing` in the core product. If a workload needs it, it belongs in a satellite process.
- No shared mutable state across tasks without a lock. The `creds/` router has a single async lock and owns all refresh decisions.

## 9. Dependencies

- **Core** (`tokenpak` on PyPI) must install with zero external dependencies beyond the standard library + `httpx`. That's the Constitution's "no external dependencies for core functionality" claim, and it's load-bearing.
- **Extras** (`tokenpak[dev]`, `tokenpak[dashboard]`, `tokenpak[codex]`) may pull additional packages.
- New runtime deps require a PR note explaining what they do, why stdlib won't work, and what happens when they're missing.
- Pin upper bounds for anything that has ever broken on a minor release.

## 10. Formatting and Lint

- `ruff` for lint and format. Config in `pyproject.toml`. `make check` is the source of truth.
- Line length 100.
- No manual formatting. Don't fight the formatter.

## 11. Commit Hygiene

- Commit and push after work completes. Uncommitted edits are unsafe when multiple contributors share a checkout.
- One logical change per commit. "fix typo + refactor module" is two commits.
- Commit messages: imperative mood, subject ≤72 chars, body wraps at 80.
- No version numbers in filenames. `git tag` handles versioning.
- Never `git push --force` to `main` or shared branches.
