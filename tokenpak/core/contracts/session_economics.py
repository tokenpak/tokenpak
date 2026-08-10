# SPDX-License-Identifier: Apache-2.0
"""Versioned, truth-preserving session-economics contract.

The contract deliberately distinguishes observations, estimates, empty data,
unavailable inputs, and failures.  A missing measurement is never serialized
as numeric zero, while a real observation of zero remains valid.

This module contains no route comparison or reroute recommendation logic.
Open-source producers always serialize ``advisory`` as JSON ``null``; entitled
extensions can consume the shared payload and own their advisory envelope
separately.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Union

Number = Union[int, float]
SCHEMA_VERSION = "session-economics/1"
_MISSING = object()
_NON_RENDERING_SYMBOLS = frozenset(
    {
        0x115F,  # HANGUL CHOSEONG FILLER
        0x1160,  # HANGUL JUNGSEONG FILLER
        0x2800,  # BRAILLE PATTERN BLANK
        0x3164,  # HANGUL FILLER
        0xFFA0,  # HALFWIDTH HANGUL FILLER
    }
)


class SessionEconomicsContractError(ValueError):
    """Base error for an invalid session-economics payload."""


class UnsupportedSessionEconomicsVersion(SessionEconomicsContractError):
    """Raised when a consumer receives an unsupported schema version."""


class _FinalValueObject:
    """Prevent public contract objects from bypassing validation through subclasses."""

    _tokenpak_final_value_object = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        final_base = next(
            (
                base
                for base in cls.__bases__
                if base.__dict__.get("_tokenpak_final_value_object", False)
            ),
            None,
        )
        if final_base is not None:
            raise TypeError(f"{final_base.__name__} does not support subclassing")
        cls._tokenpak_final_value_object = True


class ValueState(str, Enum):
    """Availability and provenance state for a numeric value."""

    OBSERVED = "observed"
    ESTIMATED = "estimated"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class CostBasis(str, Enum):
    """Economic basis for a cost value."""

    PROVIDER_BILL = "provider_bill"
    RATE_CARD = "rate_card"
    SUBSCRIPTION = "subscription"
    UNKNOWN = "unknown"


class PriceFreshness(str, Enum):
    """Freshness of the rate provenance used for a price estimate."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class BurnSlope(str, Enum):
    UNKNOWN = "unknown"
    DOWN = "down"
    FLAT = "flat"
    UP = "up"


class CacheState(str, Enum):
    WARM = "warm"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class RunwayStatus(str, Enum):
    AVAILABLE = "available"
    LEARNING = "learning"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class BindingConstraint(str, Enum):
    BUDGET = "budget"
    CONTEXT_SOFT = "context_soft"
    CONTEXT_HARD = "context_hard"
    ROLLING_CAP = "rolling_cap"
    UNKNOWN = "unknown"


class GuardState(str, Enum):
    ALLOW = "allow"
    AMBER = "amber"
    SOFT_BLOCK = "soft_block"
    HARD_STOP = "hard_stop"
    UNKNOWN = "unknown"


class ForecastStatus(str, Enum):
    LEARNING = "learning"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DriftState(str, Enum):
    STABLE = "stable"
    DRIFTING = "drifting"
    UNKNOWN = "unknown"


def _as_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SessionEconomicsContractError(f"{path} must be an object")
    return value


def _enum(enum_type: type[Enum], value: object, path: str) -> Any:
    allowed = ", ".join(item.value for item in enum_type)
    if type(value) is not str:
        raise SessionEconomicsContractError(f"{path} must be one of: {allowed}")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SessionEconomicsContractError(f"{path} must be one of: {allowed}") from exc


def _require_enum(value: object, enum_type: type[Enum], path: str) -> None:
    """Reject raw or unknown values on direct value-object construction."""

    if not isinstance(value, enum_type):
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise SessionEconomicsContractError(f"{path} must be one of: {allowed}")


def _require_value_object(value: object, expected_type: type[object], path: str) -> None:
    """Reject duck-typed or subclassed objects at direct-construction boundaries."""

    if type(value) is not expected_type:
        raise SessionEconomicsContractError(f"{path} must be a validated {expected_type.__name__}")


