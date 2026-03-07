# tokenpak-agents

TokenPak integrations for multi-agent frameworks — CrewAI, AutoGen, and Microsoft Semantic Kernel.

Enable automatic context compression and agent-to-agent handoffs across popular multi-agent platforms.

## Installation

```bash
pip install tokenpak-agents

# Or with specific framework support
pip install tokenpak-agents[crewai]
pip install tokenpak-agents[autogen]
pip install tokenpak-agents[semantic-kernel]
```

## Quick Start

### CrewAI

```python
from crewai import Agent, Task, Crew
from tokenpak_agents.crewai import TokenPakContext, TokenPakCrew

# Create agents with TokenPak context
researcher = Agent(
    role="Researcher",
    goal="Research topics",
    context_manager=TokenPakContext(budget=4000)
)

writer = Agent(
    role="Writer",
    goal="Write summaries",
    context_manager=TokenPakContext(budget=4000)
)

# Create crew with centralized budget
crew = TokenPakCrew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    context_budget=8000,
    compaction_mode="balanced"
)

result = crew.kickoff()
```

### AutoGen

```python
from autogen import UserProxyAgent
from tokenpak_agents.autogen import TokenPakAssistant, TokenPakGroupChat

# Create TokenPak-enabled assistants
researcher = TokenPakAssistant(
    name="researcher",
    llm_config={"model": "gpt-4"},
    context_budget=4000,
)

writer = TokenPakAssistant(
    name="writer",
    llm_config={"model": "gpt-4"},
    context_budget=4000,
)

# Group chat with TokenPak compression
group_chat = TokenPakGroupChat(
    agents=[researcher, writer],
    context_budget=8000,
    handoff_format="tokenpak"
)

user_proxy = UserProxyAgent(name="user")
user_proxy.initiate_chat(researcher, message="Research TokenPak")
```

### Semantic Kernel

```python
from semantic_kernel import Kernel
from tokenpak_agents.semantic_kernel import TokenPakMemory

# Create kernel with TokenPak memory
kernel = Kernel()
memory = TokenPakMemory(
    budget=4000,
    compaction_mode="balanced"
)
kernel.add_memory(memory)

# Memory automatically compressed
memory.save_information("default", "findings", "Important data")
retrieved = memory.retrieve_information("default", "findings")

# Check memory stats
stats = memory.get_stats()
print(f"Used {stats['total_tokens']} of {stats['budget']} tokens")
```

## Features

### CrewAI Integration

- **TokenPakContext** — Per-agent context management with compression
- **TokenPakHandoff** — Automatic task output → input packaging
- **TokenPakCrew** — Crew-level budget coordination

### AutoGen Integration

- **TokenPakAssistant** — ConversableAgent with message compression
- **TokenPakGroupChat** — Group chat with shared context budget
- **TokenPakMessage** — Agent-to-agent TokenPak exchange

### Semantic Kernel Integration

- **TokenPakMemory** — Compressed memory backend for SK kernels
- **Automatic stats** — Track memory usage and token budgets

## Performance

Typical savings across multi-agent frameworks:

| Framework | Context Compression | Inter-Agent Handoff |
|-----------|-------|----------|
| CrewAI | 30-50% | Automatic |
| AutoGen | 25-45% | Message-based |
| Semantic Kernel | 35-55% | Memory-based |

## API Reference

### CrewAI

```python
class TokenPakContext:
    def __init__(budget=4000, compaction_mode="balanced", keep_headers=True)
    def process_agent_context(agent_data) -> dict
    def cache_result(task_id, result)
    def get_context_for_task(task_id) -> dict

class TokenPakHandoff:
    def __init__(format="tokenpak", include_metadata=True)
    def prepare_output(task_result) -> dict
    def prepare_input(handoff_data) -> str

class TokenPakCrew:
    def __init__(agents, tasks, context_budget=8000, compaction_mode="balanced")
    def kickoff(**inputs) -> dict
    async def akickoff(**inputs) -> dict
```

### AutoGen

```python
class TokenPakAssistant:
    def __init__(name, llm_config=None, context_budget=4000, compaction_mode="balanced")
    def receive(message, sender=None)
    def send(message, recipient)

class TokenPakGroupChat:
    def __init__(agents, context_budget=8000, handoff_format="tokenpak", max_messages=50)
    def add_message(agent_name, content)
    def get_history() -> list

class TokenPakMessage:
    def __init__(pack=None, content=None)
    def to_string() -> str
```

### Semantic Kernel

```python
class TokenPakMemory:
    def __init__(budget=4000, compaction_mode="balanced", collection="default")
    def save_information(collection, key, value)
    def retrieve_information(collection, key) -> str
    def get_stats() -> dict
```

## Examples

See the `examples/` directory for complete working examples:

- `crewai_research_writer.py` — Research + writing workflow
- `autogen_group_discussion.py` — Multi-agent group chat
- `semantic_kernel_knowledge_base.py` — Knowledge base with compression

## What is TokenPak?

TokenPak is an open protocol for AI context optimization. It helps:

- **Reduce costs** — Compress context by 30-70%
- **Improve quality** — Preserve recent context intact
- **Scale workflows** — Manage token budgets across agents

Learn more: https://github.com/tokenpak/tokenpak-spec

## Support

- Issues: https://github.com/tokenpak/tokenpak-agents/issues
- Discussions: https://github.com/tokenpak/tokenpak-spec/discussions

## License

MIT
