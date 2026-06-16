---
title: "autogen-tokenpak"
description: "autogen-tokenpak"
status: active
maintainer: tokenpak
created: 2026-03-11
tags: [project]
---
# autogen-tokenpak

TokenPak integration for AutoGen — automatic context compression for multi-agent conversations.

## Installation

```bash
pip install autogen-tokenpak
```

### Dependencies

- `tokenpak>=0.1.0` (required)
- `pyautogen>=0.2.0` (optional, for AutoGen integration examples)

## Quick Start

### Basic Message Compression

Compress conversation history to fit within a token budget:

```python
from autogen_tokenpak import compress_messages

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is Python?"},
    {"role": "assistant", "content": "Python is a programming language..."},
]

# Compress messages to fit within 4000 tokens
compressed = compress_messages(messages, budget=4000)
```

### TokenPakAssistant

Wrap an AutoGen agent to automatically compress message history during conversation:

```python
from autogen_tokenpak import TokenPakAssistant
from autogen import AssistantAgent

agent = AssistantAgent(name="assistant", llm_config={"model": "gpt-4"})
assistant = TokenPakAssistant(agent, budget=6000)

# Use like normal AutoGen agent
assistant.initiate_chat(recipient, message="Hello!")
```

### TokenPakGroupChat

Compress group chat history for multi-agent conversations:

```python
from autogen_tokenpak import TokenPakGroupChat

groupchat = GroupChat(agents=[agent1, agent2], messages=[], ...)
compressed_groupchat = TokenPakGroupChat(groupchat, manager, budget=8000)

# Check compression status
print(compressed_groupchat.budget_status)  # {"budget": 8000, "messages": 42}
compressed = compressed_groupchat.get_compressed_history()
```

## API Reference

### `compress_messages(messages, budget=4000, avg_tokens_per_char=0.25)`

Compress a list of messages to fit within a token budget.

**Parameters:**
- `messages` (List[Dict]): Message list with `role` and `content` keys
- `budget` (int): Maximum tokens to use (default: 4000)
- `avg_tokens_per_char` (float): Token estimation ratio (default: 0.25)

**Returns:** List of compressed messages, preferring recent messages

### `TokenPakMessage`

Wrapper for individual messages with budget constraints.

```python
msg = TokenPakMessage(role="user", content="...", budget=1000)
print(msg.content)       # Truncated to budget
print(msg.token_count)   # Estimated tokens
```

### `TokenPakAssistant`

Wrapper for AutoGen agents with automatic history compression.

**Methods:**
- `compress_history(messages)` — compress message list
- `initiate_chat(recipient, message, **kwargs)` — start conversation
- `generate_reply(messages=None, sender=None, **kwargs)` — generate reply with compression

**Properties:**
- `budget_status` — returns `{"budget": int}`

### `TokenPakGroupChat`

Wrapper for AutoGen GroupChat with compressed history.

**Methods:**
- `get_compressed_history()` — get compressed message history
- `run(initiator, message, **kwargs)` — run group chat

**Properties:**
- `message_count` — number of messages in group chat
- `budget_status` — returns `{"budget": int, "messages": int}`

## Compression Report

All adapters include compression metrics:

```python
assistant = TokenPakAssistant(agent, budget=6000)
# Before: 8000 tokens, After: 5800 tokens → 27.5% compression
```

## Performance Metrics

Relative compression for conversation history (measure your own with `tokenpak savings`):

- **Single agent (1000 message depth):** moderate compression
- **Group chat (500+ message history):** moderate–high compression
- **Tool/function definitions:** low–moderate compression (schema deduplication)

## Design Notes

- **Framework-agnostic:** Uses standard message dictionaries
- **Optional integration:** AutoGen workflows work with or without TokenPak
- **Deterministic compression:** Same input → same output (suitable for testing)
- **Zero breaking changes:** Inherits AutoGen API surface unchanged

## License

Apache-2.0

## References

- [TokenPak Specification](https://github.com/tokenpak/tokenpak-spec)
- [AutoGen Documentation](https://microsoft.github.io/autogen/)
