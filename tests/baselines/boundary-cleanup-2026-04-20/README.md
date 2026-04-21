# Boundary cleanup green snapshot (2026-04-20)

`cleanup-log.txt` captures the `check-public-internal-boundary.sh --tracked-only` output at the completion of initiative `2026-04-20-tokenpak-boundary-leak-cleanup` (BLC-04).

## Starting state (2026-04-20 fleet-continuation)
- 28 leaks (20 vault-dir-ref + 8 personal-home-dir) — down from 58 reported 2026-04-08

## Final state
- 0 leaks (exit 0)

## How we got there
- **BLC-02** (5 fixes): env-var defaults for runtime-code leaks (VAULT_ENTRIES_DIR, teacher source roots, telemetry script path, loader example)
- **BLC-03** (8 fixes): generic placeholders for doc/docstring examples
- **BLC-04** (1 fix + script updates): rbac_core provenance reword + boundary-check script now:
  - Excludes `tests/baselines/**` (frozen pytest/conformance captures aren't shippable-code leaks)
  - False-positive-filters `tokenpak/(agent/)?vault/` substrings in generated configs (bandit baseline + pyproject linter allowlists)

## Re-running

    bash ~/vault/06_RUNTIME/scripts/check-public-internal-boundary.sh /home/sue/tokenpak --tracked-only

Expected: exit 0, clean message. If non-zero: new leak introduced; check `DECISIONS-BLC.md` patterns + fix before merge.

## Initiative 5 unblock

This snapshot being exit 0 is the evidence that satisfies **Initiative 5 R110-01** pre-flight gate. When the v1.1.0 release cuts, pre-flight re-runs the same check; should stay green as long as no new leak drifts in.
