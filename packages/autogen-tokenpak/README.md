# autogen-tokenpak

TokenPak integration for AutoGen — automatic context compression for multi-agent conversations.

Reduces token costs in group chats by 30-50%.

[![PyPI version](https://img.shields.io/pypi/v/autogen-tokenpak)](https://pypi.org/project/autogen-tokenpak/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Installation

```bash
pip install autogen-tokenpak
```

---

## Quick Start

### Assistant with compression

```python
from autogen_tokenpak import TokenPakAssistant, TokenPakGroupChat

# Create agents with token budgets
researcher = TokenPakAssistant(name="researcher", budget=4000)
writer     = TokenPakAssistant(name="writer",     budget=4000)

# Group chat compresses history as it grows
group = TokenPakGroupChat(
    agents=[researcher, writer],
    budget=8000,
)

researcher.receive_message("Analyze this dataset: ...", sender_name="user")
messages = researcher.get_messages()   # auto-compressed to fit budget
```

### Agent-to-agent handoffs

```python
from autogen_tokenpak import TokenPakAssistant

alice = TokenPakAssistant(name="alice", budget=4000)
bob   = TokenPakAssistant(name="bob",   budget=4000)

# Alice does work, then hands off compressed context
alice.receive_message("Research topic X", sender_name="user")
wire = alice.prepare_handoff(
    to_agent="bob",
    what_was_done="Researched topic X across 20 documents",
    whats_next="Summarize findings into a report",
)

# Bob receives compressed, structured context
bob.apply_handoff_wire(wire)
messages = bob.get_messages()
```

### Compress individual messages

```python
from autogen_tokenpak import TokenPakMessage

msg = TokenPakMessage(
    content="Very long document...",
    budget=500,
    role="user",
)

compressed = msg.compress()
print(f"Saved {msg.original_tokens - msg.compressed_tokens} tokens")
```

---

## What is TokenPak?

TokenPak is an open protocol for AI context optimization. It compresses context blocks to fit within token budgets while keeping the highest-priority content intact.

Learn more: https://github.com/kaywhy331/tokenpak

---

## API Reference

### `TokenPakAssistant`

```python
class TokenPakAssistant:
    def __init__(
        self,
        name: str,
        budget: int = 4000,
        **kwargs,
    ) -> None: ...

    def receive_message(
        self,
        message: str,
        sender: Any = None,
        sender_name: str = "",
    ) -> None: ...

    def get_messages(self, compress: bool = True) -> List[Dict[str, Any]]: ...

    def prepare_handoff(
        self,
        to_agent: str,
        what_was_done: str,
        whats_next: str,
    ) -> "HandoffWire": ...

    def apply_handoff_wire(self, wire: "HandoffWire") -> None: ...
```

### `TokenPakGroupChat`

```python
class TokenPakGroupChat:
    def __init__(
        self,
        agents: List[Any],
        budget: int = 8000,
        **kwargs,
    ) -> None: ...

    def add_message(self, message: Dict[str, Any]) -> None: ...
    # messages auto-compressed when over budget
    messages: List[Dict[str, Any]]
```

### `TokenPakMessage`

```python
class TokenPakMessage:
    def __init__(
        self,
        content: str,
        budget: int = 500,
        role: str = "user",
    ) -> None: ...

    def compress(self) -> str: ...
    original_tokens: int
    compressed_tokens: int
```

---

## Performance

Typical savings in AutoGen workflows:

| Conversation Length | Savings | Notes                         |
|---------------------|---------|-------------------------------|
| Short (< 10 turns)  | 10-20%  | Most content preserved        |
| Medium (10-30 turns)| 30-40%  | Older turns compressed        |
| Long (30+ turns)    | 45-60%  | Aggressive history compression|

---

## Support

- Issues: https://github.com/kaywhy331/tokenpak/issues
- Discussions: https://github.com/kaywhy331/tokenpak/discussions

## License

MIT