def _number(value: object, path: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionEconomicsContractError(f"{path} must be numeric")
    if not math.isfinite(float(value)) or value < 0:
        raise SessionEconomicsContractError(f"{path} must be finite and non-negative")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise SessionEconomicsContractError(f"{path} must be a string")
    if any(unicodedata.category(character) == "Cs" for character in value):
        raise SessionEconomicsContractError(f"{path} must contain valid Unicode scalar values")
    return value


def _has_visible_text(value: str) -> bool:
    """Return whether a string contains at least one rendering base character."""

    return any(
        not (
            character.isspace()
            or unicodedata.category(character)[0] in {"C", "M", "Z"}
            or ord(character) in _NON_RENDERING_SYMBOLS
        )
        for character in value
    )


def _non_blank_string(value: object, path: str) -> str:
    result = _string(value, path)
    if not _has_visible_text(result):
        raise SessionEconomicsContractError(f"{path} must be non-empty")
    return result


def _optional_string(value: object, path: str) -> str:
    result = _string(value, path)
    if result and not _has_visible_text(result):
        raise SessionEconomicsContractError(f"{path} must not be whitespace-only")
    return result


def _input_string(value: object, path: str, *, default: str = "") -> str:
    if value is _MISSING:
        return default
    return _string(value, path)


def _timestamp(value: str, path: str) -> None:
    value = _string(value, path)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SessionEconomicsContractError(f"{path} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SessionEconomicsContractError(f"{path} must include a timezone")


@dataclass(frozen=True)
class NumericValue(_FinalValueObject):
    """Numeric fact or estimate with explicit unavailable states."""

    state: ValueState
    value: Number | None = None
    source: str = ""
    confidence: str = ""
    reason: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.state, ValueState, "numeric value.state")
        for name in ("source", "confidence", "reason", "unit"):
            _optional_string(getattr(self, name), f"numeric value.{name}")
        has_value = self.state in {ValueState.OBSERVED, ValueState.ESTIMATED}
        if has_value:
            _number(self.value, "numeric value")
            if not _has_visible_text(self.source):
                raise SessionEconomicsContractError(
                    f"{self.state.value} numeric value requires source provenance"
                )
        elif self.value is not None:
            raise SessionEconomicsContractError(
                f"{self.state.value} numeric value must serialize as null"
            )
        if self.state is ValueState.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error numeric value requires a reason")

    @classmethod
    def observed(
        cls, value: Number, *, source: str, confidence: str = "", unit: str = ""
    ) -> "NumericValue":
        return cls(ValueState.OBSERVED, value, source, confidence, unit=unit)

    @classmethod
    def estimated(
        cls, value: Number, *, source: str, confidence: str = "", unit: str = ""
    ) -> "NumericValue":
        return cls(ValueState.ESTIMATED, value, source, confidence, unit=unit)

    @classmethod
    def no_data(cls, reason: str = "", *, unit: str = "") -> "NumericValue":
        return cls(ValueState.NO_DATA, reason=reason, unit=unit)

    @classmethod
    def unavailable(cls, reason: str = "", *, unit: str = "") -> "NumericValue":
        return cls(ValueState.UNAVAILABLE, reason=reason, unit=unit)

    @classmethod
    def error(cls, reason: str, *, unit: str = "") -> "NumericValue":
        return cls(ValueState.ERROR, reason=reason, unit=unit)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state.value, "value": self.value}
        for key in ("source", "confidence", "reason", "unit"):
            value = getattr(self, key)
            if value:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NumericValue":
        data = _as_mapping(raw, "numeric value")
        return cls(
            state=_enum(ValueState, data.get("state"), "numeric value.state"),
            value=data.get("value"),
            source=_input_string(data.get("source", _MISSING), "numeric value.source"),
            confidence=_input_string(data.get("confidence", _MISSING), "numeric value.confidence"),
            reason=_input_string(data.get("reason", _MISSING), "numeric value.reason"),
            unit=_input_string(data.get("unit", _MISSING), "numeric value.unit"),
        )


