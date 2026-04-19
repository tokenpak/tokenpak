---
title: TokenPak Docs Style Guide
type: standard
status: draft
depends_on: [00-product-constitution.md, 05-brand-style-guide.md, 08-naming-glossary.md]
---

# TokenPak Docs Style Guide

How TokenPak documentation is structured and written. Applies to `docs/`, the README, the quickstart, API references, troubleshooting pages, blog posts, and release notes.

---

## 1. The Doc Ladder

Readers arrive via different paths. TokenPak's documentation is a ladder — each rung serves a specific moment in the reader's journey.

| Rung | Doc | Reader moment |
|---|---|---|
| 0 | README | "What is this?" |
| 1 | Quickstart (`docs/quickstart.md`) | "I'm going to try it. 5 minutes." |
| 2 | Guides (`docs/guides/*.md`) | "I'm using it. How do I do X?" |
| 3 | API reference (`docs/api-tpk-v1.md`, `docs/api-reference.md`) | "I'm integrating. What does this function do?" |
| 4 | Troubleshooting (`docs/troubleshooting/*.md`) | "Something broke." |
| 5 | Architecture (`docs/ARCHITECTURE.md`) | "Why is it built this way?" |

**Rule:** every doc declares its rung in its frontmatter (`rung: 2`). Don't mix rungs in one page — a quickstart that ends with API reference dilutes both.

## 2. Frontmatter

Every doc file starts with:

```yaml
---
title: <H1 of the page>
rung: 0|1|2|3|4|5
audience: <one-sentence audience statement>
updated: YYYY-MM-DD
status: draft|current|deprecated
---
```

`updated` is edited by hand when the content meaningfully changes; it is *not* auto-generated from git. Stale `updated` dates are a smell the audit flags.

## 3. Voice

Descends from the Brand Style Guide (§3). Docs-specific:

- **Second person** (`you`) for instructions. "Run `tokenpak serve` to start the proxy."
- **First-person plural** (`we`) only sparingly, and only for TokenPak's own decisions. "We use SQLite because…"
- **Active voice.** "TokenPak compresses the payload" beats "the payload is compressed."
- **Present tense** for behavior descriptions. Past tense only in changelogs.
- **No filler.** "It's worth noting that…" is empty. Cut it.

## 4. Audience Statement

Every doc names its reader in the first paragraph.

> *This guide is for developers who have TokenPak installed and want to wire it up to a new client.*

That line sets scope. If a reader doesn't match the audience, they know to leave.

## 5. Structure

### 5.1 README (rung 0)

Follows the [readme-template](templates/readme-template.md). Fixed sections, fixed order:
1. Tagline (Constitution §2)
2. 30-second demo (install + two commands)
3. "Works with" row (one-line list of clients)
4. Install
5. What's included
6. Links to Quickstart + API reference
7. License

### 5.2 Quickstart (rung 1)

Follows [quickstart-template](templates/quickstart-template.md). Must end with a working demo, not a "now read the docs" cliff.

### 5.3 Guide (rung 2)

- **Goal-oriented title.** "Integrate Claude Code with TokenPak," not "Claude Code Integration." A title answers "what will I have done after this?"
- **Prereqs section** near the top. Version numbers, required credentials, required commands.
- **Numbered steps.** One action per step. The reader should never wonder "do I do this now or read ahead?"
- **Verification step** at the end. "Run `tokenpak savings`. You should see…" — never leave the reader guessing whether it worked.

### 5.4 API Reference (rung 3)

- **One function per entry.** Signature first, plain-English summary, parameters, return value, example, exceptions.
- **Examples are runnable.** Copy-paste must work.
- **Stability** markers: `stable`, `beta`, `internal`. Unmarked = stable.

### 5.5 Troubleshooting (rung 4)

Follows [troubleshooting-template](templates/troubleshooting-template.md). Symptom-first, one problem per page, with the exact error text users see.

### 5.6 Architecture (rung 5)

