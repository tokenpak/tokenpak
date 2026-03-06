# Save 35% on LLM Tokens

*Real numbers, practical setup, and a cost savings calculator.*

---

If you use LLMs heavily — running coding agents, summarizing documents, doing research — token costs add up fast. The math is straightforward:

- Claude Sonnet 3.5: **$3 per million input tokens**
- GPT-4o: **$2.50 per million input tokens**
- At 500 requests/day × 4,000 tokens each: **$6–7.50/day**, or $180–225/month

TokenPak cuts that by compressing your prompts before they hit the API. Here's what that looks like in practice.

---

## Real Numbers from Production

We benchmarked TokenPak on a mixed workload of coding, writing, and operational tasks across a 572-file vault:

| Scenario | Avg tokens/request | With TokenPak | Reduction |
|----------|-------------------|---------------|-----------|
| Code review | 8,200 | 5,330 | **35%** |
| Doc summarization | 6,800 | 3,740 | **45%** |
| Ops / runbook queries | 4,100 | 2,870 | **30%** |
| QMD + TokenPak (combined) | 20,801 → 6,136 | 3,265 | **84%** |

The 84% figure is from using TokenPak alongside Query-Matched Decoding (QMD). With TokenPak alone, expect 30–45% on typical workloads. Higher for verbose codebases and documentation-heavy contexts.

---

## Before and After: Real Examples

### Code Review Request

**Before TokenPak (4,231 tokens):**
```
Please review this Python module. Here is the full content:

#!/usr/bin/env python3
"""
Module for handling authentication.

This module provides comprehensive authentication functionality including
user login, session management, token generation, and validation. It supports
multiple authentication backends including database, LDAP, and OAuth2.

The module is designed to be extensible and can be integrated with any
web framework. See the documentation at docs.example.com for full details.

Author: Engineering Team
Last updated: 2026-01-15
Version: 2.4.1
"""

import hashlib
import hmac
import os
import time
# ... (hundreds of lines of commented, verbose code) ...
```

**After TokenPak (2,847 tokens — 33% reduction):**
```
Please review this Python module. Here is the full content:

import hashlib
import hmac
import os
import time
# [module header compressed: auth module v2.4.1]
# ... (same logic, stripped of verbose docstrings and redundant comments) ...
```

Same request. Same code. 33% fewer tokens. The LLM's answer is identical.

---

### Documentation Query

**Before (6,100 tokens):**
A Markdown file pasted into context with extensive formatting, nested bullet points, version history notes, and HTML comments.

**After (3,350 tokens — 45% reduction):**
Same content, with collapsed whitespace, stripped HTML comments, normalized bullet depth, and deduplicated section headers.

The meaning is preserved. The noise is gone.

---

## Setup in 5 Minutes

```bash
# 1. Install
pip install tokenpak

# 2. Start the proxy
tokenpak serve --port 8766

# 3. Point your LLM client at it
export ANTHROPIC_BASE_URL=http://localhost:8766
# or for OpenAI:
export OPENAI_BASE_URL=http://localhost:8766/v1

# 4. Verify
tokenpak status
```

That's it. Your existing workflow is unchanged. Every request now runs through the compression pipeline.

---

## Cost Savings Calculator

Use this to estimate your monthly savings:

**Inputs:**
- Daily requests: `R`
- Average input tokens per request: `T`
- Your model's price per million input tokens: `P`
- Expected compression rate: `C` (use 0.35 as a conservative estimate)

**Formula:**
```
Monthly cost without TokenPak:  R × 30 × T × P / 1,000,000
Monthly cost with TokenPak:     R × 30 × T × (1 - C) × P / 1,000,000
Monthly savings:                R × 30 × T × C × P / 1,000,000
```

**Examples:**

| Daily requests | Avg tokens | Model | Monthly before | Monthly after | Savings |
|---------------|-----------|-------|----------------|---------------|---------|
| 100 | 4,000 | Claude Sonnet ($3/M) | $36 | $23.40 | **$12.60** |
| 500 | 4,000 | Claude Sonnet ($3/M) | $180 | $117 | **$63** |
| 100 | 8,000 | GPT-4o ($2.50/M) | $60 | $39 | **$21** |
| 500 | 8,000 | GPT-4o ($2.50/M) | $300 | $195 | **$105** |
| 1,000 | 6,000 | Claude Sonnet ($3/M) | $540 | $351 | **$189** |

At 500 daily requests on Claude Sonnet: **TokenPak pays for itself in the first hour of use**.

---

## What Gets Compressed (and What Doesn't)

TokenPak uses typed recipes — it knows the difference between Python code, Markdown docs, JSON configs, and prose. Each type gets appropriate treatment:

| Content type | Typical reduction | What's removed |
|-------------|------------------|----------------|
| Python files | 20–35% | Docstrings, blank lines, type annotations (optional) |
| Markdown | 30–50% | Excessive formatting, repeated headers, HTML comments |
| JSON | 15–25% | Whitespace (minification) |
| Generic prose | 10–20% | Filler phrases, redundant whitespace |
| Shell scripts | 15–25% | Comments, blank lines |

**What's never touched:**
- Code logic and syntax (we don't rewrite code)
- Your prompt structure (questions, instructions)
- Output you explicitly request

The compression is transparent. You can inspect exactly what was removed:

```bash
tokenpak compress myfile.py --diff
# Shows a diff of what the pipeline removed
```

---

## Track Your Savings

TokenPak records everything locally. Check your savings anytime:

```bash
tokenpak savings
# This month: saved 142,000 tokens (~$0.43) via compression

tokenpak cost --week --by-model
# GPT-4o:       $8.20  (saved 38%)
# Claude Sonnet: $6.10  (saved 41%)
```

Or view the dashboard at `http://localhost:8766/dashboard` while the proxy is running.

---

## Optimize Further

### 1. Set a monthly budget

```bash
tokenpak budget set --monthly 50
tokenpak budget alert --at 80%
```

### 2. Route cheap queries to cheaper models

```bash
# Send test/debug queries to a cheaper model
tokenpak route set ".*test.*" gpt-4o-mini
tokenpak route set ".*debug.*" claude-haiku-3-5
```

Routing a 4,000-token test request from GPT-4o ($0.01) to GPT-4o-mini ($0.0006) saves **94%** on that request alone.

### 3. Combine with vault indexing

Instead of pasting large files into context, index your codebase and query it semantically:

```bash
tokenpak index ~/project
tokenpak vault search "authentication middleware"
# Returns: the 3 most relevant functions, ~500 tokens total
# Instead of: the whole auth module, ~8,000 tokens
```

### 4. Calibrate for your hardware

If you're indexing large vaults, run calibration once:

```bash
tokenpak calibrate ~/project --max-workers 8
```

This profiles your machine and sets optimal parallelism — making indexing 50–100x faster.

---

## The Math Over Time

Compression savings compound:

- Month 1 at 500 req/day: save **$63**
- Month 6: save **$378** total (same rate)
- Year 1: save **$756**

With vault indexing reducing context size even further, and smart routing handling the cheapest queries with the cheapest models, realistic users with heavy workloads save 40–60% on their total LLM spend.

---

## Get Started

```bash
pip install tokenpak
tokenpak serve
# Point your client at http://localhost:8766
tokenpak cost --today  # check savings after first day
```

[Full setup guide →](../getting-started.md)

---

*All benchmarks from internal testing on a 572-file mixed-language vault. Your results will vary based on content type and prompt patterns. Compression rates are estimates; TokenPak only compresses when it improves the cost/quality ratio.*