@dataclass(frozen=True)
class RateProvenance(_FinalValueObject):
    """Catalog identity and freshness for a rate-card estimate."""

    catalog_version: str | None = None
    effective_at: str | None = None
    source: str | None = None
    freshness: PriceFreshness = PriceFreshness.UNKNOWN

    def __post_init__(self) -> None:
        _require_enum(self.freshness, PriceFreshness, "rate_provenance.freshness")
        for name in ("catalog_version", "effective_at", "source"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise SessionEconomicsContractError(
                    f"rate_provenance.{name} must be a string or null"
                )
            if value is not None:
                _string(value, f"rate_provenance.{name}")
            if value is not None and not _has_visible_text(value):
                raise SessionEconomicsContractError(
                    f"rate_provenance.{name} must be non-empty when present"
                )
        if self.effective_at:
            _timestamp(self.effective_at, "rate_provenance.effective_at")

    @property
    def complete(self) -> bool:
        return all(
            value is not None and _has_visible_text(value)
            for value in (self.catalog_version, self.effective_at, self.source)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "effective_at": self.effective_at,
            "source": self.source,
            "freshness": self.freshness.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "RateProvenance":
        data = _as_mapping({} if raw is None else raw, "rate_provenance")
        return cls(
            catalog_version=data.get("catalog_version"),
            effective_at=data.get("effective_at"),
            source=data.get("source"),
            freshness=_enum(
                PriceFreshness,
                data.get("freshness", PriceFreshness.UNKNOWN.value),
                "rate_provenance.freshness",
            ),
        )


@dataclass(frozen=True)
class CostValue(_FinalValueObject):
    """USD value with a basis that prevents stale-rate false precision."""

    state: ValueState
    value: Number | None = None
    basis: CostBasis = CostBasis.UNKNOWN
    source: str = ""
    reason: str = ""
    rate_provenance: RateProvenance = field(default_factory=RateProvenance)

    def __post_init__(self) -> None:
        _require_enum(self.state, ValueState, "cost_usd.state")
        _require_enum(self.basis, CostBasis, "cost_usd.basis")
        _require_value_object(
            self.rate_provenance,
            RateProvenance,
            "cost_usd.rate_provenance",
        )
        _optional_string(self.source, "cost_usd.source")
        _optional_string(self.reason, "cost_usd.reason")
        has_value = self.state in {ValueState.OBSERVED, ValueState.ESTIMATED}
        if has_value:
            _number(self.value, "cost_usd.value")
        elif self.value is not None:
            raise SessionEconomicsContractError(f"{self.state.value} cost must serialize as null")

        if self.state is ValueState.OBSERVED:
            if self.basis is not CostBasis.PROVIDER_BILL or not _has_visible_text(self.source):
                raise SessionEconomicsContractError(
                    "observed cost requires provider_bill basis and source"
                )
        elif self.state is ValueState.ESTIMATED:
            if self.basis is not CostBasis.RATE_CARD:
                raise SessionEconomicsContractError("estimated cost requires rate_card basis")
            if not self.rate_provenance.complete:
                raise SessionEconomicsContractError(
                    "estimated cost requires complete rate provenance"
                )
            if self.rate_provenance.freshness is not PriceFreshness.FRESH:
                raise SessionEconomicsContractError("estimated cost requires fresh rate provenance")
        if self.basis in {CostBasis.SUBSCRIPTION, CostBasis.UNKNOWN} and has_value:
            raise SessionEconomicsContractError(
                f"{self.basis.value} cost basis cannot carry numeric USD"
            )
        if self.state is ValueState.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error cost requires a reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state.value,
            "value": self.value,
            "basis": self.basis.value,
            "rate_provenance": self.rate_provenance.to_dict(),
        }
        if self.source:
            result["source"] = self.source
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CostValue":
        data = _as_mapping(raw, "cost_usd")
        return cls(
            state=_enum(ValueState, data.get("state"), "cost_usd.state"),
            value=data.get("value"),
            basis=_enum(
                CostBasis,
                data.get("basis", CostBasis.UNKNOWN.value),
                "cost_usd.basis",
            ),
            source=_input_string(data.get("source", _MISSING), "cost_usd.source"),
            reason=_input_string(data.get("reason", _MISSING), "cost_usd.reason"),
            rate_provenance=RateProvenance.from_dict(data.get("rate_provenance")),
        )


@dataclass(frozen=True)
class SessionFacts(_FinalValueObject):
    input_tokens: NumericValue
    output_tokens: NumericValue
    cache_read_tokens: NumericValue
    cache_write_tokens: NumericValue
    cost_usd: CostValue

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            value = getattr(self, name)
            _require_value_object(value, NumericValue, f"facts.{name}")
            if value.state is ValueState.ESTIMATED:
                raise SessionEconomicsContractError(f"facts.{name} cannot use estimated state")
        _require_value_object(self.cost_usd, CostValue, "facts.cost_usd")

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens.to_dict(),
            "output_tokens": self.output_tokens.to_dict(),
            "cache_read_tokens": self.cache_read_tokens.to_dict(),
            "cache_write_tokens": self.cache_write_tokens.to_dict(),
            "cost_usd": self.cost_usd.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SessionFacts":
        data = _as_mapping(raw, "facts")
        return cls(
            input_tokens=NumericValue.from_dict(data.get("input_tokens")),
            output_tokens=NumericValue.from_dict(data.get("output_tokens")),
            cache_read_tokens=NumericValue.from_dict(data.get("cache_read_tokens")),
            cache_write_tokens=NumericValue.from_dict(data.get("cache_write_tokens")),
            cost_usd=CostValue.from_dict(data.get("cost_usd")),
        )


@dataclass(frozen=True)
class ModelRef(_FinalValueObject):
    id: str
    effort: str = "unknown"

    def __post_init__(self) -> None:
        _non_blank_string(self.id, "session.model.id")
        _non_blank_string(self.effort, "session.model.effort")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "effort": self.effort}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelRef":
        data = _as_mapping(raw, "session.model")
        return cls(
            id=_input_string(data.get("id", _MISSING), "session.model.id"),
            effort=_input_string(
                data.get("effort", _MISSING), "session.model.effort", default="unknown"
            ),
        )


