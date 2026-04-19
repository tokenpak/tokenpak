---
title: TokenPak Consistency Audit Rubric
type: standard
status: draft
depends_on: [00-product-constitution.md, 01-architecture-standard.md, 02-code-standard.md, 03-cli-ux-standard.md, 04-dashboard-ui-standard.md, 05-brand-style-guide.md, 06-docs-style-guide.md, 08-naming-glossary.md]
---

# TokenPak Consistency Audit Rubric

This rubric is both a checklist for humans and an operable prompt for an audit agent. Run it before every release, and on-demand whenever drift is suspected.

The audit's shift in framing (from `00-product-constitution.md §4`): ask "Does this match the TokenPak standard?" — not "Is this clean?"

---

## 1. Audit Agent Prompt

Use this verbatim as the audit agent's system-prompt body:

> You are the TokenPak consistency auditor. Your job is to evaluate the current state of the TokenPak repository, docs, CLI output, and dashboard against the canonical standards in `/home/sue/tokenpak/standards/`.
>
> Read the Constitution (00), Domain Standards (01–07), and Glossary (08). Then walk each section of this rubric and score the product against it. For each section produce:
>
> 1. **Score** — `pass` / `partial` / `fail`.
> 2. **Evidence** — file paths, line numbers, exact strings showing what you saw.
> 3. **Deviations** — each a one-sentence description of what deviates from the standard, with a pointer to the relevant section of the relevant standard.
> 4. **Severity** — `low` / `medium` / `high` / `critical` using the definitions in §2.
> 5. **Recommended fix** — one sentence, concrete.
>
> Report shape: one section per rubric area, a rollup table at the end, plus a summary of High and Critical findings.
>
> Evaluate correctness, structural consistency, visual consistency, behavioral consistency, and verbal consistency. Flag anything technically functional but structurally, visually, behaviorally, or verbally inconsistent with the TokenPak standard.

## 2. Severity Definitions

| Severity | Definition | Release impact |
|---|---|---|
| **Critical** | Directly contradicts the Constitution's product identity (§2) or non-negotiable principles (§5). Visible to first-time users. | Blocks release. |
| **High** | Violates a domain standard on a user-visible surface. Multiple users will hit it. | Blocks release. |
| **Medium** | Inconsistency on an internal surface, or a user-visible inconsistency rare enough that most users won't see it. | Must be tracked; may ship with a written justification. |
| **Low** | Taste-level, deferred-polish, or "would be cleaner if." | Ships; file an issue. |

## 3. Rubric — Area by Area

Each area below has: **What to check**, **Evidence sources**, **Common deviations**.

### 3.1 Architecture consistency

**Standard:** `01-architecture-standard.md`.

**What to check:**
- Module placement matches §1 (subsystem → concern mapping).
- Imports respect the Level 0–3 dependency direction (§2).
- No hardcoded enumerations of providers, models, adapters, or stages (§4).
- Config loaded from the documented chain only (§6).
- State persistence restricted to the documented stores (§7).

**Evidence sources:**
- `tokenpak/` package tree.
- `import-linter` config and the CI run.
- Grep for `SUPPORTED_`, `ALLOWED_`, `KNOWN_` + provider/model/adapter strings.
- `find . -name "*.db" -o -name "*.sqlite" -o -name "*.pickle"` outside documented paths.

**Common deviations:**
- New top-level directories not added to §1 (today: `agent/`, `agentic/`, `orchestration/` overlap).
- `cli/` imported from a non-CLI subsystem.
- Enumerations for providers/models that should be discovered.
- Test fixtures writing to real `~/.tokenpak/`.

### 3.2 Code consistency

**Standard:** `02-code-standard.md`.

**What to check:**
- Naming matches §1.
- Type hints on public surfaces (§2); `mypy --strict` passes on the required subsystems.
- Errors teach (§3): every user-visible error names cause + next step.
- One logger per module, correct levels (§4).
- Every public function has at least one test (§5).
- No `# type: ignore` without a comment, no `except Exception: pass` (§2, §3).

**Evidence sources:**
- `ruff check` output.
- `mypy --strict tokenpak/core tokenpak/proxy tokenpak/creds tokenpak/compression tokenpak/cache` output.
- Grep for `except Exception:\s*pass`, `# type: ignore` without trailing comment.
- `pytest --collect-only` vs function count.

**Common deviations:**
- Error messages missing next-step guidance.
- Generic `logging.getLogger("tokenpak")` instead of `__name__`.
- Test files in the wrong directory.

### 3.3 Flow consistency

**Standard:** `03-cli-ux-standard.md`, `04-dashboard-ui-standard.md`.

**What to check:**
- Install → first savings visible within 60s (Constitution §11).
- `tokenpak <verb>` grammar is consistent (03 §1).
- Exit codes follow 03 §3.
- `--json` mode supported by every summary command (03 §4.2).
- Destructive commands default to dry-run (03 §5).
- Dashboard landing page matches 04 §2 order.

**Evidence sources:**
- Walk every subcommand in `tokenpak --help`.
- Fresh-machine install test.
- Dashboard screenshot.

