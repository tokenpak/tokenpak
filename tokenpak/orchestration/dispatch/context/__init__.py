"""TokenPak Dispatch — context assembly (Standards Delta v0 §5.9).

This subpackage hosts the ``ContextProvider`` interface plus the two
implementations the Standards Delta describes:

* :class:`LocalContextProvider` — the OSS v0.1-alpha provider. Deterministic,
  no LLM, no network, no Pro-tier Pak dependency. It assembles a
  :class:`ContextBundle` from explicit manifest files, Route/Station-declared
  files, a simple repo scan, the current task frontmatter, and manually
  attached items — under per-station size and token budgets, with
  gitignore-aware path filtering.
* :class:`PaidContextProvider` — a stub that raises ``NotImplementedError``.
  Its sole job in v0.1-alpha is to make the interface boundary visible from day
  one (the Pro-tier boundary): the Pro path is a later *swap* of the provider
  instance, not a rewrite (Standards Delta v0 §5.9 "Pro path").

The token budget inherits the Spend Guard cap. In v0.1-alpha that cap is
modelled as an injected config value on the provider (``token_budget`` /
``ContextBudget.token_budget``) with a sane default; the runtime wires the live
cap from Spend Guard through TIP (Standards Delta v0 §8). This module performs
no Spend Guard enforcement itself — it only honors the budget value it is
given.
"""

from __future__ import annotations

from tokenpak.orchestration.dispatch.context.provider import (
    DEFAULT_SIZE_BUDGET_BYTES,
    DEFAULT_TOKEN_BUDGET,
    ContextBudget,
    ContextBundle,
    ContextFile,
    ContextProvider,
    ContextSource,
    LocalContextProvider,
    PaidContextProvider,
    SkippedItem,
)

__all__ = [
    "ContextProvider",
    "LocalContextProvider",
    "PaidContextProvider",
    "ContextBundle",
    "ContextFile",
    "ContextSource",
    "SkippedItem",
    "ContextBudget",
    "DEFAULT_SIZE_BUDGET_BYTES",
    "DEFAULT_TOKEN_BUDGET",
]
