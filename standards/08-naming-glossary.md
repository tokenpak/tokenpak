---
title: TokenPak Naming Glossary
type: standard
status: draft
depends_on: [00-product-constitution.md]
---

# TokenPak Naming Glossary

The single source of truth for what terms mean inside TokenPak. Code, docs, dashboard, CLI, and marketing copy all draw from this list.

**Rules:**
- One concept, one term. No synonyms.
- If a term is missing, add it in the same PR that introduces it.
- External copy uses the `external label` column when it differs from the internal term.

---

## A

**adapter**
A per-client integration that wires a specific LLM tool (Claude Code, Cursor, Aider, etc.) to the TokenPak proxy. Each adapter lives in `tokenpak/cli/integrations/` and has an entry in `tokenpak integrate`.
External label: "integration."

**api-tpk-v1**
TokenPak's public HTTP API surface, documented in `docs/api-tpk-v1.md`. The `v1` in the name is a stability contract, not a filename version number (carve-out from Constitution §5.6).

## B

**budget (companion)**
A per-session token or dollar cap enforced by the companion before sending prompts. Stored in `~/.tokenpak/companion/budget.db`.
External label: "spend guard."

**byte-preserved passthrough**
The proxy mode for clients where request body byte-identity matters (Claude Code billing routing is the canonical case). The proxy splices vault injection into the byte stream without re-serializing JSON.
External label: "request-integrity passthrough."

## C

**cache**
Never say "cache" alone in docs, dashboard, or API. Always qualify as **TokenPak cache** or **provider cache**.

**TokenPak cache**
The proxy's local store of compressed-payload keys and their outputs. Lives in `~/.tokenpak/cache/`. Distinct from any provider-side cache. Controlled by `tokenpak cache {stats,clear}`.
External label: same.

**provider cache**
The cache maintained by the model vendor. Anthropic's `cache_control` blocks are the most common case. TokenPak never writes to it; TokenPak observes and reports on its hit rate.
External label: same.

**cache_origin**
Enum on every request row in `monitor.db` indicating whether an observed cache hit came from `proxy` (the TokenPak cache), `client` (the provider cache, inferred from response), or `unknown`. Never over-claim; `unknown` is a legitimate bucket.
External label: "cache source."

**capsule**
A serialized memory snapshot produced by the companion and stored in `~/.tokenpak/companion/capsules/`. Capsules are reusable context blocks that get injected into prompts on demand.
External label: "memory capsule."

**client**
The LLM tool a human interacts with: Claude Code, Cursor, Aider, Cline, Continue, Codex, or any SDK. Distinct from **provider**.
External label: same.

**compaction**
LLM-client-side summarization of conversation history when the context window fills. **Not** a TokenPak feature — this is behavior inside Claude Code, openclaw, etc. Mentioned in docs only to disambiguate from compression.
External label: "conversation compaction" on first use; "compaction" thereafter.

**companion**
TokenPak's local pre-send optimizer for TUI and CLI clients. Runs as an MCP server, registers hooks and skills, attaches a system prompt. Not for API/SDK users — those get the full proxy pipeline. Lives in `tokenpak/companion/`.
External label: "pre-send optimizer" on first mention; "companion" after.

**companion journal**
SQLite log at `~/.tokenpak/companion/journal.db` recording prompt-side events (token estimates, budget decisions, capsule loads). Distinct from `monitor.db` which is wire-side.
External label: "companion log."

**compression**
TokenPak's wire-side token reduction pipeline. Deterministic, <50ms, built from stages (dedup, alias, segmentize, directives). **Not** compaction. Lives in `tokenpak/compression/`.
External label: "context compression."

**creds router**
The subsystem that discovers and arbitrates credentials across 5 provider surfaces (codex-cli, claude-cli, env-pool, user-config, openclaw). Single-refresh-owner invariant: exactly one path owns refresh per provider.
External label: "credentials router" or "creds."

## D

**dashboard**
The local web UI at `127.0.0.1:<dashboard-port>` that visualizes monitor-DB data. Local only; never a cloud component.
External label: same.

**directives (compression stage)**
The compression stage that recognizes and replaces recurring instruction blocks with short tokens. One of the four canonical stages.
External label: "directive compression."

**dedup (compression stage)**
The compression stage that removes literal repetition across a prompt. First stage in the canonical pipeline.
External label: "deduplication."

