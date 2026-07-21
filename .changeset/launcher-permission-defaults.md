---
---

feat(cli): add per-client launcher permission defaults for Codex and Claude Code.

- Persist launcher-only `inherit` and `full-bypass` modes per client, plus
  Codex `approval-bypass` and `sandbox-bypass`, without modifying either
  client's configuration.
- Require explicit confirmation for bypass modes and warn at configuration,
  inspection, doctor, and launch time, including effective composed risk.
- Preserve the legacy `fleet` full-bypass alias while rejecting narrower client
  scopes that would otherwise be broadened silently.
- Fail closed on malformed, unsupported, or future launcher-state schemas.
