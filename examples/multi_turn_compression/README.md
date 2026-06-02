# Multi-Turn Conversation Compression

**Problem:** Long chat histories consume massive token budgets. A 50-turn conversation can eat 10,000+ tokens, leaving little room for new content and driving up API costs.

**Solution:** Compress older turns while preserving recent context. TokenPak's `HeuristicEngine` compresses older messages substantially, making space for continued conversation.

## What This Shows

- Sliding window strategy: keep N recent turns intact
- Compress older turns with `HeuristicEngine`
- System messages are always preserved
- Token budget enforcement

## Expected Results

| History Length | Relative Savings |
|---|---|
| 10 turns | Moderate |
| 20 turns | High |
| 50 turns | Very high |

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
Before compression: 9 messages, ~650 tokens

After compression:  9 messages, ~380 tokens
Savings:            42%

Message breakdown:
  [1] system      ~15 tokens
  [2] user        ~22 tokens [COMPRESSED]
  [3] assistant   ~48 tokens [COMPRESSED]
  ...
  [8] user        ~28 tokens
  [9] assistant   ~45 tokens
```

## Key Strategy

```python
# Keep recent turns intact — they matter most
recent = messages[-6:]   # last 3 user+assistant pairs
older = messages[:-6:]

# Compress older turns to ~50% of original
for msg in older:
    hints = CompactionHints(target_tokens=estimate_tokens(msg["content"]) // 2)
    compressed = engine.compact(msg["content"], hints)
```

## Time to Complete

~10 minutes
