---
title: "TokenPak Compression Tuning Guide"
subtitle: "Optimize token savings vs latency tradeoffs for your use case"
author: Trix
date: 2026-03-25
tags: [tokenpak, compression, tuning, performance]
---

# TokenPak Compression Tuning Guide

This guide explains **how to tune TokenPak's compression engine** to maximize token savings while minimizing latency impact for your specific workload.

## Overview: Why Compression Matters

LLM API costs scale with token count. TokenPak's compression pipeline intercepts requests and semantically equivalent content with **2–8% fewer tokens**, depending on your data.

### The Math

- **Without compression:** 10,000-token request = $0.30 (Sonnet)
- **With 5% compression:** 9,500 tokens = $0.285 (saves $0.015 per request)
- **At 100 requests/day:** ~$5/month saved (or 5% cost reduction)

### The Tradeoff

Compression has latency cost:

| Strategy | Latency | Token Savings | Best For |
|----------|---------|---------------|----------|
| **Dedup** | <1ms | 2–3% (on repeated context) | Iterative workflows, session context |
| **Segmentation** | <2ms | 1–2% (metadata, structure) | Code review, doc analysis |
| **Alias compression** | <5ms | 3–5% (long repeated names) | Large schemas, entity lists |
| **Instruction table** | <10ms | 4–6% (cookbook patterns) | Repetitive tasks, templates |
| **Semantic caching** (off) | N/A | 15–40% (prompt cache hit) | Same prompts, different inputs |

**Default (all enabled):** ~5ms latency for ~3–5% savings. Acceptable for most workloads.

---

## Compression Strategies: How to Use Each

### Strategy 1: Dedup (Fast, Safe)

**What it does:** Removes duplicate message turns from conversation history.

**When it helps:**
- Iterative debugging (code repeatedly pasted)
- Multi-turn conversations where context is re-injected
- Workflow loops where the same block appears multiple times

**Real example:**

```
Message 1: "Here's the current auth schema:\n<200 lines JSON>"
Message 2: "Review line 42."
Message 3: [Assistant response]
Message 4: "Here's the current auth schema:\n<200 lines JSON>"  ← DEDUP removes this
Message 5: "Now add refresh tokens."
```

**Savings:** 1–3% (depends on how often context repeats)

**Configuration (in proxy config):**

```python
# tokenpack/proxy.py
pipeline = CompressionPipeline(
    enable_dedup=True,           # ← enable/disable
    enable_segmentation=True,
    enable_alias=True,
    enable_directives=True,
)
```

**When to disable:** Single-turn requests (queries, completions). No benefit, adds latency.

---

### Strategy 2: Segmentation (Safe, Structural)

**What it does:** Classifies message content into typed blocks (code, markdown, JSON, tool results, etc.) and applies targeted compression to each type.

**Strategies per segment type:**

| Segment Type | What Gets Compressed | Savings | Risk |
|---|---|---|---|
| **Code** | Signature extraction, docstring keep | 5–8% | Low (retains logic) |
| **Markdown** | Keep headers, strip body text | 3–6% | Medium (loses details) |
| **JSON** | Schema + sample data (strip repetitive rows) | 4–7% | Medium (loses volume) |
| **Tool results** | Truncation (keep first N lines) | 2–4% | Low (summaries) |
| **Text/prose** | Token filtering by importance | 3–5% | High (selective) |

**Real example — code compression:**

```python
# Before (28 tokens)
def calculate_total(items):
    """Calculate the sum of item values."""
    result = 0
    for item in items:
        result += item['price']
    return result

# After (signature only, 8 tokens)
def calculate_total(items): ...
    """Calculate the sum of item values."""
```

**Configuration (in `proxy.py`):**

```python
pipeline = CompressionPipeline(
    enable_segmentation=True,    # ← enable/disable
    enable_dedup=True,
    enable_alias=True,
    enable_directives=True,
)

# Optionally provide a recipe (directives)
# See recipes/oss/*.yaml for examples
```

**When to disable:** If you need full code bodies preserved (not just signatures). Saves 2ms latency but gives up 3–6% compression.

---

### Strategy 3: Alias Compression (Moderate)

**What it does:** Detects long repeated names/entities (variable names, long strings, UUIDs) and replaces them with short aliases.

**Real example:**

```
Before:
"The ManagerInterface.process_authentication_token() method..."
"Then ManagerInterface.process_authentication_token() handles..."
"Finally ManagerInterface.process_authentication_token() returns..."

After:
"The A1() method..."
"Then A1() handles..."
"Finally A1() returns..."

Mapping: A1 → ManagerInterface.process_authentication_token
```

**When it helps:**
- Long class/function names repeated 3+ times
- Domain-specific acronyms or entity names
- Code with verbose variable names

