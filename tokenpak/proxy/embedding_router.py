"""
Content-aware embedding router.

Selects the optimal embedding provider based on input characteristics.

Routing strategies (TOKENPAK_EMBEDDING_ROUTING_STRATEGY env var, default: auto):
    auto        — apply all rules in priority order
    fast        — always use the fast provider (short-text-optimised)
    quality     — always use the quality provider (long-text-optimised)
    passthrough — return the first available provider without analysis

Routing rules applied in ``auto`` mode (highest priority first):
    1. model-preference  — explicit model_hint overrides everything
    2. text-length       — short text (≤512 chars) → fast provider,
                           long text (>512 chars)  → quality provider
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider name constants (mirror source_format values in the adapter layer)
# ---------------------------------------------------------------------------

PROVIDER_VOYAGE = "voyage-embeddings"
PROVIDER_OPENAI = "openai-embeddings"
PROVIDER_GEMINI = "gemini-embeddings"
PROVIDER_JINA = "jina-embeddings"
PROVIDER_OLLAMA = "ollama-embeddings"

# Ordered by quality / capability (highest first).  Used as the default
# priority list when env vars are not explicitly set.
_DEFAULT_PROVIDERS: List[str] = [
    PROVIDER_VOYAGE,
    PROVIDER_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_JINA,
    PROVIDER_OLLAMA,
]

# Providers preferred for short / fast requests
_FAST_PROVIDERS: List[str] = [
    PROVIDER_OPENAI,
    PROVIDER_JINA,
    PROVIDER_OLLAMA,
    PROVIDER_GEMINI,
    PROVIDER_VOYAGE,
]

# Providers preferred for long / quality requests
_QUALITY_PROVIDERS: List[str] = [
    PROVIDER_VOYAGE,
    PROVIDER_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_JINA,
    PROVIDER_OLLAMA,
]

# Character-count threshold separating "short" from "long" text.
_LENGTH_THRESHOLD = int(os.environ.get("TOKENPAK_EMBEDDING_LENGTH_THRESHOLD", "512"))

# Model-prefix → canonical provider name lookup
_MODEL_PREFIX_MAP: List[tuple] = [
    ("voyage-", PROVIDER_VOYAGE),
    ("text-embedding-", PROVIDER_OPENAI),
    ("models/text-embedding", PROVIDER_GEMINI),
    ("jina-", PROVIDER_JINA),
    ("nomic-", PROVIDER_OLLAMA),
    ("mxbai-", PROVIDER_OLLAMA),
    ("all-minilm", PROVIDER_OLLAMA),
    ("snowflake-", PROVIDER_OLLAMA),
]

# ---------------------------------------------------------------------------
# Routing rules
# ---------------------------------------------------------------------------


def _rule_model_preference(
    model_hint: Optional[str],
    available: List[str],
) -> Optional[str]:
    """Return the provider for *model_hint* if it matches a known prefix.

    Returns None when no match found or model_hint is None / empty.
    """
    if not model_hint:
        return None

    hint_lower = model_hint.lower()
    for prefix, provider in _MODEL_PREFIX_MAP:
        if hint_lower.startswith(prefix):
            if provider in available:
                logger.debug(
                    "content-aware router: model_hint=%r → %s", model_hint, provider
                )
                return provider
            logger.debug(
                "content-aware router: model_hint=%r matched %s but provider not available",
                model_hint,
                provider,
            )
            return None

    return None


def _rule_text_length(
    text: str,
    available: List[str],
) -> Optional[str]:
    """Return a provider based on text length.

    Short text (≤ threshold chars) → prefer a fast provider.
    Long text  (> threshold chars) → prefer a quality provider.
    """
    if len(text) <= _LENGTH_THRESHOLD:
        candidates = _FAST_PROVIDERS
        label = "short"
    else:
        candidates = _QUALITY_PROVIDERS
        label = "long"

    for provider in candidates:
        if provider in available:
            logger.debug(
                "content-aware router: text length=%d (%s) → %s",
                len(text),
                label,
                provider,
            )
            return provider

    return None


def _first_available(available: List[str], ordered: List[str]) -> Optional[str]:
    for p in ordered:
        if p in available:
            return p
    return available[0] if available else None


# ---------------------------------------------------------------------------
# ContentAwareRouter
# ---------------------------------------------------------------------------


class ContentAwareRouter:
    """Route embedding requests to the best available provider.

    Parameters
    ----------
    available_providers:
        Ordered list of provider name strings that are currently reachable.
        Defaults to the module-level ``_DEFAULT_PROVIDERS`` list (all known
        providers, useful for unit tests that do not need real API keys).
    strategy:
        Overrides ``TOKENPAK_EMBEDDING_ROUTING_STRATEGY`` env var when
        supplied.  One of ``"auto"``, ``"fast"``, ``"quality"``,
        ``"passthrough"``.
    """

    def __init__(
        self,
        available_providers: Optional[List[str]] = None,
        strategy: Optional[str] = None,
    ) -> None:
        self.available_providers: List[str] = (
            list(available_providers) if available_providers is not None
            else list(_DEFAULT_PROVIDERS)
        )
        self._strategy = strategy or os.environ.get(
            "TOKENPAK_EMBEDDING_ROUTING_STRATEGY", "auto"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def strategy(self) -> str:
        return self._strategy

    def route(
        self,
        input_text: str,
        model_hint: Optional[str] = None,
    ) -> str:
        """Return the name of the best provider for this request.

        Parameters
        ----------
        input_text:
            The text to be embedded.  May be an empty string.
        model_hint:
            Optional explicit model name (e.g. ``"voyage-3.5"``).  When
            supplied and recognised, the matching provider is returned
            regardless of other heuristics.

        Returns
        -------
        str
            A provider name from the available providers list.

        Raises
        ------
        RuntimeError
            If no providers are available.
        """
        if not self.available_providers:
            raise RuntimeError(
                "ContentAwareRouter: no embedding providers are available."
            )

        strategy = self._strategy

        if strategy == "fast":
            result = _first_available(self.available_providers, _FAST_PROVIDERS)
        elif strategy == "quality":
            result = _first_available(self.available_providers, _QUALITY_PROVIDERS)
        elif strategy == "passthrough":
            result = self.available_providers[0]
        else:
            # "auto" — apply rules in priority order
            result = (
                _rule_model_preference(model_hint, self.available_providers)
                or _rule_text_length(input_text, self.available_providers)
                or self.available_providers[0]
            )

        logger.debug(
            "content-aware router: strategy=%s model_hint=%r text_len=%d → %s",
            strategy,
            model_hint,
            len(input_text),
            result,
        )
        return result
