---
title: TokenPak Standards
type: index
status: draft
---

# TokenPak Standards

This directory is the canonical source of truth for how TokenPak should look, feel, behave, and be written about. If the product contradicts a document here, the product is what's wrong — not the standard.

Standards are ordered deliberately. Earlier documents set the constitutional frame; later documents refine specific surfaces.

| # | Document | What it governs |
|---|---|---|
| 00 | [Product Constitution](00-product-constitution.md) | Identity, principles, quality bar. Everything else descends from this. |
| 01 | [Architecture Standard](01-architecture-standard.md) | Module boundaries, dependency direction, registration rules. |
| 02 | [Code Standard](02-code-standard.md) | Naming, typing, logging, errors, tests. |
| 03 | [CLI/UX Standard](03-cli-ux-standard.md) | `tokenpak <verb>` shape, exit codes, output format, prompts. |
| 04 | [Dashboard UI Standard](04-dashboard-ui-standard.md) | Local dashboard layout, information density, signals. |
| 05 | [Brand Style Guide](05-brand-style-guide.md) | Logo, color, typography, emoji/icon rules, tone across surfaces. |
| 06 | [Docs Style Guide](06-docs-style-guide.md) | Structure, voice, audience assumptions, example format. |
| 08 | [Naming Glossary](08-naming-glossary.md) | Approved meaning for proxy, companion, compression, compaction, cache, savings, protected tokens, etc. |
| 09 | [Audit Rubric](09-audit-rubric.md) | What the auditor checks, how it scores, severity definitions. |
| 10 | [Release Quality Bar](10-release-quality-bar.md) | The gates a release must pass. |

## Templates

Canonical examples live in [`templates/`](templates/). Follow the template, then deviate only with justification.

| Template | Use when |
|---|---|
| [feature-template.md](templates/feature-template.md) | Adding a new feature (proposal → spec → ship) |
| [module-template.md](templates/module-template.md) | Creating a new Python subpackage under `tokenpak/` |
| [readme-template.md](templates/readme-template.md) | New top-level README (package, example, satellite repo) |
| [quickstart-template.md](templates/quickstart-template.md) | New "get from zero to working in 5 min" doc |
| [troubleshooting-template.md](templates/troubleshooting-template.md) | New troubleshooting page |
| [release-notes-template.md](templates/release-notes-template.md) | Every tagged release |

## How to use this directory

**Writing code or docs:** Read the relevant standard first. If the standard doesn't cover your case, that's a gap — file a PR updating the standard before or alongside your work, not after.

**Reviewing a PR:** Cite the standard by number. "02-code-standard.md §3 — logger name must match module path."

**Running an audit:** [09-audit-rubric.md](09-audit-rubric.md) is the agent's prompt. The agent evaluates current TokenPak against 00–08 and produces a scored report.

**Shipping a release:** [10-release-quality-bar.md](10-release-quality-bar.md) lists the gates. All must pass.

## Status

All documents in this directory are currently `draft`. They codify current best thinking; expect edits as TokenPak matures. Breaking changes to standards require a PR titled `standards: ...` and reviewer sign-off from Kevin.
