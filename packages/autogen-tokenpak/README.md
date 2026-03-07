# autogen-tokenpak

TokenPak integration for AutoGen — automatic context compression for multi-agent conversations.

Reduces token costs in group chats by 30-50%.

## Installation

```bash
pip install autogen-tokenpak
```

## Quick Start

```python
from autogen_tokenpak import TokenPakAssistant, TokenPakGroupChat

assistant = TokenPakAssistant(
    name="assistant",
    budget=4000,
)

group = TokenPakGroupChat(
    agents=[assistant, user_proxy],
    budget=8000,
)

user_proxy.initiate_chat(
    assistant,
    message="Let's solve this problem.",
)
```

## Features

- **TokenPakAssistant**: ConversableAgent with compression
- **TokenPakGroupChat**: Group chat with message compression
- **TokenPakMessage**: Message compression utilities

## Documentation

See [full documentation](https://tokenpak.dev/integrations/autogen).

## License

MIT
