---
"tokenpak": patch
---

Fix Claude companion bash pre-send accounting so it uses model-aware input-rate snapshots when available, fails closed when a configured budget cannot be evaluated because sqlite3 is unavailable, and writes best-effort auto journal rows when the journal database is present.
