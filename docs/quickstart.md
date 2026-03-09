# TokenPak Quick Start Guide

Get up and running with the TokenPak Python SDK in 5 minutes.

---

## What is TokenPak?

TokenPak is a context compression library for LLM-powered applications. When your prompts grow large — chat histories, document dumps, multi-turn dialogues — TokenPak compresses them intelligently before they hit the model. The result: 40–60% fewer tokens, lower API costs, and no meaningful loss of context.

It works with any LLM provider (Anthropic, OpenAI, Gemini, etc.) and drops into any existing Python stack. Compression is deterministic: same input always produces the same output, which means it's safe to cache and predictable in production.

---

## Install

```bash
pip install tokenpak
```

For more accurate token counting (recommended for production):

```bash
pip install tokenpak[tiktoken]
```

> Requires Python 3.8+. See [install-guide.md](./install-guide.md) for virtual env setup and troubleshooting.

---

## Basic Example

```python
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

# Your long context (could be chat history, docs, instructions, etc.)
context = """
    The TokenPak library provides a comprehensive solution for managing token budgets
    in large language model applications. It includes multiple compression strategies,
    caching mechanisms, and telemetry tools. The library is designed to be easy to use
    while providing powerful functionality for advanced users. By compressing content,
    you can fit more information into fewer tokens, reducing API costs and improving
    response quality. The heuristic engine uses rule-based text processing to remove
    redundant content while preserving the most important information.
"""

# Initialize the engine
engine = HeuristicEngine()

# Compress with a token budget
hints = CompactionHints(target_tokens=100)
compressed = engine.compact(context, hints)

# Use the compressed context in your prompt
question = "What does TokenPak do?"
prompt = f"Context:\n{compressed}\n\nQuestion: {question}"
print(prompt)
```

To compress without a specific token target (uses default heuristics):

```python
compressed = engine.compact(context)
```

---

## What Just Happened?

Here's what TokenPak did under the hood:

1. **Tokenization** — The engine estimated token counts for each sentence and block in your context.
2. **Block-based compression** — Content was split into semantic blocks (sentences, paragraphs, code chunks). Each block was scored by information density.
3. **Heuristic filtering** — Low-signal sentences (filler, redundant restatements, over-explained comments) were dropped. High-value content — code, headers, list items, key facts — was preserved.
4. **Deterministic output** — Given the same input and the same hints, you'll always get the same compressed output. No randomness, no drift.

The net result: fewer tokens sent to the model, with the substance of your context intact.

---

## Common Use Cases

- **Chat history compression** — Trim multi-turn conversation logs before appending to a new prompt
- **Document retrieval augmentation (RAG)** — Compress retrieved chunks before injecting into context
- **Multi-turn dialogue context** — Keep system context lean as conversations grow
- **Code review tools** — Strip redundant comments from large files before analysis

---

## Next Steps

- 📖 [Full API Reference](./api-reference.md) — All classes, methods, and parameters
- ⚙️ [Configuration Options](./compression.md) — Tune compression aggressiveness, preserve lists/code, set custom budgets
- 🧪 [Advanced Examples](../examples/README.md) — Async compression, streaming, LangChain integration, FastAPI middleware
- 🔧 [Install Guide](./install-guide.md) — Virtual envs, extras, and troubleshooting

---

> **Tip:** TokenPak also ships a proxy server (`tokenpak serve`) that transparently compresses requests to any OpenAI-compatible endpoint. No code changes needed — see the [CLI Reference](./cli-reference.md) for details.