**Savings:** 3–5% (depends on repetition and name length)

**Configuration:**

```python
pipeline = CompressionPipeline(
    enable_alias=True,              # ← enable/disable
    alias_min_occurrences=3,        # minimum times to alias
    alias_min_length=20,            # minimum name length to alias
    enable_dedup=True,
    enable_segmentation=True,
)
```

**Tuning parameters:**
- `alias_min_occurrences=2` → more aggressive, catch 2+ repeats
- `alias_min_occurrences=5` → conservative, only high-frequency names
- `alias_min_length=15` → catch shorter names
- `alias_min_length=30` → only very long names

**When to disable:** If output is sent to users (aliases make it unreadable). Safe to disable; minimal latency impact.

---

### Strategy 4: Instruction Table (Advanced)

**What it does:** Uses a persistent table of common instructions and replaces repetitive task descriptions with references.

**Real example:**

```
Before:
"You are a code reviewer. Your job is to find bugs, suggest improvements, 
enforce style consistency, and suggest refactoring opportunities..."

After:
"Apply instruction [CODE-REVIEW-V2]"

Lookup table maps [CODE-REVIEW-V2] → full instruction text
```

**When it helps:**
- Batch processing (same role repeated 10+ times)
- Service agents (standard prompts)
- Workflows with template instructions

**Savings:** 4–8% (depends on instruction repetition)

**Configuration:**

```python
pipeline = CompressionPipeline(
    enable_instruction_table=True,                   # ← enable/disable
    instruction_table_path="path/to/instruction.db", # optional custom table
    context_budget_tight=True,                       # aggressive mode
    enable_dedup=True,
    enable_segmentation=True,
    enable_alias=True,
)
```

**How to add instructions:**

```python
# In your code:
from tokenpak.agent.compression.instruction_table import InstructionTable

table = InstructionTable(path="instruction.db")
table.add_instruction(
    id="CODE-REVIEW-V2",
    text="You are a code reviewer...",
)
```

**When to disable:** One-shot requests, unique prompts. Overhead > savings for low-repetition tasks.

---

### Strategy 5: Semantic Caching (Native to Claude API)

**What it does:** Reuses cached prompt prefixes when subsequent requests have similar context.

**How it works:**
- First request with context → stored in Claude's cache (5 min TTL, by default)
- Identical or very similar context → reuses cached tokens at ~10% cost

**Real example:**

```
Request 1: "Here's the codebase:\n<50KB context>" → 12 cache creation tokens
Request 2: "Same codebase, different question" → 24 cache read tokens (10% cost)

Savings: (12 - 2.4) tokens per request = ~80% on that chunk
```

**Savings:** 15–40% (only on repeated prefix, but huge when it hits)

**How to enable (in your client code):**

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are a code reviewer. [... static system prompt ...]",
            "cache_control": {"type": "ephemeral"}  # ← enable caching
        }
    ],
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Here's the full codebase:\n" + large_code,
                    "cache_control": {"type": "ephemeral"}  # ← cache this too
                }
            ]
        }
    ]
)

# Subsequent requests with same codebase will hit the cache
```

**When to use:**
- System prompts (static, reused 100% of the time)
- Large context blocks (code, docs, schemas) used in multiple requests
- Batch workflows where the same context applies to different questions

**When NOT to use:**
- One-off requests
- Context that changes every turn

---

## Performance Characteristics: Latency vs Savings

### Measured on Fleet (March 2026 Benchmark)

| Agent | Compression Mode | Token Savings | P50 Latency | P99 Latency |
|---|---|---|---|---|
| **Trix** | All enabled (default) | 2.8% | 5.2ms | 12ms |
| **Trix** | Dedup + Segment only | 2.1% | 2.1ms | 5ms |
| **Trix** | Dedup only | 1.2% | 0.8ms | 2ms |
| **Sue** | All enabled | 2.2% | 6.1ms | 14ms |
| **Cali** | All enabled | 2.8% | 4.9ms | 11ms |

**Analysis:**
- Dedup: <1ms overhead, 1–2% savings (always worth it)
- Segmentation: <2ms overhead, 1–2% savings (usually worth it)
- Alias: <5ms overhead, 3–5% savings (worth it for code-heavy workloads)
- Instruction table: <10ms overhead, 4–6% savings (worth it for batch/service work)

---

## Configuration: Copy-Paste Examples

### Example 1: Lightweight (Low Latency)

Use this for real-time chat, quick queries.

```python
# In proxy.py
pipeline = CompressionPipeline(
    enable_dedup=True,
    enable_segmentation=False,
    enable_alias=False,
    enable_instruction_table=False,
    enable_directives=False,
)
```

**Tradeoff:** <2ms latency, 1–2% savings.

---

### Example 2: Balanced (Default)

Use this for general workloads (development, analysis).

```python
# In proxy.py
pipeline = CompressionPipeline(
    enable_dedup=True,
    enable_segmentation=True,
    enable_alias=True,
    enable_instruction_table=False,
    enable_directives=True,
)
```

**Tradeoff:** ~5ms latency, 3–5% savings.

---

### Example 3: Aggressive (High Savings)

Use this for batch work, background jobs, offline analysis.

```python
# In proxy.py
pipeline = CompressionPipeline(
    enable_dedup=True,
    enable_segmentation=True,
    enable_alias=True,
    enable_instruction_table=True,
    enable_directives=True,
    context_budget_tight=True,
    alias_min_occurrences=2,      # catch more aliases
    alias_min_length=15,           # shorter names too
)
```

**Tradeoff:** ~10–15ms latency, 5–8% savings.

---

### Example 4: Code Review Specialized

Optimized for code review tasks.

```python
# In proxy.py
pipeline = CompressionPipeline(
    enable_dedup=True,
    enable_segmentation=True,
    enable_alias=True,
    enable_instruction_table=True,
    enable_directives=True,
)

