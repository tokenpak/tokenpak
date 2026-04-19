---
title: TokenPak Release Quality Bar
type: standard
status: draft
depends_on: [00-product-constitution.md, 09-audit-rubric.md]
---

# TokenPak Release Quality Bar

Every TokenPak release — major, minor, or patch — must clear every gate in this document. If any gate fails, the release does not ship.

"Release" means: a version tagged in git or published to PyPI. Local / development builds are not subject to all gates but must pass A1–A3.

> **Companion documents:** this doc defines *what* must pass. [`11-release-workflow-overview.md`](11-release-workflow-overview.md) and 12–19 define *how* to run the release such that the gates pass.

---

## 1. Gate Categories

- **A. Technical gates** — the product works.
- **B. Consistency gates** — the audit passes.
- **C. Documentation gates** — docs describe what shipped.
- **D. Messaging gates** — the story is straight.
- **E. Operational gates** — we can tell if this release breaks in the wild.

## 2. Gates

### A. Technical

- [ ] **A1. Test suite green.** `make check` (lint + format + tests) passes on CI for the release commit. No `xfail` additions in this release without a linked issue.
- [ ] **A2. Fast suite under 60s.** `make test` runs in under 60 seconds on the reference machine.
- [ ] **A3. `mypy --strict`** passes on `tokenpak/core`, `tokenpak/proxy`, `tokenpak/creds`, `tokenpak/compression`, `tokenpak/cache`.
- [ ] **A4. Architecture boundaries respected.** `import-linter` config passes. No new circular imports.
- [ ] **A5. Fresh-machine install works.** A clean container / VM gets from `pip install tokenpak` to a working `tokenpak demo` in under 60 seconds.
- [ ] **A6. Hot-path performance.** `make bench` shows no >5% regression on the Claude Code passthrough scenario.
- [ ] **A7. Byte-fidelity preserved.** The dedicated byte-fidelity test (Constitution §5.2) passes.

### B. Consistency

- [ ] **B1. Automated audit clean.** `make audit` produces no Critical or High findings not already documented in `09-audit-rubric.md §6` as known and accepted.
- [ ] **B2. Agent audit reviewed.** The audit agent's run on the release candidate has been read; any new High or Critical findings are resolved or consciously accepted in the release notes.
- [ ] **B3. No marketing filler.** Grep on `{README.md, docs/**, site/**, tokenpak/dashboard/**}` finds no uses of: "revolutionary," "game-changing," "cutting-edge," "industry-leading," "next-gen," "best-in-class," "simply," "just" (as a qualifier), "easily." Per Constitution §8.
- [ ] **B4. Naming clean.** No retired terms from `08-naming-glossary.md` in shipped code or docs.

### C. Documentation

- [ ] **C1. README reflects shipped features.** The "What's included" section matches the actual 1.x feature set.
- [ ] **C2. Quickstart runs.** Every command in `docs/quickstart.md` executes successfully against the release candidate.
- [ ] **C3. API reference up to date.** Public SDK functions with docstring changes are reflected in `docs/api-tpk-v1.md`.
- [ ] **C4. Troubleshooting covers first-hour errors.** Every error the user can trigger during install + `tokenpak demo` has a troubleshooting entry.
- [ ] **C5. Links resolve.** `make docs-check` passes. No broken links in shipped docs.
- [ ] **C6. No forbidden patterns.** No `TODO`, no "coming soon," no stale `updated` dates in docs touched by this release.
- [ ] **C7. CHANGELOG updated.** Human-readable summary of what changed, using the format from `templates/release-notes-template.md`.

### D. Messaging

- [ ] **D1. The identity story holds.** README, site, dashboard, and any pitch material all agree on: local proxy for LLM context compression; 30–50% savings on typical agent workloads; Apache 2.0 licensed.
- [ ] **D2. Claims defensible.** Every new numeric claim cites the scenario that produced it or is labeled workload-dependent.

### E. Operational

- [ ] **E1. Monitor schema compatible.** Any `monitor.db` schema change ships with a migration; upgrading from the previous release does not break existing user data.
- [ ] **E2. Config compatibility.** Any config key change keeps the old key as a deprecated alias for one minor version, with a warning.
- [ ] **E3. CLI compatibility.** Any CLI flag or verb rename keeps the old form as an alias for one minor version.
- [ ] **E4. Telemetry opt-in preserved.** If telemetry is touched, it remains off by default and the disclosure in the README is current.
- [ ] **E5. Rollback plan.** The release notes include the exact `pip install tokenpak==<previous>` command and any `~/.tokenpak/` cleanup needed to revert.
- [ ] **E6. Integration smoke.** The release candidate passes the reference scenarios in `examples/` against at least two different clients (e.g., Claude Code + Cursor, or Claude Code + the OpenAI SDK). No proxy or creds errors introduced.

## 3. Gate Exceptions

A gate can be **consciously waived** for a release only if:

1. The waiver appears in the release PR description with a paragraph explaining the decision.
2. The waived finding is added to `09-audit-rubric.md §6` (Known Current Findings) in the same PR.
3. Kevin signs off.

A waiver is not a free pass. It creates a tracked debt; the next release either resolves it or re-justifies it.

## 4. Release Types

| Type | Gates required | Notes |
|---|---|---|
| Patch (1.4.1 → 1.4.2) | A1–A4, A7, B1, B3, B4, C2, C5, C7, E1, E3, E6 | No D gates unless messaging touched. |
| Minor (1.4.2 → 1.5.0) | All A, B, C gates; D1; E1, E2, E3, E5, E6 | D2 if new numeric claims. |
| Major (1.x → 2.0) | **All gates.** | Includes agent audit (B2) and human-reviewed messaging walkthrough. |

## 5. Release Process

1. **Cut release branch** from `main`.
2. **Run `make release-check`** — automates A1–A7, B1, B3, C5, C6.
3. **Run agent audit** (B2) on the branch.
4. **Hand-verify C1, C2, C4, D1** — these require reader judgment.
5. **Fill the release notes** from `templates/release-notes-template.md`.
6. **Integration smoke (E6):** run the `examples/` scenarios against at least two different clients; confirm no regressions.
7. **Gate review:** go down this checklist. Sign off or waive each.
8. **Tag and publish.**
9. **Post-release:** monitor `monitor.db` error rate for 24 h; any regression triggers a rollback decision.

## 6. Post-Release Invariants

These must hold true at all times after shipping:

- Previous versions remain installable from PyPI (no yanks except security).
- Rollback path in the release notes still works.
- Docs for the previous release stay available until the release after next ships.

## 7. Release Cadence

No fixed cadence. TokenPak ships when a coherent slice of value passes all gates. "Coherent slice" is judged per-release, not measured in story points.

Unshipped work is not a failure; shipping broken work is.
