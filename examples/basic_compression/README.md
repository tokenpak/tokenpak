# Basic Compression Example

**Problem:** LLM context windows are expensive. Verbose text wastes tokens and increases API costs.

**Solution:** TokenPak's `HeuristicEngine` compresses text while preserving meaning — removing filler sentences, redundant comments, and low-signal content.

## What This Shows

- Simple text compression with `HeuristicEngine`
- Targeted compression to a specific token budget
- Code compression (removing redundant comments)

## Expected Results

| Content Type | Relative Savings |
|---|---|
| Verbose prose | High |
| Over-commented code | Moderate |
| Instruction lists | Low–Moderate |

## Setup

```bash
pip install tokenpak
```

## Run

```bash
python main.py
```

## Sample Output

```
=== Prose Compression ===

Original:   ~155 tokens (621 chars)
Compressed: ~75 tokens (300 chars)
Savings:    52%

--- Compressed Output ---
The TokenPak library provides a comprehensive solution for managing token budgets
in large language model applications.
...
```

## Key API

```python
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

engine = HeuristicEngine()

# Basic compression
compressed = engine.compact(text)

# Targeted compression (stay under N tokens)
compressed = engine.compact(text, CompactionHints(target_tokens=100))
```

## Time to Complete

~5 minutes