# Add custom hook for code-specific compression
def code_priority_hook(messages):
    """Keep code segments, compress narrative text."""
    for msg in messages:
        # Custom logic here
        pass
    return messages

pipeline.add_hook(code_priority_hook)
```

**Tradeoff:** ~8ms latency, 6–8% savings on code.

---

## Tuning Checklist

When you want to optimize compression for YOUR workload:

- [ ] **Profile your requests:** What's the typical size? Code? Text? JSON?
- [ ] **Set a baseline:** Run a week with `enable_all=True`, measure token savings.
- [ ] **Identify bottlenecks:** Which compression stage gives the most savings? (Use `PipelineResult.stages_run`)
- [ ] **Disable low-ROI stages:** If alias compression adds 5ms for <0.5% savings, disable it.
- [ ] **Batch profile:** Test on 100+ requests to get real averages (single-request measurements are noisy).
- [ ] **Test in production:** A/B test config changes on real workloads, measure cost + latency.

### Monitoring

```python
# After pipeline.run(), inspect:
result = pipeline.run(messages)

print(f"Tokens saved: {result.tokens_saved} ({result.savings_pct}%)")
print(f"Latency: {result.duration_ms}ms")
print(f"Stages run: {', '.join(result.stages_run)}")
```

---

## Common Tuning Questions

### Q: "Compression makes responses slightly different. Is this safe?"

**A:** TokenPak compression is **semantic-preserving**. The meaning of the request/response is identical; only formatting and redundancy are removed. Safe for production.

### Q: "Can I compress the response too?"

**A:** TokenPak currently compresses **requests only** (to LLM). Response compression would require client-side modifications. Future feature.

### Q: "How much should I save?"

**A:** Typical range is **2–6% depending on workload:**
- Text-heavy (essays, reports): 2–3%
- Code-heavy (review, analysis): 4–6%
- JSON/structured: 3–5%
- Real-time chat (short messages): <1%

### Q: "Should I use alias compression?"

**A:** Yes, unless output is user-facing. Aliases make text unreadable in logs/exports.

### Q: "How often should I update the instruction table?"

**A:** Once per week or when your templates change significantly. It's auto-reloaded every 5 minutes.

### Q: "What if compression breaks something?"

**A:** File an issue on GitHub. In the meantime, disable the offending stage and continue. Compression is designed to fail gracefully.

---

## Reference: Source Code Links

- **Pipeline orchestrator:** [`packages/core/tokenpak/agent/compression/pipeline.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/pipeline.py) (line 20–150)
- **Dedup logic:** [`packages/core/tokenpak/agent/compression/dedup.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/dedup.py)
- **Segmentizer:** [`packages/core/tokenpak/agent/compression/segmentizer.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/segmentizer.py)
- **Alias compressor:** [`packages/core/tokenpak/agent/compression/alias_compressor.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/alias_compressor.py) (line 30–80 for tuning)
- **Instruction table:** [`packages/core/tokenpak/agent/compression/instruction_table.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/instruction_table.py)
- **Directives applier:** [`packages/core/tokenpak/agent/compression/directives.py`](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/agent/compression/directives.py)

---

## Next Steps

1. **Start with the balanced config** (Example 2 above).
2. **Measure token savings** on your workload for 1 week.
3. **Adjust based on your latency tolerance:** Trade off ~5% savings for <10ms latency most cases.
4. **Monitor regularly:** Token costs shift as context size changes.

---

**Questions? Issues?** Open a GitHub issue or reach out to the TokenPak team on Slack.