@dataclass(frozen=True)
class SessionRef(_FinalValueObject):
    id: str | None
    identity_state: ValueState
    turns_observed: int
    model: ModelRef
    reason: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.identity_state, ValueState, "session.identity_state")
        _require_value_object(self.model, ModelRef, "session.model")
        _optional_string(self.reason, "session.reason")
        if self.id is not None:
            if not isinstance(self.id, str):
                raise SessionEconomicsContractError("session.id must be a string or null")
            _string(self.id, "session.id")
        if self.identity_state is ValueState.ESTIMATED:
            raise SessionEconomicsContractError("session identity cannot be estimated")
        if self.identity_state is ValueState.OBSERVED:
            if self.id is None or not _has_visible_text(self.id):
                raise SessionEconomicsContractError(
                    "observed session identity requires a non-empty id"
                )
        elif self.id is not None:
            raise SessionEconomicsContractError(
                f"{self.identity_state.value} session identity must be null"
            )
        if (
            isinstance(self.turns_observed, bool)
            or not isinstance(self.turns_observed, int)
            or self.turns_observed < 0
        ):
            raise SessionEconomicsContractError(
                "session.turns_observed must be a non-negative integer"
            )
        if self.identity_state is ValueState.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error session identity requires a reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "identity_state": self.identity_state.value,
            "turns_observed": self.turns_observed,
            "model": self.model.to_dict(),
        }
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SessionRef":
        data = _as_mapping(raw, "session")
        turns = data.get("turns_observed")
        if isinstance(turns, bool) or not isinstance(turns, int):
            raise SessionEconomicsContractError(
                "session.turns_observed must be a non-negative integer"
            )
        return cls(
            id=data.get("id"),
            identity_state=_enum(
                ValueState,
                data.get("identity_state"),
                "session.identity_state",
            ),
            turns_observed=turns,
            model=ModelRef.from_dict(data.get("model")),
            reason=_input_string(data.get("reason", _MISSING), "session.reason"),
        )


