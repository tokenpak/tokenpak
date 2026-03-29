# TokenPak Python SDK: Quick Start Guide

## What is TokenPak?

TokenPak is a context compression library for LLM agents and applications. It reduces the size of long context blocks by 40-60% while preserving semantic meaning, allowing you to stay within token budgets without sacrificing information. Works with any LLM provider (OpenAI, Anthropic, Google, local models, etc.).

## Installation

Install TokenPak via pip:

```bash
pip install tokenpak
```

If you need ML-based compression features, install the full extras:

```bash
pip install tokenpak[ml,tiktoken]
```

## Basic Example

Here's a minimal 5-minute example to get you started:

```python
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

# Your long context (could be from a file, API, chat history, etc.)
context = """
Long document with 10,000+ tokens...
[Your actual long text here]
"""

# Create a compression engine
engine = HeuristicEngine()

# Define a target budget (tokens)
hints = CompactionHints(target_tokens=2048)

# Compress the context
compressed = engine.compact(context, hints)

# Use the compressed result in your LLM prompt
prompt = f"""
Context (compressed):
{compressed}

Question: What are the key points?
"""

print(f"Original: {len(context.split())} words")
print(f"Compressed: {len(compressed.split())} words")
print(f"Savings: {100 * (1 - len(compressed) / len(context)):.1f}%")
```

## What Just Happened?

1. **Tokenization:** TokenPak tokenizes your input using tiktoken or a compatible tokenizer.
2. **Block-based compression:** The heuristic engine identifies important blocks (headers, summaries, semantic boundaries) and removes or condenses less critical content.
3. **Deterministic output:** The same input always produces the same compressed output, making it safe for reproducible LLM workflows.
4. **Budget compliance:** The engine respects your target token count while prioritizing information density.

## Common Use Cases

- **Chat history compression** — Keep long conversation threads within token limits while preserving context
- **Document retrieval augmentation** — Compress search results before feeding them to your LLM
- **Multi-turn dialogue** — Maintain context across many exchanges without hitting rate limits
- **Batch processing** — Process large document collections with a fixed token budget per item

## Verify Your Install

Test that everything is working:

```bash
python3 -c "from tokenpak import HeuristicEngine; print('TokenPak installed successfully!')"
```

## Next Steps

- **[Installation Guide](./install-guide.md)** — Detailed setup, Python versions, virtual environments
- **[API Reference](./api-reference.md)** — Full API documentation and all available options
- **[Compression Options](./compression.md)** — Advanced configuration and custom engines
- **[Examples](./examples/README.md)** — Real-world examples and patterns
- **[CLI Reference](./cli-reference.md)** — Command-line tools for compression tasks

## Support

For questions, issues, or feature requests, visit the [TokenPak GitHub repository](https://github.com/tokenpak/tokenpak).
