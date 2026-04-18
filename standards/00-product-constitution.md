---
title: TokenPak Product Constitution
type: standard
status: draft
owner: Kevin
---

# TokenPak Product Constitution

This is the short, hard-edged description of what TokenPak is, what it is not, and the non-negotiables that govern every decision.

If a standard, feature, or message contradicts this document, the Constitution wins.

---

## 1. Mission

**Cut LLM token spend by 30–50% for agent workloads, with zero code changes.**

TokenPak is the local layer between a developer's LLM client and the provider. It compresses context before the wire, routes requests intelligently, and tells the user what was saved — without touching their credentials or their code.

## 2. Product Identity

> TokenPak is a **local proxy** that compresses context before it hits the LLM API.

That's the sentence. It goes in the README, the homepage, and every pitch. If a future document (including the existing `ARCHITECTURE.md` "Universal Content Compiler" framing) wants to broaden the identity, it must first resolve this sentence in a Constitution amendment, not diverge silently.

**Core product promise:** install in 30 seconds, see savings in the first call, never modify your code.

## 3. What TokenPak Is

- A **proxy** that sits at `127.0.0.1:8766` between your LLM client and the provider.
- A **companion** that optimizes prompts locally *before* they leave the client (TUI/CLI only).
- A **cost/savings tracker** with a local SQLite monitor database and a dashboard.
- A **client integrator** — one command wires Claude Code, Cursor, Aider, Cline, Continue, Codex, etc.
- A **codebase indexer** for semantic search without burning LLM calls.
- **Open source**, Apache 2.0 licensed.

## 4. What TokenPak Is Not

- **Not a cloud service.** Nothing leaves the user's machine except the LLM requests they were already making.
- **Not a credential vault.** TokenPak routes; it does not store tokens it didn't discover locally.
- **Not a chat UI.** Interaction happens through the user's existing LLM client.
- **Not a model.** No inference runs in TokenPak; compression is deterministic.
- **Not a silver bullet.** Savings are workload-dependent; we report real numbers, never aspirational ones.
- **Not ambient magic.** Every behavior has an opt-in or an explicit config path.

## 5. Non-Negotiable Principles

These are hard constraints. Violating them requires a Constitution amendment, not a judgment call.

1. **Zero code changes.** The user installs TokenPak and their existing agent workflow works. Always.
2. **Byte-fidelity on passthrough paths.** For Claude Code and any client where billing routing depends on the request body, TokenPak never re-serializes JSON — it splices at the byte level.
3. **Truth over polish in telemetry.** If we don't know whether a cache hit came from the client or the provider, the `cache_origin` is `unknown`. Never over-claim.
4. **No hardcoded enumerations.** Providers, models, adapters, features, and platforms are discovered at runtime with graceful handling of unknowns. The matrix changes weekly; the code must not.
5. **Modular tree only.** The monolith is archived at `_legacy/`. All new work lands in the modular package under `tokenpak/tokenpak/`.
6. **No versioned filenames.** Git is the version system. `CHANGELOG.md`, not `CHANGELOG_v1.0.md`. `ARCHITECTURE.md`, not `ARCHITECTURE_v2.md`.
7. **Commit + push after work completes.** Uncommitted edits are unsafe when multiple contributors share a checkout.

## 6. UX Principles

- **Fast, precise, transparent, intentional.** Every surface should prove value, not just expose internals.
- **Same concept, same shape.** `tokenpak serve`, `tokenpak cost`, `tokenpak integrate` — noun-verb, stable flags, consistent output block.
- **The first screen is the money screen.** Within 30 seconds of install, the user sees tokens saved. Never make them dig.
- **Errors teach.** Every error names the cause, the next step, and a doc link.
- **Silent success is fine.** If a command succeeded with nothing to say, don't fabricate output.

## 7. Architecture Philosophy

- **Single-refresh-owner invariant.** Exactly one subsystem owns credential refresh per provider. Cohabitation is a flag.
- **Build dynamic.** Discovery over declaration; runtime over compile-time.
- **Subsystem per concern.** `proxy/`, `companion/`, `compression/`, `cache/`, `creds/`, `monitor/` — each owns one thing and is replaceable.
- **Flat dependency graph.** Subsystems import from `core/` and each other only in the documented direction (see 01-architecture-standard.md).

## 8. Tone

- Direct. Never salesy.
- Specific numbers before adjectives. "32.8% saved" beats "massive savings."
- Developer-to-developer. Assume technical fluency; never patronize.
- Honest about limits. If a claim depends on workload, say so.

## 9. Naming Rules

- **Clear first, clever second.** `tokenpak integrate claude-code` beats `tokenpak wire`.
- **One concept, one name.** The Glossary ([08-naming-glossary.md](08-naming-glossary.md)) is canonical. If a term is missing from the Glossary, add it before shipping.
- **Internal names don't leak.** `cache_origin` is an enum value; users see "cache source" in the dashboard.

## 10. Documentation Rules

- README → Quickstart → Guide → API Reference → Troubleshooting. One path, no mazes.
- Every doc states its audience in the first paragraph.
- Examples are runnable. If you write a command, it must work as written today.
- No TODOs, no "coming soon," no broken links in shipped docs.

## 11. Quality Bar — "What Polished Means"

A release is polished when:

- [ ] Install → demo path works in under 60 seconds on a clean machine.
- [ ] Every user-visible command has a `--help` that actually describes it.
- [ ] The dashboard shows real, correct numbers — no placeholders, no zeros where data exists.
- [ ] Every error a user can trigger in the first hour has a troubleshooting entry.
- [ ] README, quickstart, dashboard, and CLI all tell the same story about what TokenPak does and what it saves.
- [ ] The audit ([09-audit-rubric.md](09-audit-rubric.md)) passes with no High-severity findings.

## 12. Amendment Process

Constitution changes require:
1. A PR titled `standards/constitution: <what's changing>`.
2. A one-paragraph "why this is changing" in the PR body.
3. Kevin's sign-off.
4. Updates to any Domain Standard or Glossary entry that referenced the old text.

Drafts and discussions happen in the vault, not here. This file is canon.