**Common deviations:**
- One command emits JSON by default; another needs `--json`.
- Destructive verb without `--apply` gate.
- Dashboard hero metric not savings.
- "Works/doesn't work" readable only from log output, not status.

### 3.4 Brand / visual consistency

**Standard:** `05-brand-style-guide.md`.

**What to check:**
- "TokenPak" spelled correctly everywhere (§1).
- Color rules respected — signal colors for signals only (§5.2).
- Typography system-stack (§6).
- CLI panels use Unicode box characters (§8).
- Dashboard icons from one set only (§9).
- Emoji restricted to the five approved (§11).

**Evidence sources:**
- Grep for variants: `Token Pak`, `token pak`, `TokenPack`, `Tokenpak`.
- CSS files in `site/` and `tokenpak/dashboard/`.
- Dashboard screenshot.
- CLI output snapshots.

**Common deviations:**
- Site CSS hexes don't match the `tp-*` palette.
- Two icon sets in the dashboard.
- Decorative emoji in docs or commit messages.

### 3.5 Documentation consistency

**Standard:** `06-docs-style-guide.md`.

**What to check:**
- Every doc has frontmatter with `rung`, `audience`, `updated` (§2).
- Doc ladder respected — no rung-mixing (§1).
- Examples runnable (§6).
- Glossary terms used correctly (§7).
- Forbidden patterns absent (§14).

**Evidence sources:**
- Grep for files under `docs/` missing frontmatter.
- `make docs-check` link checker.
- Grep for `TODO`, "coming soon," "simply," "just."
- Automated run of every code block in `docs/**/*.md`.

**Common deviations:**
- Date-stamped or version-suffixed filenames (`FOO_2026-03-09.md`, `BAR_v2.md`) violating "no versioned filenames."
- "cache" used unqualified.
- Quickstart that doesn't end at a working demo.
- Stale `updated` dates.

### 3.6 Messaging consistency

**Standard:** `00-product-constitution.md §2` and §8 (tone).

**What to check:**
- Identity statement consistent across README, site, dashboard. Canonical phrasing in Constitution §2.
- Numeric claims (savings %, latency, compatibility lists) match across surfaces.
- Every numeric claim cites its scenario or is labeled workload-dependent.

**Evidence sources:**
- Grep README, `site/`, `docs/`, and dashboard strings for claim variations.
- Cross-check product claims against `monitor.db` / benchmarks.

**Common deviations:**
- README claims 30–50%; another surface claims "up to 90%."
- Different one-sentence product descriptions across surfaces.
- Unlabeled numeric claims that depend on workload.

### 3.7 Naming consistency

**Standard:** `08-naming-glossary.md`.

**What to check:**
- Every domain term used matches the Glossary.
- No retired terms present anywhere (see Glossary "Forbidden Terms").
- External labels used in external copy; internal terms in internal docs/code.
- `cache` never appears unqualified.

**Evidence sources:**
- Grep the entire repo for each retired term.
- Dashboard string extraction.
- CLI `--help` corpus.

**Common deviations:**
- "Memoized" in code comments.
- "Gateway" unqualified in docs.
- "Compaction" used where "compression" is meant.

### 3.8 Telemetry / attribution truth

**Standard:** Constitution §5.3, Glossary `cache_origin`, `savings`, `protected tokens`.

**What to check:**
- Every request row in `monitor.db` has `cache_origin` set.
- Dashboard never shows 0 where data is missing; uses the empty states from 04 §10.
- "Savings" always attributable — no lump sum that mixes compression and cache.
- No claim exceeds measurable reality.

**Evidence sources:**
- SQL: `SELECT cache_origin, COUNT(*) FROM monitor GROUP BY cache_origin;` — `unknown` is legitimate; `NULL` is a bug.
- Dashboard data-binding code.

**Common deviations:**
- Legacy rows with `cache_origin` = NULL.
- Dashboard summing compression + cache into one "savings" number.

## 4. Rollup

Audit output closes with this table:

| Area | Score | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| Architecture | | | | | |
| Code | | | | | |
| Flow | | | | | |
| Brand | | | | | |
| Documentation | | | | | |
| Messaging | | | | | |
| Naming | | | | | |
| Telemetry | | | | | |

Release decision logic: any **Critical** or **High** blocks release (per §2). Any section scoring `fail` blocks release regardless of severity rollup.

## 5. Running the Audit

- **Automated pass:** `make audit`. Runs lint, mypy, link check, forbidden-phrase grep, and the summary SQL queries. ~2 min.
- **Agent pass:** invoke the audit agent with the prompt in §1. ~15 min.
- **Human pass:** optional, for brand/flow areas automated checks can't judge. Use the rollup table as the checklist.

All three passes feed one rollup table. Disagreements between passes are themselves findings.

## 6. Known Findings Log

Accepted findings (ones we know about and decided not to fix immediately) are tracked in a running log, not in this standard. The log pairs with this rubric and is updated after every audit.

The point of the log is that each audit does not re-discover the same findings from scratch. Move a finding to the log when it is genuinely accepted debt with a rationale; leave it out when the expectation is to fix it before the next release.

A finding becomes inactive when a commit or PR closes it. Record that resolution in the log — the rubric itself does not need updating.
