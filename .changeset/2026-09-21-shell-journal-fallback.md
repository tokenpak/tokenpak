---
"tokenpak": patch
---

Record Claude prompt submissions in fresh companion journals and when the external
sqlite3 executable is unavailable. The fallback uses the managed launcher's Python
runtime and queues content-free metadata without adding provider usage or costs.
Existing session metadata is preserved, and configured shell-hook budgets retain
their refusal behavior when sqlite3 is missing.
