"""Versioned pricing catalog and cost computation for TokenPak telemetry.

The catalog is loaded from ``data/pricing_catalog.json`` (co-located with
this package).  All pricing is expressed in USD per 1,000,000 tokens.

Usage::

    from tokenpak.telemetry.pricing import PricingCatalog
    from tokenpak.telemetry.models import Cost

    catalog = PricingCatalog.load()
    cost = catalog.compute_cost(
        trace_id="abc-123",
        model="claude-sonnet-4-6",
        baseline_input_tokens=50_000,
        actual_input_tokens=32_000,
        output_tokens=2_000,
        cache_read=15_000,
    )
    # cost.actual_cost, cost.savings_total

Anthropic cache multipliers (when not overridden by catalog entry):
    cache_read  = 0.10 × input price per token
    cache_write = 1.25 × input price per token
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from tokenpak.telemetry.models import Cost

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default Anthropic cache multipliers
# ---------------------------------------------------------------------------

_ANTHROPIC_CACHE_READ_MULT: float = 0.10
_ANTHROPIC_CACHE_WRITE_MULT: float = 1.25

_CATALOG_PATH = Path(__file__).parent / "data" / "pricing_catalog.json"


def _per_token(per_million: float) -> float:
    """Convert a per-1M price to a per-token price."""
    return per_million / 1_000_000.0


class ModelPricing:
    """Pricing record for a single model.

    Parameters
    ----------
    model:
        Model identifier (as it appears in the catalog).
    provider:
        Provider name (``"anthropic"``, ``"openai"``, ``"gemini"``).
    input_per_token:
        USD cost per input token.
    output_per_token:
        USD cost per output token.
    cache_read_per_token:
        USD cost per cache-read token (``None`` if caching not supported).
    cache_write_per_token:
        USD cost per cache-write token (``None`` if caching not supported).
    """

    def __init__(
        self,
        model: str,
        provider: str,
        input_per_token: float,
        output_per_token: float,
        cache_read_per_token: Optional[float],
        cache_write_per_token: Optional[float],
    ) -> None:
        self.model = model
        self.provider = provider
        self.input_per_token = input_per_token
        self.output_per_token = output_per_token
        self.cache_read_per_token = cache_read_per_token
        self.cache_write_per_token = cache_write_per_token

    @classmethod
    def from_dict(cls, model: str, data: dict[str, Any]) -> "ModelPricing":
        """Construct from a catalog ``models`` entry dict.

        ``cache_read`` / ``cache_write`` may be ``None`` (not supported) or
        a float value in USD per 1M tokens.
        """
        provider = data.get("provider", "unknown")
        input_pm = float(data.get("input", 0.0))
        output_pm = float(data.get("output", 0.0))

        cr_pm = data.get("cache_read")
        cw_pm = data.get("cache_write")

        cache_read = _per_token(float(cr_pm)) if cr_pm is not None else None
        cache_write = _per_token(float(cw_pm)) if cw_pm is not None else None

        return cls(
            model=model,
            provider=provider,
            input_per_token=_per_token(input_pm),
            output_per_token=_per_token(output_pm),
            cache_read_per_token=cache_read,
            cache_write_per_token=cache_write,
        )

    def __repr__(self) -> str:
        return (
            f"ModelPricing(model={self.model!r}, provider={self.provider!r}, "
            f"input={self.input_per_token * 1e6:.4f}/1M, "
            f"output={self.output_per_token * 1e6:.4f}/1M)"
        )


class PricingCatalog:
    """Versioned pricing catalog loaded from ``pricing_catalog.json``.

    Attributes
    ----------
    version:
        Catalog version string (from ``_meta.version``).
    models:
        Dict mapping model identifiers to :class:`ModelPricing` records.

    Examples
    --------
    >>> catalog = PricingCatalog.load()
    >>> cost = catalog.compute_cost(
    ...     trace_id="t1",
    ...     model="claude-sonnet-4-6",
    ...     baseline_input_tokens=100_000,
    ...     actual_input_tokens=60_000,
    ...     output_tokens=5_000,
    ...     cache_read=20_000,
    ... )
    """

    def __init__(
        self,
        version: str,
        models: dict[str, ModelPricing],
    ) -> None:
        self.version = version
        self.models: dict[str, ModelPricing] = models

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[os.PathLike] = None) -> "PricingCatalog":
        """Load and parse the pricing catalog from *path*.

        If *path* is ``None`` the bundled ``data/pricing_catalog.json`` is
        used.

        Raises
        ------
        FileNotFoundError:
            If the catalog file cannot be found.
        ValueError:
            If the catalog JSON is malformed.
        """
        catalog_path = Path(path) if path is not None else _CATALOG_PATH
        if not catalog_path.exists():
            raise FileNotFoundError(f"Pricing catalog not found: {catalog_path}")
        with catalog_path.open("r", encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)

        if "models" not in raw:
            raise ValueError(f"Pricing catalog at {catalog_path} is missing a 'models' key.")

        meta: dict[str, Any] = raw.get("_meta", {})
        version: str = meta.get("version", "v1")

        models: dict[str, ModelPricing] = {}
        for model_id, entry in raw["models"].items():
            models[model_id] = ModelPricing.from_dict(model_id, entry)

        return cls(version=version, models=models)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PricingCatalog":
        """Construct a catalog from an already-parsed dict (useful in tests)."""
        meta = data.get("_meta", {})
        version = meta.get("version", "v1")
        models: dict[str, ModelPricing] = {}
        for model_id, entry in data.get("models", {}).items():
            models[model_id] = ModelPricing.from_dict(model_id, entry)
        return cls(version=version, models=models)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_model(self, model: str) -> Optional[ModelPricing]:
        """Return pricing for *model*, or ``None`` if not in catalog.

        Uses fuzzy matching: tries exact match first, then strips date
        suffixes, then finds longest prefix match.
        """
        resolved = self._resolve_model(model)
        if resolved is None:
            return None
        return self.models.get(resolved)

    def known_models(self) -> list[str]:
        """Return a sorted list of all model identifiers in the catalog."""
        return sorted(self.models.keys())

    def _resolve_model(self, model: str) -> Optional[str]:
        """Resolve model name with fuzzy matching.

        Resolution order:
        1. Exact match in catalog
        2. Strip date suffix (e.g. ``-20250514``) and retry
        3. Longest prefix match from catalog keys

        Parameters
        ----------
        model:
            Model identifier to resolve.

        Returns
        -------
        str or None
            Resolved model identifier, or ``None`` if no match found.
        """
        # 1. Exact match
        if model in self.models:
            return model

        # 2. Strip date suffix (e.g. claude-opus-4-6-20250514 -> claude-opus-4-6)
        base = re.sub(r"-\d{8}$", "", model)
        if base in self.models:
            logger.debug("Fuzzy match: %s -> %s (stripped date suffix)", model, base)
            return base

        # 3. Longest prefix match
        # Try models where either model starts with catalog key
        # or catalog key starts with model's base prefix
        matches: list[str] = []
        model_parts = model.split("-")
        for key in self.models:
            # catalog key is prefix of model
            if model.startswith(key):
                matches.append(key)
            # model base (first 2 parts) is prefix of catalog key
            elif len(model_parts) >= 2:
                base_prefix = "-".join(model_parts[:2])
                if key.startswith(base_prefix):
                    matches.append(key)

        if matches:
            resolved = max(matches, key=len)
            logger.debug("Fuzzy match: %s -> %s (prefix match)", model, resolved)
            return resolved

        logger.warning("Unknown model: %s (no pricing available)", model)
        return None

    # ------------------------------------------------------------------
    # Cost computation
    # ------------------------------------------------------------------

    def compute_cost(
        self,
        model: str,
        baseline_input_tokens: int,
        actual_input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
        trace_id: str = "",
        savings_qmd: float = 0.0,
        savings_tp: float = 0.0,
    ) -> Cost:
        """Compute cost and compression savings for a single LLM call.

        Parameters
        ----------
        model:
            Model identifier.  If unknown, costs are returned as zero with
            ``pricing_version="unknown"``.
        baseline_input_tokens:
            Token count *before* any compression (for savings calculation).
        actual_input_tokens:
            Token count *after* compression, actually sent to the model.
        output_tokens:
            Output tokens billed by the provider.
        cache_read:
            Tokens served from the provider cache (read hit).
        cache_write:
            Tokens written to the provider cache.
        trace_id:
            Parent trace identifier copied into the returned :class:`Cost`.
        savings_qmd:
            Savings (USD) to attribute to QMD compression pass.
        savings_tp:
            Savings (USD) to attribute to TokenPak compression pass.

        Returns
        -------
        Cost
            Populated :class:`~tokenpak.telemetry.models.Cost` instance.
        """
        resolved = self._resolve_model(model)
        if resolved is None:
            # Unknown model — return zero-cost record
            return Cost(
                trace_id=trace_id,
                pricing_version="unknown",
                baseline_input_tokens=baseline_input_tokens,
                actual_input_tokens=actual_input_tokens,
                output_tokens=output_tokens,
            )
        pricing = self.models[resolved]

        # --- baseline cost (no compression, no cache) -------------------
        baseline_cost = (
            baseline_input_tokens * pricing.input_per_token
            + output_tokens * pricing.output_per_token
        )

        # --- actual cost ------------------------------------------------
        # Input tokens are billed at the standard rate; cache-read tokens
        # are billed at the (lower) cache-read rate; cache-write tokens are
        # billed at the (higher) cache-write rate.
        cr_rate = pricing.cache_read_per_token if pricing.cache_read_per_token is not None else 0.0
        cw_rate = (
            pricing.cache_write_per_token if pricing.cache_write_per_token is not None else 0.0
        )

        # Actual input = tokens sent minus cache-read tokens (already cached)
        effective_input = max(0, actual_input_tokens - cache_read)
        actual_cost = (
            effective_input * pricing.input_per_token
            + cache_read * cr_rate
            + cache_write * cw_rate
            + output_tokens * pricing.output_per_token
        )

        savings_total = max(0.0, baseline_cost - actual_cost)

        return Cost(
            trace_id=trace_id,
            pricing_version=self.version,
            baseline_input_tokens=baseline_input_tokens,
            actual_input_tokens=actual_input_tokens,
            output_tokens=output_tokens,
            baseline_cost=baseline_cost,
            actual_cost=actual_cost,
            savings_total=savings_total,
            savings_qmd=savings_qmd,
            savings_tp=savings_tp,
        )


# ---------------------------------------------------------------------------
# Module-level convenience helpers
# ---------------------------------------------------------------------------

_default_catalog: Optional["PricingCatalog"] = None


def _get_default_catalog() -> "PricingCatalog":
    """Return the bundled pricing catalog (cached singleton)."""
    global _default_catalog
    if _default_catalog is None:
        _default_catalog = PricingCatalog.load()
    return _default_catalog


def compute_baseline_cost(model: str, raw_input_tokens: int) -> float:
    """Compute the hypothetical (no-compression) input cost for a model.

    This is the **"what would naive RAG have cost?"** number — the raw
    input-token count priced at the model's standard input rate with no
    cache or compression discounts applied.

    Parameters
    ----------
    model:
        Model identifier (fuzzy-matched against the pricing catalog).
    raw_input_tokens:
        Token count *before* any compression / cache discounts.

    Returns
    -------
    float
        Hypothetical cost in USD.  Returns ``0.0`` if the model is not in
        the pricing catalog (with a warning logged).

    Examples
    --------
    >>> cost = compute_baseline_cost("claude-opus-4-6", 50_000)
    >>> print(f"${cost:.4f}")   # ~ $0.75 at $15/1M
    """
    if raw_input_tokens <= 0:
        return 0.0

    catalog = _get_default_catalog()
    pricing = catalog.get_model(model)

    if pricing is None:
        logger.warning("compute_baseline_cost: unknown model %r — returning 0.0", model)
        return 0.0

    return raw_input_tokens * pricing.input_per_token
