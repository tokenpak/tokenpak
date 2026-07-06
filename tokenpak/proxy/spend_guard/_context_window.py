# SPDX-License-Identifier: Apache-2.0
"""Model max-context-window lookup for spend-guard threshold derivation.

The spend guard's soft-block threshold derives dynamically from the selected
model's context window (default 80% of max). When a model's context window
is unknown, the caller falls back to the configured static fallback in
``SpendGuardConfig.block_tokens`` rather than silently assuming a large
window.

The window values themselves live in the :mod:`tokenpak.models` registry
(seed catalog ``context_windows`` section) — the single source of truth for
model metadata. Each entry is verified against the provider's published
Models API ``max_input_tokens``; when the provider publishes no number for
a model, the model is omitted so the spend guard falls back to
``cfg.block_tokens`` and audits ``threshold_hit=block_tokens_fallback``.
This module remains as a thin compatibility accessor for existing
importers of :func:`get_model_max_context`.
"""

from __future__ import annotations

from tokenpak.models import get_registry


def get_model_max_context(model_id: str | None) -> int | None:
    """Resolve the max context window in tokens for a model id.

    Returns ``None`` when the model is unknown — the caller is responsible
    for falling back to the configured static threshold rather than silently
    assuming a default.

    Matching strategy (case-insensitive; see
    :meth:`tokenpak.models.ModelRegistry.get_max_context`):

    1. A trailing ``[1m]`` long-context tier marker is honored: the base
       model must be known, and the window is floored at 1,000,000 tokens.
    2. Exact match on ``model_id`` (lowercased, trimmed).
    3. Strip provider prefix (``anthropic/``, ``openai/``, ``google/``,
       etc.) and retry exact match.
    4. Strip trailing 8-digit date suffix and retry exact match.
    5. Longest-prefix match against the registry keys.

    Examples::

        get_model_max_context("claude-opus-4-8")             # → 1_000_000
        get_model_max_context("anthropic/claude-opus-4-8")   # → 1_000_000
        get_model_max_context("claude-sonnet-4-5")           # → 200_000
        get_model_max_context("claude-sonnet-4-5[1m]")       # → 1_000_000
        get_model_max_context("gpt-4.1")                     # → 1_047_576
        get_model_max_context("gemini-1.5-pro")              # → 2_000_000
        get_model_max_context("unknown-frontier-model")      # → None
        get_model_max_context(None)                          # → None
    """
    return get_registry().get_max_context(model_id)


def known_models() -> list[str]:
    """Return the context-window table's model-id keys (sorted). For
    diagnostics."""
    return get_registry().context_window_models()
