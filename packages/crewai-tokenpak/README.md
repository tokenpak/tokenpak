# crewai-tokenpak

TokenPak integration for CrewAI — automatic context compression for multi-agent systems.

Reduces token costs across agent communications by 30-50%.

[![PyPI version](https://img.shields.io/pypi/v/crewai-tokenpak)](https://pypi.org/project/crewai-tokenpak/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Installation

```bash
pip install crewai-tokenpak
```

---

## Quick Start

### Crew with automatic compression

```python
from crewai_tokenpak import TokenPakCrew

crew = TokenPakCrew(
    agents=[researcher, writer, reviewer],
    tasks=[research_task, write_task, review_task],
    budget=8000,   # total token budget for the crew
)

result = crew.kickoff()
```

### Coordinate budgets across agents

```python
from crewai_tokenpak import TokenPakContext

ctx = TokenPakContext(
    total_budget=8000,
    per_agent_budget=2000,  # override per-agent limit
)

# Allocate budget for a specific agent
budget = ctx.allocate_budget("researcher")

# Track actual usage
ctx.record_usage("researcher", tokens_used=1500)
usage = ctx.get_usage()
# {"researcher": 1500}
```

### Compress context during agent handoffs

```python
from crewai_tokenpak import TokenPakHandoff

handoff = TokenPakHandoff(budget=2000)

# Agent A prepares handoff package
wire = handoff.prepare_handoff(
    state={"findings": "..."},
    from_agent="researcher",
    to_agent="writer",
    what_was_done="Analyzed 50 documents on topic X",
    whats_next="Write executive summary",
)

# Agent B receives compressed context
context = handoff.receive_handoff_wire(wire)
```

---

## What is TokenPak?

TokenPak is an open protocol for AI context optimization. It compresses context blocks to fit within token budgets while keeping the highest-priority content intact.

Learn more: https://github.com/kaywhy331/tokenpak

---

## API Reference

### `TokenPakCrew`

```python
class TokenPakCrew:
    def __init__(
        self,
        agents: List[Any],
        tasks: List[Any],
        budget: int = 8000,      # total token budget
        **kwargs,
    ) -> None: ...

    def kickoff(self) -> Any: ...
```

### `TokenPakContext`

```python
class TokenPakContext:
    def __init__(
        self,
        total_budget: int = 8000,
        per_agent_budget: Optional[int] = None,  # defaults to total/4
    ) -> None: ...

    def allocate_budget(self, agent_id: str) -> int: ...
    def record_usage(self, agent_id: str, tokens_used: int) -> None: ...
    def get_usage(self) -> Dict[str, int]: ...
```

### `TokenPakHandoff`

```python
class TokenPakHandoff:
    def __init__(
        self,
        budget: int = 2000,
        keep_recent: int = 10,
    ) -> None: ...

    def prepare_handoff(
        self,
        state: Dict[str, Any],
        from_agent: str,
        to_agent: str,
        what_was_done: str,
        whats_next: str,
    ) -> "HandoffWire": ...

    def receive_handoff_wire(self, wire: "HandoffWire") -> Dict[str, Any]: ...
```

---

## Performance

Typical savings in multi-agent workflows:

| Workflow Type         | Savings | Notes                        |
|-----------------------|---------|------------------------------|
| Research → Write      | 35-50%  | Compresses research output   |
| Multi-step pipelines  | 40-55%  | Compounds across handoffs    |
| Group coordination    | 30-45%  | Shared context compression   |

---

## Support

- Issues: https://github.com/kaywhy331/tokenpak/issues
- Discussions: https://github.com/kaywhy331/tokenpak/discussions

## License

MIT
