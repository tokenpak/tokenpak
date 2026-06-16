# crewai-tokenpak

TokenPak integration for CrewAI with a minimal hook that intercepts crew context assembly and compresses task context before it reaches each agent.

## Installation

```bash
pip install crewai-tokenpak
```

## Quick Start

```python
from crewai import Agent, Crew, Task
from crewai_tokenpak import AgentContextConfig, TokenPakCrewAIHook

researcher = Agent(role="researcher", goal="Find the best evidence", backstory="...")
writer = Agent(role="writer", goal="Draft the answer", backstory="...")

research_task = Task(
    description="Research the topic from prior notes",
    expected_output="Key findings",
    agent=researcher,
)
write_task = Task(
    description="Write the final brief",
    expected_output="Executive summary",
    agent=writer,
    context=[research_task],
)

crew = Crew(agents=[researcher, writer], tasks=[research_task, write_task])
hook = TokenPakCrewAIHook(
    total_budget=4000,
    shared_context="Company style guide: concise, source-backed, no filler.",
    task_context={"Write the final brief": "Audience: executive staff."},
    agent_overrides={
        "writer": AgentContextConfig(budget=900, prefix="Focus on synthesis.")
    },
)
hook.apply_to_crew(crew)

result = crew.kickoff()
print(result)
print(hook.compression_report())
```

## API Reference

### `TokenPakCrewAIHook`

Primary integration point. Patches one crew instance by replacing its `_get_context(...)` method, then:

- collects shared crew context
- resolves task-level context dependencies
- injects optional task-specific context
- applies optional per-agent budget overrides
- compresses the final assembled context deterministically
- stores a compression report per task

### `TokenPakCrew`

Thin wrapper that applies `TokenPakCrewAIHook` before delegating to `crew.kickoff()` or `crew.akickoff()`.

### `TokenPakContext`

Budget allocator and deterministic compressor used by the hook. It does not require the `crewai` package at import time.

### `AgentContextConfig`

Per-agent override object with:

- `budget`: custom compression budget for that agent
- `prefix`: agent-specific text injected before assembled crew context
- `suffix`: agent-specific text injected after assembled crew context

## Compression Report Example

```python
{
    "tasks": 2,
    "original_tokens": 1120,
    "compressed_tokens": 540,
    "saved_tokens": 580,
    "agents": ["researcher", "writer"],
}
```

Each task also records a `TokenPakCompressionReport` with per-task budgets, token counts, and which context sections were injected.

## Design Notes

- Mirrors the lightweight adapter style used by `langchain-tokenpak`.
- Keeps `crewai` imports lazy so the package remains importable in restricted environments.
- Uses a simple deterministic compressor for stable tests and predictable behavior.

## License

Apache-2.0
