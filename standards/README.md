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

### Release workflow (11–19)

Descend from 10. Together they describe how a TokenPak release actually gets shipped.

| # | Document | What it governs |
|---|---|---|
| 11 | [Release Workflow Overview](11-release-workflow-overview.md) | The master document. Pipeline, roles, go/no-go rules, links to runbooks. |
| 12 | [Environments and Promotion Rules](12-environments-and-promotion-rules.md) | Local → dev → RC → PyPI, plus containers. Promotion criteria and drift policy. |
| 13 | [Staging Validation Checklist](13-staging-validation-checklist.md) | The evidence-backed gate between RC and production. |
| 14 | [Production Deployment Runbook](14-production-deployment-runbook.md) | Step-by-step SOP for tag → PyPI → GitHub release. |
| 15 | [Post-Deploy Validation](15-post-deploy-validation.md) | Is the release actually healthy in production? |
| 16 | [Rollback and Recovery Runbook](16-rollback-and-recovery-runbook.md) | Supersede vs yank; decision authority; exact commands. |
| 17 | [Hotfix Workflow](17-hotfix-workflow.md) | The shortcut path for urgent fixes, and what it doesn't let you skip. |
| 18 | [Release Communication Template](18-release-communication-template.md) | Pre-release, success, rollback, escalation, summary. |
| 19 | [Release Log Template](19-release-log-template.md) | The audit trail, one file per release, kept forever. |

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

**Shipping a release:** start at [11-release-workflow-overview.md](11-release-workflow-overview.md); it links to [10](10-release-quality-bar.md) for the gates and 12–19 for the runbooks.

## Status

All documents in this directory are currently `draft`. They codify current best thinking; expect edits as TokenPak matures. Breaking changes to standards require a PR titled `standards: ...` and reviewer sign-off from Kevin.
