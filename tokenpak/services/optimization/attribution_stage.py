# SPDX-License-Identifier: Apache-2.0
"""Attribution stage — parses upstream usage and emits SavingsAttribution records.

The AttributionStage is a post-response stage that:
1. Inspects the upstream provider response usage dict.
2. Extracts provider/platform cache token counts.
3. Creates SavingsAttribution records on the context via a side attribute.

Feature flag: ``TOKENPAK_ATTRIBUTION_V2`` (default off).

Layering:
- TIP layer: SavingsSource vocabulary from tokenpak.tip.telemetry_contract
- TIP layer: SavingsAttribution from tokenpak.tip.trace_contract
- Services layer: this module — consumes TIP types, operates on canonical context
- Adapter layer: provider-specific parsing in telemetry/savings.py

The stage intentionally does NOT claim provider/platform cache as TokenPak
savings (attribution rule from SavingsSource docs).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from .context import OptimizationContext
from .stage import EligibilityResult

_log = logging.getLogger(__name__)

_ENV_FLAG = "TOKENPAK_ATTRIBUTION_V2"

# Side-attribute keys on OptimizationContext (additive, non-breaking)
_ATTRIBUTIONS_ATTR = "_tip06_attributions"


def is_attribution_v2_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True when TOKENPAK_ATTRIBUTION_V2 is set to a truthy value."""
    source = env if env is not None else os.environ
    val = (source.get(_ENV_FLAG, "") or "").strip().lower()
    return val in {"1", "on", "true", "yes"}


def get_attributions(ctx: OptimizationContext) -> List[Any]:
    """Return the SavingsAttribution list set by apply(), or empty list."""
    return getattr(ctx, _ATTRIBUTIONS_ATTR, [])


def _set_attributions(ctx: OptimizationContext, attrs: List[Any]) -> None:
    object.__setattr__(ctx, _ATTRIBUTIONS_ATTR, attrs)


class AttributionStage:
    """Post-response stage that parses usage and emits SavingsAttribution records.

    Usage
    -----
    Unlike most stages, AttributionStage works in two phases:

    Phase A — eligibility check (pre-upstream, via eligible()):
        stage.eligible(ctx)

    Phase B — attribution parsing (post-upstream, via parse_response()):
        stage.parse_response(ctx, response_body_bytes)

    The standard pipeline calls only ``eligible()``. Callers that want
    actual attribution data must call ``parse_response()`` after receiving
    the upstream response.
    """

    name: str = "attribution"
    required_capabilities: frozenset[str] = frozenset()

    def __init__(self, env: Optional[Dict[str, str]] = None) -> None:
        self._env = env

    # ------------------------------------------------------------------
    # OptimizationStage protocol
    # ------------------------------------------------------------------

    def eligible(self, ctx: OptimizationContext) -> EligibilityResult:
        if not is_attribution_v2_enabled(self._env):
            return EligibilityResult(
                eligible=False,
                skip_reason="flag-off",
                detail="TOKENPAK_ATTRIBUTION_V2 not set",
            )
        return EligibilityResult(eligible=True)

    def apply(self, ctx: OptimizationContext) -> OptimizationContext:
        # apply() is a no-op — this stage works post-response via parse_response()
        return ctx

    # ------------------------------------------------------------------
    # Post-response attribution
    # ------------------------------------------------------------------

    def parse_response(
        self,
        ctx: OptimizationContext,
        response_body: bytes,
        *,
        platform: Optional[str] = None,
        model: str = "",
    ) -> List[Any]:
        """Parse response body and return SavingsAttribution records.

        Annotates ctx with the results via ``_tip06_attributions``.
        Errors are caught and logged; an empty list is returned on failure.
        Never raises.
        """
        if not is_attribution_v2_enabled(self._env):
            return []

        try:
            attributions = _extract_attributions(
                response_body,
                platform=platform or ctx.platform,
                model=model or _extract_model(ctx),
            )
        except Exception as exc:
            _log.debug(
                "[AttributionStage] extraction error: %s: %s",
                type(exc).__name__,
                exc,
            )
            attributions = []

        _set_attributions(ctx, attributions)
        return attributions


# ---------------------------------------------------------------------------
# Parsing helpers (services layer — delegate to telemetry/savings.py for source logic)
# ---------------------------------------------------------------------------


def _extract_model(ctx: OptimizationContext) -> str:
    """Best-effort model name from context."""
    if ctx.raw_body:
        try:
            parsed = json.loads(ctx.raw_body)
            return parsed.get("model", "") or ""
        except Exception:
            pass
    return ""


def _detect_provider(platform: Optional[str], body_parsed: Dict[str, Any]) -> str:
    """Infer provider from platform hint or response shape."""
    if platform:
        pl = platform.lower()
        if "anthropic" in pl or "claude" in pl:
            return "anthropic"
        if "openai" in pl or "codex" in pl:
            return "openai"

    # Anthropic responses have input_tokens at top level without prompt_tokens
    if "input_tokens" in body_parsed and "prompt_tokens" not in body_parsed:
        return "anthropic"
    # OpenAI has prompt_tokens
    if "prompt_tokens" in body_parsed or "usage" in body_parsed:
        parsed_usage = body_parsed.get("usage", {})
        if "prompt_tokens" in parsed_usage:
            return "openai"

    return "unknown"


def _extract_attributions(
    response_body: bytes,
    *,
    platform: Optional[str] = None,
    model: str = "",
) -> List[Any]:
    """Parse a provider response body and return SavingsAttribution records."""
    from tokenpak.telemetry.savings import parse_anthropic_usage, parse_openai_usage

    if not response_body:
        return []

    try:
        parsed = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    # Extract usage sub-dict (OpenAI wraps in "usage", Anthropic has top-level fields)
    usage_dict: Dict[str, Any] = {}
    if "usage" in parsed:
        usage_dict = parsed["usage"] or {}
    elif "input_tokens" in parsed or "prompt_tokens" in parsed:
        usage_dict = parsed

    if not usage_dict:
        return []

    provider = _detect_provider(platform, parsed)

    if provider == "anthropic":
        return parse_anthropic_usage(usage_dict, model=model)
    elif provider == "openai":
        return parse_openai_usage(usage_dict, model=model)
    else:
        # Try both; take whichever yields results
        results = parse_openai_usage(usage_dict, model=model)
        if not results:
            results = parse_anthropic_usage(usage_dict, model=model)
        return results


__all__ = [
    "AttributionStage",
    "is_attribution_v2_enabled",
    "get_attributions",
]
