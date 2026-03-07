# crewai-tokenpak

TokenPak integration for CrewAI — automatic context compression for multi-agent systems.

Reduces token costs across agent communications by 30-50%.

## Installation

```bash
pip install crewai-tokenpak
```

## Quick Start

```python
from crewai_tokenpak import TokenPakCrew

crew = TokenPakCrew(
    agents=[agent1, agent2, agent3],
    tasks=[task1, task2, task3],
    budget=8000,  # total token budget for crew
)

result = crew.kickoff()
```

## Features

- **TokenPakCrew**: CrewAI crew with automatic compression
- **TokenPakContext**: Manage budgets across agents
- **TokenPakHandoff**: Compress context during agent handoffs

## Documentation

See [full documentation](https://tokenpak.dev/integrations/crewai).

## License

MIT
