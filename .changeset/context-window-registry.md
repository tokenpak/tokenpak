---
---

fix(spend-guard): correct stale context-window sizes and make the models
registry the single source for window metadata.

Several current 1M-input-window models were listed at a stale 200K, which
made the derived soft-block threshold fire far earlier than the provider's
published `max_input_tokens` warrants (conservative, but wrong). The window
table now lives in the models registry seed catalog (`context_windows`
section) as the single source of truth, surfaced via
`ModelRegistry.get_max_context()` and a new `ModelInfo.max_input_tokens`
field; the spend-guard module keeps `get_model_max_context()` as a thin
compatibility accessor over the registry.

Value corrections and additions (verified against provider-published
Models API `max_input_tokens`):

- Corrected 200K → 1M: `claude-opus-4-7`, `claude-opus-4-6`,
  `claude-sonnet-4-6`.
- Added current models: `claude-fable-5` (1M), `claude-opus-4-8` (1M),
  `claude-sonnet-5` (1M).
- A trailing `[1m]` long-context tier marker (e.g.
  `claude-sonnet-4-5[1m]`) is now recognized: known base models resolve to
  a window floored at 1M tokens; unknown base models still resolve to
  `None`.

Unknown-model behavior is unchanged: lookups return `None` and the spend
guard falls back to the configured static `block_tokens`, auditing
`threshold_hit=block_tokens_fallback`. No public API symbols were added or
removed.
