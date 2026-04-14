---
title: "llamaindex-tokenpak"
description: "llamaindex-tokenpak"
status: active
maintainer: tokenpak
created: 2026-03-11
tags: [project]
---
# llamaindex-tokenpak

TokenPak integration for LlamaIndex.

## Installation
```
pip install llamaindex-tokenpak
```

## Quick Start
```python
from llamaindex_tokenpak import TokenPakIndex
tp_index = TokenPakIndex(index=your_index, default_budget=4000)
engine = tp_index.as_query_engine()
```

See: https://github.com/tokenpak/tokenpak-spec
