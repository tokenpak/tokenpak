"""Explicit workload quotes for decisions that cannot use inferred prices."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ._pricing import PricingContext, RateBand, parse_pricing_timestamp
from ._registry import ModelRegistry, get_registry


class PricingQuoteUnavailable(ValueError):
    """No complete, current price is verified for the supplied workload."""


@dataclass(frozen=True)
class PricingQuote:
    provider: str
    model: str
    context: PricingContext
    band: RateBand
    as_of: datetime
    valid_until: datetime
    sha256: str


_OFFICIAL_HOSTS = {
    "anthropic": {"platform.claude.com"},
    "openai": {"developers.openai.com", "platform.openai.com"},
}


def quote_workload(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    input_modality: str,
    output_modality: str,
    service_tier: str,
    region: str,
    cache_ttl_seconds: int,
    as_of: datetime,
    max_age_seconds: int,
    registry: ModelRegistry | None = None,
) -> PricingQuote:
    """Quote an exact model and workload; all applicability fields are required.

    The caller supplies measured workload facts and its explicit freshness
    policy. This function performs no inference, discovery or network access.
    A quote describes token rates, not cache-hit eligibility or provider bills.
    """
    if type(input_tokens) is not int or type(cache_ttl_seconds) is not int:
        raise PricingQuoteUnavailable("input count and cache lifetime must be explicit integers")
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise PricingQuoteUnavailable("a positive pricing freshness policy is required")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PricingQuoteUnavailable("quote time must include a timezone")
    if not isinstance(model, str) or not model or model != model.strip():
        raise PricingQuoteUnavailable("an exact model identifier is required")
    if not isinstance(provider, str) or provider not in _OFFICIAL_HOSTS:
        raise PricingQuoteUnavailable("provider pricing is not verified")
    try:
        context = PricingContext(
            input_tokens, input_modality, output_modality, service_tier, region, cache_ttl_seconds
        )
        if "*" in (
            context.input_modality,
            context.output_modality,
            context.service_tier,
            context.region,
        ):
            raise ValueError("workload fields cannot be wildcards")
        info = (registry or get_registry()).resolve(model)
        if info.model_id != model or info.source != "seed" or info.provider != provider:
            raise ValueError("exact model and provider must have catalog evidence")
        band = info.rate_band_for(context)
        if band is None:
            raise ValueError("no price band matches the explicit workload")
        if (
            any(
                "*" in selector
                for selector in (
                    band.input_modalities,
                    band.output_modalities,
                    band.service_tiers,
                    band.regions,
                )
            )
            or band.cache_ttl_seconds is None
            or band.max_input_tokens is None
        ):
            raise ValueError("price band has unspecified workload applicability")
        if band.cache_read_per_mtok is None or band.cache_write_per_mtok is None:
            raise ValueError("price band lacks complete cache rates")
        url = urlparse(band.source_url)
        if (
            band.source != "official"
            or url.scheme != "https"
            or url.hostname not in _OFFICIAL_HOSTS[provider]
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
        ):
            raise ValueError("price band lacks official provider provenance")
        verified = parse_pricing_timestamp(band.verified_at)
        if verified > as_of:
            raise ValueError("price verification is in the future")
        valid_until = verified + timedelta(seconds=max_age_seconds)
        if band.expires_at is not None:
            valid_until = min(valid_until, parse_pricing_timestamp(band.expires_at))
        if as_of >= valid_until:
            raise ValueError("price verification is stale or expired")
    except (ValueError, OverflowError) as exc:
        raise PricingQuoteUnavailable(str(exc)) from exc
    as_of = as_of.astimezone(timezone.utc)
    valid_until = valid_until.astimezone(timezone.utc)
    payload = {
        "provider": provider,
        "model": model,
        "context": asdict(context),
        "band": band.to_mapping(),
        "as_of": as_of.isoformat(),
        "valid_until": valid_until.isoformat(),
        "unit_basis": "USD_PER_1M",
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return PricingQuote(provider, model, context, band, as_of, valid_until, digest)
