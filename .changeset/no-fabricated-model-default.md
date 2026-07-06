---
---

Proxy: stop defaulting a missing model id to a real model name in logging,
forecast, streaming, and spend-guard estimation paths. When a request or
backend response carries no model, it is now recorded as empty ("unknown"):
cost is still estimated against default-class rates, but is never attributed
to a fabricated model id, and the Claude Code CLI backend falls back to its
own configured default model instead of a hardcoded one.