@dataclass(frozen=True)
class SessionState(_FinalValueObject):
    context_tokens: NumericValue
    base_tokens: NumericValue
    context_growth_ewma: NumericValue
    burn_tokens_per_turn: NumericValue
    burn_usd_per_turn: NumericValue
    burn_slope: BurnSlope
    idle_seconds: NumericValue
    cache_ttl_seconds: NumericValue
    cache_state: CacheState

    def __post_init__(self) -> None:
        for name in (
            "context_tokens",
            "base_tokens",
            "context_growth_ewma",
            "burn_tokens_per_turn",
            "burn_usd_per_turn",
            "idle_seconds",
            "cache_ttl_seconds",
        ):
            _require_value_object(getattr(self, name), NumericValue, f"state.{name}")
        _require_enum(self.burn_slope, BurnSlope, "state.burn_slope")
        _require_enum(self.cache_state, CacheState, "state.cache_state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_tokens": self.context_tokens.to_dict(),
            "base_tokens": self.base_tokens.to_dict(),
            "context_growth_ewma": self.context_growth_ewma.to_dict(),
            "burn_tokens_per_turn": self.burn_tokens_per_turn.to_dict(),
            "burn_usd_per_turn": self.burn_usd_per_turn.to_dict(),
            "burn_slope": self.burn_slope.value,
            "idle_seconds": self.idle_seconds.to_dict(),
            "cache_ttl_seconds": self.cache_ttl_seconds.to_dict(),
            "cache_state": self.cache_state.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SessionState":
        data = _as_mapping(raw, "state")
        return cls(
            context_tokens=NumericValue.from_dict(data.get("context_tokens")),
            base_tokens=NumericValue.from_dict(data.get("base_tokens")),
            context_growth_ewma=NumericValue.from_dict(data.get("context_growth_ewma")),
            burn_tokens_per_turn=NumericValue.from_dict(data.get("burn_tokens_per_turn")),
            burn_usd_per_turn=NumericValue.from_dict(data.get("burn_usd_per_turn")),
            burn_slope=_enum(BurnSlope, data.get("burn_slope"), "state.burn_slope"),
            idle_seconds=NumericValue.from_dict(data.get("idle_seconds")),
            cache_ttl_seconds=NumericValue.from_dict(data.get("cache_ttl_seconds")),
            cache_state=_enum(CacheState, data.get("cache_state"), "state.cache_state"),
        )


@dataclass(frozen=True)
class Runway(_FinalValueObject):
    status: RunwayStatus
    turns: int | None
    binding_constraint: BindingConstraint
    guard_state: GuardState
    reason: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.status, RunwayStatus, "runway.status")
        _require_enum(
            self.binding_constraint,
            BindingConstraint,
            "runway.binding_constraint",
        )
        _require_enum(self.guard_state, GuardState, "runway.guard_state")
        _optional_string(self.reason, "runway.reason")
        if self.status is RunwayStatus.AVAILABLE:
            if isinstance(self.turns, bool) or not isinstance(self.turns, int) or self.turns < 0:
                raise SessionEconomicsContractError(
                    "available runway requires non-negative integer turns"
                )
            if self.binding_constraint is BindingConstraint.UNKNOWN:
                raise SessionEconomicsContractError(
                    "available runway requires a binding constraint"
                )
        elif self.turns is not None:
            raise SessionEconomicsContractError(f"{self.status.value} runway turns must be null")
        if self.guard_state is GuardState.HARD_STOP and self.turns not in {None, 0}:
            raise SessionEconomicsContractError(
                "hard_stop runway cannot report positive remaining turns"
            )
        if self.status is RunwayStatus.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error runway requires a reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "turns": self.turns,
            "binding_constraint": self.binding_constraint.value,
            "guard_state": self.guard_state.value,
        }
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Runway":
        data = _as_mapping(raw, "runway")
        return cls(
            status=_enum(RunwayStatus, data.get("status"), "runway.status"),
            turns=data.get("turns"),
            binding_constraint=_enum(
                BindingConstraint,
                data.get("binding_constraint"),
                "runway.binding_constraint",
            ),
            guard_state=_enum(GuardState, data.get("guard_state"), "runway.guard_state"),
            reason=_input_string(data.get("reason", _MISSING), "runway.reason"),
        )