## G

**gateway**
Context-dependent. If prefixed (`dashboard-gateway`, `proxy-gateway`), means that subsystem's HTTP entry point. Never use standalone — too vague.
External label: avoid; pick a more specific term.

## I

**integration**
See **adapter**. Preferred external label.

## M

**model**
A specific model identifier within a provider (e.g., `claude-opus-4-7`, `gpt-5.3-codex`). Distinct from **provider**.
External label: same.

**monitor DB**
The SQLite ledger at `~/.tokenpak/monitor.db` recording every request the proxy handles. One row per request, schema versioned, `cache_origin` required.
External label: "local request log."

## P

**profile (Claude Code)**
TokenPak's notion of a Claude Code consumption mode (TUI, CLI, tmux, SDK, IDE, cron). Profiles select routing rules and companion behavior. Detected at runtime via `X-Claude-Code-*` headers.
External label: "Claude Code mode."

**protected tokens**
Tokens within a prompt that would hit the provider cache if left intact. Compression preserves them; the savings calculation credits them separately so TokenPak doesn't over-claim.
External label: "cache-protected tokens."

**provider**
The vendor serving the model — Anthropic, OpenAI, Google, local Ollama, etc. Distinct from **client** and **model**.
External label: same.

**proxy**
TokenPak's local HTTP server at `127.0.0.1:8766`. Handles request compression, forwarding, and response monitoring. Byte-preserved passthrough on billing-sensitive paths.
External label: same.

## R

**recipe**
A YAML-defined compression configuration. Recipes live in `tokenpak/recipes_oss/*.yaml` and auto-register at import. Users can write their own in `~/.tokenpak/recipes/`.
External label: "compression profile" on first mention; "recipe" after.

**route**
A decision about which provider/model a request should go to. Routes come from the `routing/` subsystem and respect user-configured fallback rules.
External label: "routing rule."

## S

**savings**
The tokens and dollars TokenPak reduced from a prompt or set of prompts. Always attributable to a cause (compression, cache hit) and an origin (`proxy`, `client`, `unknown`).
External label: same. Never say "reductions" or "optimizations."

**segmentize (compression stage)**
The compression stage that splits long passages into cacheable segments matching the provider's cache granularity.
External label: "segmentization."

**session**
A single client-side run of an LLM tool — one `claude` invocation, one Cursor IDE window, etc. Companion state and budget are scoped per session.
External label: same.

**stage**
One step in the compression pipeline. Canonical stages: `dedup`, `alias`, `segmentize`, `directives`. Stages register via `Stage.__subclasses__`.
External label: "compression stage."

## T

**telemetry**
Anonymous, opt-in usage metrics sent to TokenPak's own servers. Off by default. Configurable in `~/.tokenpak/config.yaml`.
External label: same. Note: explicitly not the same as `monitor.db` (local, always on).

## V

**vault**
The `tokenpak/vault/` subsystem that implements semantic indexing of arbitrary directories. Users may point it at a notes store, a codebase, a docs tree, or any other directory they want to search without an LLM call.
External label: "indexed directory."

**vault injection**
The proxy's mechanism for splicing vault-retrieved context into requests without re-serializing JSON. Byte-level operation.
External label: "context injection."

---

## Retired / Forbidden Terms

These were used in earlier drafts or competing systems. Do not use.

| Forbidden | Use instead |
|---|---|
| "memo," "memoized" | TokenPak cache / provider cache |
| "reduction," "shrink," "shrinkage" | savings / compression |
| "TokenPak agent" (for the proxy) | proxy |
| "middleware" | proxy |
| "gateway" (unqualified) | proxy (or name the specific gateway) |
| "context compiler" | proxy + companion |
| "universal content compiler" | (retired framing from ARCHITECTURE.md) |
| "TokenPak service" | proxy (it's local, not a service) |
| "LLM optimizer" (standalone) | companion (pre-send) or proxy (wire-side) |

---

## Adding a Term

1. Pick the canonical internal term.
2. Add an entry here under the right letter, including an `external label` if the docs-facing term differs.
3. Add any retired synonyms to the "Forbidden Terms" table.
4. Update any code, docs, or dashboard copy that used the old synonym in the same PR.

If you catch yourself hesitating over what to call something, that's the signal to add it here before shipping.
