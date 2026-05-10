# SPDX-License-Identifier: Apache-2.0
"""Model max-context-window registry for spend-guard threshold derivation.

The spend guard's soft-block threshold derives dynamically from the selected
model's context window (default 80% of max). When a model's context window
is unknown, the caller falls back to the configured static fallback in
``SpendGuardConfig.block_tokens`` rather than silently assuming a large
window.

This registry is intentionally narrow: it lists the frontier-class models
whose context window matters for blocking large-batch traffic at the proxy
seam (Anthropic Claude, OpenAI GPT/o-series, Google Gemini). Local-model
context windows (Llama, Mistral, Phi, Gemma, Qwen, etc.) are handled by
``tokenpak.sdk.local.auto_budget.MODEL_CONTEXT_LENGTHS`` for SDK budgeting
and are not duplicated here.

When in doubt, omit a model from this table — the spend guard will fall
back to ``cfg.block_tokens`` and audit ``threshold_hit=block_tokens_fallback``
so the operator can see that dynamic derivation was unavailable for the
unknown model.
"""

from __future__ import annotations

import re

# Max context windows, in tokens, for known frontier models.
# Verify against the provider's published model card before adding entries.
# Keys are matched case-insensitively against ``model_id`` after stripping
# any provider prefix (``anthropic/claude-…``) and trailing date suffix
# (``-20261015``); see :func:`get_model_max_context`.
_MODEL_MAX_CONTEXT: dict[str, int] = {
    # Anthropic Claude — all current frontier and 3.x lines: 200K
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,

    # OpenAI — GPT and o-series. Verify per release; the 4.1 family ships
    # with a 1M-effective window on the API.
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4.5": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,

    # Google Gemini — 1.5/2.0/2.5 lines. Pro ships at 2M effective; flash at 1M.
    "gemini-2.5-pro": 2_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.0-pro": 2_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
}

# Date suffixes like ``-20261015`` or ``-20241022`` are stripped before lookup.
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")


def get_model_max_context(model_id: str | None) -> int | None:
    """Resolve the max context window in tokens for a model id.

    Returns ``None`` when the model is unknown — the caller is responsible
    for falling back to the configured static threshold rather than silently
    assuming a default.

    Matching strategy (case-insensitive):

    1. Exact match on ``model_id`` (lowercased, trimmed).
    2. Strip provider prefix (``anthropic/``, ``openai/``, ``google/``,
       etc.) and retry exact match.
    3. Strip trailing 8-digit date suffix and retry exact match.
    4. Longest-prefix match against the registry keys.

    Examples::

        get_model_max_context("claude-opus-4-7")             # → 200_000
        get_model_max_context("anthropic/claude-opus-4-7")   # → 200_000
        get_model_max_context("claude-opus-4-7-20261015")    # → 200_000
        get_model_max_context("gpt-4.1")                     # → 1_047_576
        get_model_max_context("gemini-1.5-pro")              # → 2_000_000
        get_model_max_context("unknown-frontier-model")      # → None
        get_model_max_context(None)                          # → None
    """
    if not model_id:
        return None

    m = model_id.lower().strip()
    if not m:
        return None

    # 1. Exact match.
    if m in _MODEL_MAX_CONTEXT:
        return _MODEL_MAX_CONTEXT[m]

    # 2. Strip provider prefix.
    if "/" in m:
        suffix = m.split("/", 1)[1]
        if suffix in _MODEL_MAX_CONTEXT:
            return _MODEL_MAX_CONTEXT[suffix]
        m = suffix

    # 3. Strip date suffix.
    stripped = _DATE_SUFFIX_RE.sub("", m)
    if stripped != m and stripped in _MODEL_MAX_CONTEXT:
        return _MODEL_MAX_CONTEXT[stripped]

    # 4. Longest-prefix match.
    best_key: str | None = None
    best_len = 0
    for key in _MODEL_MAX_CONTEXT:
        if (m.startswith(key) or stripped.startswith(key)) and len(key) > best_len:
            best_key = key
            best_len = len(key)
    if best_key is not None:
        return _MODEL_MAX_CONTEXT[best_key]

    return None


def known_models() -> list[str]:
    """Return the registry's model-id keys (sorted). For diagnostics."""
    return sorted(_MODEL_MAX_CONTEXT.keys())
