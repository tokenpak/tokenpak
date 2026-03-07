# tokenpak-agents

Unified multi-agent framework integration package for TokenPak.

## Included Integrations
- `tokenpak_agents.crewai`: CrewAI context, handoff, and crew wrappers
- `tokenpak_agents.autogen`: AutoGen message, assistant, and group chat wrappers
- `tokenpak_agents.semantic_kernel`: Semantic Kernel-oriented memory utilities

## Install

```bash
pip install -e .
```

Optional extras:

```bash
pip install -e .[crewai]
pip install -e .[autogen]
pip install -e .[semantic-kernel]
pip install -e .[all]
```

## Quick Usage

```python
from tokenpak_agents.crewai import TokenPakCrew
from tokenpak_agents.autogen import TokenPakAssistant
from tokenpak_agents.semantic_kernel import TokenPakMemory

crew = TokenPakCrew(agents=[], tasks=[], budget=8000)
assistant = TokenPakAssistant(name="agent", budget=4000)
memory = TokenPakMemory(budget=4000)
```
