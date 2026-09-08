# SPDX-License-Identifier: Apache-2.0
"""Bind a committed token-rate estimate to its observed workload and prices.

Receipts establish rate applicability at pricing time, not provider invoices
or a consumer's separate maximum price-age policy. No network access occurs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from tokenpak.models._pricing import PricingContext, RateBand, parse_pricing_timestamp
from tokenpak.models._quote import _OFFICIAL_HOSTS
from tokenpak.models._registry import ModelRegistry

from .request_workload import RequestWorkload, _json

_MAX_RECEIPT = 16 * 1024


def _context(workload, ttl):
    return PricingContext(
        workload.input_tokens,
        workload.input_modality,
        workload.output_modality,
        workload.service_tier,
        workload.region,
        ttl,
    )


def _check_workload(workload):
    if not isinstance(workload, RequestWorkload):
        raise ValueError("observed workload required")
    # Zero-cache charges still require every other observed fact. Do not
    # populate a synthetic TTL in the recorded workload or pricing context.
    if workload.reason_codes == ("cache_ttl_unavailable",):
        if (
            workload.cache_ttl_seconds is not None
            or workload.cache_read_tokens != 0
            or workload.cache_write_tokens != 0
        ):
            raise ValueError("cache lifetime affects this charge")
        required = (
            "body_sha256",
            "provider",
            "model",
            "response_model",
            "input_modality",
            "output_modality",
            "service_tier",
            "region",
            "input_tokens",
            "output_tokens",
        )
        if (
            not workload.response_complete
            or any(getattr(workload, field) is None for field in required)
            or workload.model != workload.response_model
            or (workload.requested_region and workload.requested_region != workload.region)
            or (
                workload.requested_service_tier == "standard_only"
                and workload.service_tier != "standard"
            )
        ):
            raise ValueError("incomplete zero-cache workload")
    elif workload.reason_codes:
        raise ValueError("workload is unavailable for pricing")
    RequestWorkload.from_json(workload.to_json())


def _check_band(band, workload, priced_at):
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
        or band.max_input_tokens is None
        or band.cache_ttl_seconds is None
        or band.cache_read_per_mtok is None
        or band.cache_write_per_mtok is None
        or not band.matches(_context(workload, band.cache_ttl_seconds))
    ):
        raise ValueError("price band lacks exact workload applicability")
    url = urlsplit(band.source_url)
    if (
        band.source != "official"
        or url.scheme != "https"
        or url.hostname not in _OFFICIAL_HOSTS.get(workload.provider, ())
        or url.username is not None
        or url.password is not None
        or url.port not in (None, 443)
        or url.query
        or url.fragment
    ):
        raise ValueError("price band lacks public official provenance")
    if parse_pricing_timestamp(band.verified_at) > priced_at:
        raise ValueError("price verification is in the future")
    if band.expires_at is not None and priced_at >= parse_pricing_timestamp(band.expires_at):
        raise ValueError("price band expired before pricing")


@dataclass(frozen=True)
class RequestPrice:
    workload: RequestWorkload
    bands: tuple[RateBand, ...]
    priced_at: str
    schema_version: str = "native-request-price/1"

    def __post_init__(self):
        if self.schema_version != "native-request-price/1":
            raise ValueError("unsupported request price schema")
        _check_workload(self.workload)
        priced_at = parse_pricing_timestamp(self.priced_at)
        if type(self.bands) is not tuple or not 1 <= len(self.bands) <= 8:
            raise ValueError("invalid request price bands")
        if any(not isinstance(band, RateBand) for band in self.bands):
            raise ValueError("invalid request price band")
        ttls = tuple(band.cache_ttl_seconds for band in self.bands)
        if any(type(ttl) is not int for ttl in ttls) or tuple(sorted(set(ttls))) != ttls:
            raise ValueError("ambiguous request price lifetimes")
        for band in self.bands:
            _check_band(band, self.workload, priced_at)
        if self.workload.cache_ttl_seconds is not None:
            if ttls != (self.workload.cache_ttl_seconds,):
                raise ValueError("request price lifetime mismatch")
        else:
            if ttls != (300, 3600):
                raise ValueError("prices do not cover the supported cache lifetimes")
            if len({(b.input_per_mtok, b.output_per_mtok) for b in self.bands}) != 1:
                raise ValueError("unknown lifetime changes token prices")
        if not math.isfinite(self.cost_usd):
            raise ValueError("nonfinite request price")

    @property
    def cost_usd(self):
        w, band = self.workload, self.bands[0]
        return (
            (w.input_tokens - w.cache_read_tokens - w.cache_write_tokens) * band.input_per_mtok
            + w.output_tokens * band.output_per_mtok
            + w.cache_read_tokens * band.cache_read_per_mtok
            + w.cache_write_tokens * band.cache_write_per_mtok
        ) / 1_000_000

    def to_json(self):
        result = json.dumps(
            {
                "schema_version": self.schema_version,
                "workload": self.workload.to_dict(),
                "bands": [b.to_mapping() for b in self.bands],
                "priced_at": self.priced_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(result.encode()) > _MAX_RECEIPT:
            raise ValueError("request price receipt exceeds size limit")
        return result

    @classmethod
    def from_json(cls, raw):
        if not isinstance(raw, str) or len(raw.encode()) > _MAX_RECEIPT:
            raise ValueError("invalid request price receipt")
        data = _json(raw)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("invalid request price fields")
        if not isinstance(data["bands"], list) or not 1 <= len(data["bands"]) <= 8:
            raise ValueError("invalid request price bands")
        data["workload"] = RequestWorkload.from_json(json.dumps(data["workload"]))
        data["bands"] = tuple(RateBand.from_mapping(band) for band in data["bands"])
        return cls(**data)

    def require_row(
        self, *, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost
    ):
        w = self.workload
        if model != w.model or any(
            type(actual) is not int or actual != expected
            for actual, expected in (
                (input_tokens, w.input_tokens),
                (output_tokens, w.output_tokens),
                (cache_read_tokens, w.cache_read_tokens),
                (cache_creation_tokens, w.cache_write_tokens),
            )
        ):
            raise ValueError("request price does not match recorded workload")
        if type(cost) not in (int, float) or cost != self.cost_usd:
            raise ValueError("request price does not match recorded cost")


def price_request(workload: RequestWorkload, *, registry=None) -> RequestPrice:
    """Resolve actual observed token prices from a fresh installed catalog."""
    _check_workload(workload)
    info = (registry or ModelRegistry()).resolve(workload.model)
    if (
        info.model_id != workload.model
        or info.provider != workload.provider
        or info.source != "seed"
    ):
        raise ValueError("exact catalog model and provider required")
    ttl = workload.cache_ttl_seconds
    if ttl is not None:
        band = info.rate_band_for(_context(workload, ttl))
        bands = (band,) if band is not None else ()
    else:
        # Consult every explicit lifetime for this workload. Do not choose a
        # cheap lifetime or let an unspecified wildcard band establish prices.
        candidates = [
            band
            for band in info.rate_bands
            if band.matches(_context(workload, band.cache_ttl_seconds))
        ]
        if any(band.cache_ttl_seconds is None for band in candidates):
            raise ValueError("unspecified lifetime price applicability")
        bands = tuple(
            info.rate_band_for(_context(workload, candidate_ttl))
            for candidate_ttl in sorted({b.cache_ttl_seconds for b in candidates})
        )
    result = RequestPrice(workload, bands, datetime.now(timezone.utc).isoformat())
    result.to_json()  # Apply the persistence bound before a price is used.
    return result