Explains *why*, not *how-to*. Diagrams are welcome; every diagram labels its layers with the subsystem names from `01-architecture-standard.md`.

## 6. Examples

- **Runnable or dropped.** If you can't test the example today, delete it. Rotten examples are worse than no examples.
- **Real output, not prose.** Show the CLI block the reader will see, not a narrated summary.
- **Minimal.** Strip every line that isn't load-bearing for the lesson.

## 7. Terminology

Use the Glossary ([08-naming-glossary.md](08-naming-glossary.md)) as the single source of truth. If a term doesn't appear there and you need it, add it in the same PR.

- "**cache**" is ambiguous — always qualify: "TokenPak cache" or "provider cache."
- "**compression**" and "**compaction**" are not synonyms. Compression is TokenPak's wire-side token reduction; compaction is the LLM client's conversation-history summarization.
- "**savings**" always refers to tokens or dollars reduced by TokenPak. Cache hit-rate improvements are labeled separately.
- "**client**" is the LLM tool (Claude Code, Cursor, etc.). "**provider**" is the model vendor (Anthropic, OpenAI). They are not interchangeable.

## 8. Links

- **Prefer relative links** within `docs/` and `standards/`. Cross-directory links via the shortest unambiguous path.
- **Check before shipping.** `make docs-check` runs a link checker. Broken links are a release blocker.
- **External links** get a `rel="noopener"` automatically from the MkDocs config; nothing special needed per-page.
- **No "click here."** Link the noun. `See the [Quickstart](../quickstart.md)`, not `See [here](../quickstart.md)`.

## 9. Code Samples

- **Language tag required** on every fenced block. `bash`, `python`, `yaml`, `json`, `toml`, `text` (for plain output).
- **Prompts shown** in bash blocks: `$ ` for user input, plain line for output.
- **No syntax-highlighting gymnastics.** If a sample is long, split it; don't collapse it.

## 10. Headings

- One `H1` per page; it matches the frontmatter `title`.
- `H2` for top-level sections, `H3` for subsections. `H4` only when necessary. No `H5` / `H6` in shipped docs.
- **Sentence case**, not Title Case. "Install the proxy" beats "Install The Proxy."
- No punctuation in headings except for a colon in "X: Y" form. No trailing periods, no question marks.

## 11. Lists

- **Bullets** for sets of peer items. **Numbers** for ordered steps only.
- **Parallel grammar.** Start every bullet the same way: all noun phrases or all imperatives, not a mix.
- Max depth: two levels. Deeper nesting indicates the content wants to be a table or its own section.

## 12. Tables

- **Headers are nouns.** Not verbs, not sentences.
- **Columns sorted by importance**, left to right.
- **Numbers right-aligned.** Text left-aligned.
- Don't use tables for layout; use them for data with two or more dimensions.

## 13. Diagrams

- **Mermaid** for flowcharts, sequence diagrams, and simple architecture diagrams. Source in the markdown file.
- **ASCII art** is acceptable for simple block diagrams in reference docs; always wrap in a `text` code block.
- **No PNG screenshots of code.** Text is copyable; screenshots rot.
- **Image alt text required.** "Architecture diagram showing proxy between client and provider," not "diagram."

## 14. Forbidden Patterns

- "**TODO**" in shipped docs. Move the TODO to an issue; remove the line.
- "**Coming soon.**" Either it's shipped or it's not in the doc.
- "**Simply / just / easily.**" Cut.
- **Repeating the page title** in the first sentence of the body. The reader already read it.
- **"In this guide, we will…"** Get to the instruction.
- **`v2`, `v2.0`, date stamps in filenames** (Constitution §5.6). Git tracks versions.

## 15. Deprecation

When a doc is replaced:

1. The old page becomes `status: deprecated` with a prominent banner linking to the replacement.
2. The deprecated page is kept for one minor version, then deleted.
3. The `updated` date stays the date of deprecation.
4. Incoming links are updated in the same PR.

Silent deletion breaks external references; never do it.