@dataclass(frozen=True)
class IntervalEstimate(_FinalValueObject):
    state: ValueState
    low: Number | None = None
    high: Number | None = None
    source: str = ""
    reason: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.state, ValueState, "interval.state")
        for name in ("source", "reason", "unit"):
            _optional_string(getattr(self, name), f"interval.{name}")
        if self.state is ValueState.ESTIMATED:
            low = _number(self.low, "interval.low")
            high = _number(self.high, "interval.high")
            if low > high:
                raise SessionEconomicsContractError("interval.low must not exceed interval.high")
            if not _has_visible_text(self.source):
                raise SessionEconomicsContractError("estimated interval requires source provenance")
        elif self.low is not None or self.high is not None:
            raise SessionEconomicsContractError(f"{self.state.value} interval bounds must be null")
        if self.state is ValueState.OBSERVED:
            raise SessionEconomicsContractError("forecast interval cannot be observed")
        if self.state is ValueState.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error interval requires a reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state.value,
            "low": self.low,
            "high": self.high,
        }
        for key in ("source", "reason", "unit"):
            value = getattr(self, key)
            if value:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IntervalEstimate":
        data = _as_mapping(raw, "interval")
        return cls(
            state=_enum(ValueState, data.get("state"), "interval.state"),
            low=data.get("low"),
            high=data.get("high"),
            source=_input_string(data.get("source", _MISSING), "interval.source"),
            reason=_input_string(data.get("reason", _MISSING), "interval.reason"),
            unit=_input_string(data.get("unit", _MISSING), "interval.unit"),
        )


