# autogen-tokenpak

TokenPak integration for Microsoft AutoGen — automatic context compression for multi-agent conversations.

## Features

- **Automatic Context Compression**: Reduces token usage in AutoGen conversations without API changes
- **Multi-Agent Support**: Compress system prompts, conversation history, and tool definitions across agents
- **Transparent Integration**: Integrates via method patching — works with existing AutoGen workflows
- **Compression Reports**: Get detailed metrics on compression applied
- **Type-Safe**: Full mypy strict mode compliance

## Installation

```bash
pip install autogen-tokenpak
```

## Quick Start

```python
from autogen_tokenpak import TokenPakConversationHook
from autogen import UserProxyAgent, AssistantAgent

# Initialize compression hook
hook = TokenPakConversationHook()

# Create your AutoGen agents
user = UserProxyAgent("user")
assistant = AssistantAgent(
    "assistant",
    llm_config={
        "config_list": [{"model": "gpt-4", "api_key": "..."}]
    }
)

# Apply compression
hook.compress_agent(assistant)

# Use agents normally — compression applied automatically
user.initiate_chat(
    assistant,
    message="Help me solve this problem..."
)

# Get compression report
report = hook.get_report("assistant")
print(report)
```

## Multi-Agent Workflow

```python
from autogen_tokenpak import (
    TokenPakConversationHook,
    AgentContextConfig,
)

hook = TokenPakConversationHook()

# Create agents for a workflow
researcher = AssistantAgent("researcher", llm_config={...})
writer = AssistantAgent("writer", llm_config={...})
reviewer = AssistantAgent("reviewer", llm_config={...})

# Apply compression to all agents
for agent in [researcher, writer, reviewer]:
    hook.compress_agent(agent)

# Or customize per-agent compression
researcher_config = AgentContextConfig(
    max_tokens=4096,
    preserve_recent_messages=5,
)
hook.compress_agent(researcher, researcher_config)

# Run your workflow...
```

## Configuration

### Default Configuration

```python
hook.compress_agent(agent)  # Uses defaults
```

Defaults:
- `max_tokens`: 4096
- `preserve_recent_messages`: 5
- `compress_system_prompt`: True
- `compress_tools`: True
- `compress_history`: True

### Custom Configuration

```python
from autogen_tokenpak import AgentContextConfig

config = AgentContextConfig(
    max_tokens=2048,
    preserve_recent_messages=3,
    compress_system_prompt=True,
    compress_tools=True,
    compress_history=True,
)

hook.compress_agent(agent, config)
```

## API Reference

### `TokenPakConversationHook`

Main hook class for AutoGen compression.

#### Methods

- **`compress_agent(agent, config=None)`**: Patch an agent to apply compression
  - `agent`: AutoGen agent instance
  - `config`: Optional `AgentContextConfig`

- **`restore_agent(agent)`**: Remove compression hook from an agent
  - `agent`: AutoGen agent instance

- **`get_report(agent_name)`**: Get compression metrics for an agent
  - `agent_name`: Name of the agent
  - Returns: `TokenPakCompressionReport` or None

### `TokenPakCompressionReport`

Compression metrics report.

#### Attributes

- `agent_name`: Name of the agent
- `original_tokens`: Estimated tokens before compression
- `compressed_tokens`: Estimated tokens after compression
- `compression_ratio`: Fraction of tokens saved
- `messages_compressed`: Number of messages compressed
- `tools_compressed`: Number of tools compressed
- `system_prompt_length`: Length of system prompt after compression

#### Methods

- **`to_dict()`**: Convert report to dictionary
- **`__str__()`**: Human-readable report string

### `AgentContextConfig`

Per-agent compression configuration.

#### Attributes

- `max_tokens`: Maximum tokens for compressed context (default: 4096)
- `preserve_recent_messages`: Number of recent messages to keep uncompressed (default: 5)
- `compress_system_prompt`: Compress system prompt (default: True)
- `compress_tools`: Compress tool schemas (default: True)
- `compress_history`: Compress message history (default: True)

## Compression Strategy

1. **System Prompt**: Normalize whitespace, remove redundant text
2. **Message History**: Preserve recent messages; compress older ones
3. **Tool Schemas**: Normalize descriptions and parameter docs
4. **Deduplication**: Remove duplicate definitions and instructions

## Example: Compression Report

```
TokenPak Compression Report (assistant)
  Original tokens: 8542
  Compressed tokens: 5123
  Compression ratio: 40.09%
  Messages compressed: 12
  Tools compressed: 3
  System prompt length: 245
```

## Design Notes

- Follows the same integration pattern as `crewai-tokenpak`
- Lazy imports for constrained environments
- Deterministic compression for reproducible results
- Zero breaking changes to AutoGen API
- Hook-based patching: works with all AutoGen agent types

## Testing

```bash
pytest tests/ -v
```

Coverage:

```bash
pytest --cov=autogen_tokenpak --cov-report=term-missing tests/
```

## License

MIT

## Support

For issues, questions, or contributions:
- GitHub: https://github.com/tokenpak/autogen-tokenpak
- Docs: https://tokenpak.dev/integrations/autogen
