---
name: Cross-Provider Matrix Test
system: |
  You are a senior software engineer. Write clean Python code with type hints.
  Be concise.
max_tokens: 4096

matrix:
  - name: Sonnet Direct
    platform: api
    provider: anthropic
    model: claude-sonnet-4-6

  - name: Sonnet + TokenPak
    platform: proxy
    provider: anthropic
    model: claude-sonnet-4-6

  - name: GPT-4o Direct
    platform: api
    provider: openai
    model: gpt-4o

  - name: GPT-4o + TokenPak
    platform: proxy
    provider: openai
    model: gpt-4o
---

## Turn 1: Design

Design a Python rate limiter class that supports:
- Token bucket algorithm
- Per-key rate limiting (e.g., per user)
- Configurable rate and burst size
- Thread-safe operation

Explain your design choices and show the class interface.

## Turn 2: Implement

Write the full implementation of the rate limiter.
Include proper type hints, docstrings, and thread safety.

## Turn 3: Test

Write pytest tests covering:
- Basic rate limiting (allow then deny)
- Burst handling
- Per-key isolation
- Token refill over time
- Thread safety under concurrent access