@dataclass(frozen=True)
class Coverage(_FinalValueObject):
    method: str | None = None
    observed: float | None = None
    history_n: int = 0
    drift_state: DriftState = DriftState.UNKNOWN

    def __post_init__(self) -> None:
        _require_enum(self.drift_state, DriftState, "forecast.coverage.drift_state")
        if self.method is not None:
            _non_blank_string(self.method, "forecast.coverage.method")
        if self.observed is not None:
            value = _number(self.observed, "forecast.coverage.observed")
            if value > 1:
                raise SessionEconomicsContractError(
                    "forecast.coverage.observed must be between 0 and 1"
                )
        if (
            isinstance(self.history_n, bool)
            or not isinstance(self.history_n, int)
            or self.history_n < 0
        ):
            raise SessionEconomicsContractError(
                "forecast.coverage.history_n must be a non-negative integer"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "observed": self.observed,
            "history_n": self.history_n,
            "drift_state": self.drift_state.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Coverage":
        data = _as_mapping(raw, "forecast.coverage")
        history_n = data.get("history_n", 0)
        if isinstance(history_n, bool) or not isinstance(history_n, int):
            raise SessionEconomicsContractError(
                "forecast.coverage.history_n must be a non-negative integer"
            )
        return cls(
            method=data.get("method"),
            observed=data.get("observed"),
            history_n=history_n,
            drift_state=_enum(
                DriftState,
                data.get("drift_state", DriftState.UNKNOWN.value),
                "forecast.coverage.drift_state",
            ),
        )


@dataclass(frozen=True)
class Forecast(_FinalValueObject):
    status: ForecastStatus
    remaining_tokens_likely_50: IntervalEstimate
    remaining_tokens_ceiling_90: NumericValue
    remaining_cost_usd_likely_50: IntervalEstimate
    remaining_cost_usd_ceiling_90: NumericValue
    expected_turns: IntervalEstimate
    coverage: Coverage
    predicted_block_probability: NumericValue
    reason: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.status, ForecastStatus, "forecast.status")
        for name in (
            "remaining_tokens_likely_50",
            "remaining_cost_usd_likely_50",
            "expected_turns",
        ):
            _require_value_object(getattr(self, name), IntervalEstimate, f"forecast.{name}")
        for name in (
            "remaining_tokens_ceiling_90",
            "remaining_cost_usd_ceiling_90",
            "predicted_block_probability",
        ):
            _require_value_object(getattr(self, name), NumericValue, f"forecast.{name}")
        _require_value_object(self.coverage, Coverage, "forecast.coverage")
        _optional_string(self.reason, "forecast.reason")
        token_predictions = (
            self.remaining_tokens_likely_50.state,
            self.remaining_tokens_ceiling_90.state,
            self.expected_turns.state,
        )
        all_predictions = token_predictions + (
            self.remaining_cost_usd_likely_50.state,
            self.remaining_cost_usd_ceiling_90.state,
            self.predicted_block_probability.state,
        )
        if self.status is ForecastStatus.AVAILABLE:
            if any(state is not ValueState.ESTIMATED for state in token_predictions):
                raise SessionEconomicsContractError(
                    "available forecast requires token range, ceiling, and turns"
                )
            cost_states = {
                self.remaining_cost_usd_likely_50.state,
                self.remaining_cost_usd_ceiling_90.state,
            }
            if len(cost_states) != 1:
                raise SessionEconomicsContractError(
                    "forecast cost range and ceiling must share one state"
                )
            assert self.remaining_tokens_likely_50.high is not None
            assert self.remaining_tokens_ceiling_90.value is not None
            if self.remaining_tokens_ceiling_90.value < self.remaining_tokens_likely_50.high:
                raise SessionEconomicsContractError(
                    "90% token ceiling must not be below the likely-50 high"
                )
            if self.remaining_cost_usd_likely_50.state is ValueState.ESTIMATED:
                assert self.remaining_cost_usd_likely_50.high is not None
                assert self.remaining_cost_usd_ceiling_90.value is not None
                if (
                    self.remaining_cost_usd_ceiling_90.value
                    < self.remaining_cost_usd_likely_50.high
                ):
                    raise SessionEconomicsContractError(
                        "90% cost ceiling must not be below the likely-50 high"
                    )
            if (
                not self.coverage.method
                or self.coverage.observed is None
                or self.coverage.history_n <= 0
            ):
                raise SessionEconomicsContractError(
                    "available forecast requires observed coverage and positive history"
                )
            probability = self.predicted_block_probability
            if probability.state is ValueState.ESTIMATED:
                assert probability.value is not None
                if probability.value > 1:
                    raise SessionEconomicsContractError(
                        "predicted block probability must be between 0 and 1"
                    )
            elif probability.state is ValueState.OBSERVED:
                raise SessionEconomicsContractError(
                    "predicted block probability cannot be observed"
                )
        elif any(state in {ValueState.OBSERVED, ValueState.ESTIMATED} for state in all_predictions):
            raise SessionEconomicsContractError(
                f"{self.status.value} forecast cannot carry predictions"
            )
        if self.status is ForecastStatus.ERROR and not _has_visible_text(self.reason):
            raise SessionEconomicsContractError("error forecast requires a reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "remaining_tokens_likely_50": self.remaining_tokens_likely_50.to_dict(),
            "remaining_tokens_ceiling_90": self.remaining_tokens_ceiling_90.to_dict(),
            "remaining_cost_usd_likely_50": self.remaining_cost_usd_likely_50.to_dict(),
            "remaining_cost_usd_ceiling_90": self.remaining_cost_usd_ceiling_90.to_dict(),
            "expected_turns": self.expected_turns.to_dict(),
            "coverage": self.coverage.to_dict(),
            "predicted_block_probability": self.predicted_block_probability.to_dict(),
        }
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Forecast":
        data = _as_mapping(raw, "forecast")
        return cls(
            status=_enum(ForecastStatus, data.get("status"), "forecast.status"),
            remaining_tokens_likely_50=IntervalEstimate.from_dict(
                data.get("remaining_tokens_likely_50")
            ),
            remaining_tokens_ceiling_90=NumericValue.from_dict(
                data.get("remaining_tokens_ceiling_90")
            ),
            remaining_cost_usd_likely_50=IntervalEstimate.from_dict(
                data.get("remaining_cost_usd_likely_50")
            ),
            remaining_cost_usd_ceiling_90=NumericValue.from_dict(
                data.get("remaining_cost_usd_ceiling_90")
            ),
            expected_turns=IntervalEstimate.from_dict(data.get("expected_turns")),
            coverage=Coverage.from_dict(data.get("coverage")),
            predicted_block_probability=NumericValue.from_dict(
                data.get("predicted_block_probability")
            ),
            reason=_input_string(data.get("reason", _MISSING), "forecast.reason"),
        )


