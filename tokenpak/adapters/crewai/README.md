---
title: "crewai-tokenpak"
description: "crewai-tokenpak"
status: active
owner: Kevin
created: 2026-03-11
tags: [project]
---
# crewai-tokenpak

TokenPak integration for CrewAI.

## Installation
```
pip install crewai-tokenpak
```

## Quick Start
```python
from crewai_tokenpak import TokenPakCrew, TokenPakContext
ctx = TokenPakContext(total_budget=8000)
ctx.register_agent("researcher", budget=4000)
```

See: https://github.com/tokenpak/tokenpak-spec