@dataclass(frozen=True)
class SessionEconomics(_FinalValueObject):
    """Immutable shared session-economics payload."""

    as_of: str
    session: SessionRef
    facts: SessionFacts
    state: SessionState
    runway: Runway
    forecast: Forecast
    advisory: None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, expected_type in (
            ("session", SessionRef),
            ("facts", SessionFacts),
            ("state", SessionState),
            ("runway", Runway),
            ("forecast", Forecast),
        ):
            _require_value_object(
                getattr(self, name),
                expected_type,
                f"session economics.{name}",
            )
        if type(self.schema_version) is not str or self.schema_version != SCHEMA_VERSION:
            raise UnsupportedSessionEconomicsVersion(
                f"unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        _timestamp(self.as_of, "as_of")
        if self.advisory is not None:
            raise SessionEconomicsContractError("OSS session economics requires advisory: null")

        burn_cost_state = self.state.burn_usd_per_turn.state
        if burn_cost_state is ValueState.ESTIMATED:
            if self.facts.cost_usd.state is not ValueState.ESTIMATED:
                raise SessionEconomicsContractError(
                    "estimated USD burn requires fresh rate-card cost provenance"
                )
        elif burn_cost_state is ValueState.OBSERVED:
            if self.facts.cost_usd.state is not ValueState.OBSERVED:
                raise SessionEconomicsContractError(
                    "observed USD burn requires provider-billed cost provenance"
                )

        forecast_cost_states = {
            self.forecast.remaining_cost_usd_likely_50.state,
            self.forecast.remaining_cost_usd_ceiling_90.state,
        }
        if (
            ValueState.ESTIMATED in forecast_cost_states
            and self.facts.cost_usd.state is not ValueState.ESTIMATED
        ):
            raise SessionEconomicsContractError(
                "remaining USD forecast requires fresh rate-card cost provenance"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "as_of": self.as_of,
            "session": self.session.to_dict(),
            "facts": self.facts.to_dict(),
            "state": self.state.to_dict(),
            "runway": self.runway.to_dict(),
            "forecast": self.forecast.to_dict(),
            "advisory": None,
        }

    def to_json(self) -> str:
        """Return a byte-stable JSON encoding for equivalent values."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SessionEconomics":
        data = _as_mapping(raw, "session economics")
        version = data.get("schema_version")
        if type(version) is not str or version != SCHEMA_VERSION:
            raise UnsupportedSessionEconomicsVersion(
                f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION!r}"
            )
        if "advisory" not in data:
            raise SessionEconomicsContractError(
                "OSS session economics requires explicit advisory: null"
            )
        if data["advisory"] is not None:
            raise SessionEconomicsContractError(
                "OSS session economics cannot accept a non-null advisory"
            )
        return cls(
            schema_version=version,
            as_of=_input_string(data.get("as_of", _MISSING), "as_of"),
            session=SessionRef.from_dict(data.get("session")),
            facts=SessionFacts.from_dict(data.get("facts")),
            state=SessionState.from_dict(data.get("state")),
            runway=Runway.from_dict(data.get("runway")),
            forecast=Forecast.from_dict(data.get("forecast")),
            advisory=None,
        )

    @classmethod
    def from_json(cls, raw: str) -> "SessionEconomics":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SessionEconomicsContractError("invalid session-economics JSON") from exc
        return cls.from_dict(_as_mapping(data, "session economics"))


__all__ = [
    "BindingConstraint",
    "BurnSlope",
    "CacheState",
    "CostBasis",
    "CostValue",
    "Coverage",
    "DriftState",
    "Forecast",
    "ForecastStatus",
    "GuardState",
    "IntervalEstimate",
    "ModelRef",
    "NumericValue",
    "PriceFreshness",
    "RateProvenance",
    "Runway",
    "RunwayStatus",
    "SCHEMA_VERSION",
    "SessionEconomics",
    "SessionEconomicsContractError",
    "SessionFacts",
    "SessionRef",
    "SessionState",
    "UnsupportedSessionEconomicsVersion",
    "ValueState",
]
