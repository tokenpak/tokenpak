# SPDX-License-Identifier: Apache-2.0
"""The measured-data contract.

Every user-facing number TokenPak prints is either a *measurement* or it is
not. Before this module existed, four surfaces (``report``, ``stats``,
``diff``, ``cost``) rendered "no data" as ``0.0%`` / ``$0.00`` / ``0ms`` /
"all blocks retained", which reads to a user as "TokenPak measured this and
it was zero". That is a false statement about their workload.

This module makes that failure mode unrepresentable:

* A :class:`Measured` in state ``MEASURED`` **must** carry a value.
* A :class:`Measured` in any other state **must not** carry one — its
  ``value`` is ``None``, and :meth:`to_json` emits JSON ``null``.
* There is no code path that turns an unmeasured quantity into ``0``.

Construction is validated, not merely documented, so a surface cannot
regress into zero-defaulting without raising.

States
------
``MEASURED``
    A real observation. Carries the value and the source that produced it.
``NO_DATA``
    The source is reachable and healthy; there is simply nothing recorded
    yet. This is the correct state for a fresh install. Renders as a
    forward-looking hint, not a failure.
``UNAVAILABLE``
    The source could not be consulted (absent database, unreachable proxy,
    feature disabled). Distinct from ``NO_DATA``: we do not know whether a
    value exists.
``ERROR``
    The source was consulted and failed (corrupt file, schema mismatch).
    Always carries a reason.

Usage::

    from tokenpak.core.contracts import measured, no_data, unavailable

    if db is None:
        savings = unavailable("monitor_db_not_found")
    elif rows == 0:
        savings = no_data("no requests recorded yet")
    else:
        savings = measured(total_usd, source="monitor_db")

    print(f"  Saved  {savings.render(fmt='usd')}")
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union

Number = Union[int, float]


class DataState(str, Enum):
    """Lifecycle state of a user-facing quantity."""

    MEASURED = "measured"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


#: Rendering for each non-measured state. Deliberately never numeric.
_PLACEHOLDER: Dict[DataState, str] = {
    DataState.NO_DATA: "not yet measured",
    DataState.UNAVAILABLE: "unavailable",
    DataState.ERROR: "error",
}


@dataclass(frozen=True)
class Measured:
    """A quantity that knows whether it was actually measured.

    Prefer the module-level constructors (:func:`measured`, :func:`no_data`,
    :func:`unavailable`, :func:`error`) over calling this directly.
    """

    state: DataState
    value: Optional[Number] = None
    reason: str = ""
    source: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        # Fail loud rather than silently producing a misleading render. The
        # whole point of this type is that these two shapes cannot exist.
        if self.state is DataState.MEASURED:
            if self.value is None:
                raise ValueError(
                    "Measured(MEASURED) requires a value; use no_data()/"
                    "unavailable()/error() when there is nothing to report"
                )
        elif self.value is not None:
            raise ValueError(
                f"Measured({self.state.value}) must not carry a value "
                f"(got {self.value!r}); an unmeasured quantity is null, not a number"
            )
        if self.state is DataState.ERROR and not self.reason:
            raise ValueError("Measured(ERROR) requires a reason")

    # -- predicates ---------------------------------------------------------

    @property
    def is_measured(self) -> bool:
        return self.state is DataState.MEASURED

    def __bool__(self) -> bool:
        """Truthiness tracks *measured*, not the value.

        So ``if savings:`` means "did we measure this", which is the check
        callers actually want. A measured zero stays truthy — a real
        observation of zero savings is still a real observation.
        """
        return self.is_measured

    # -- rendering ----------------------------------------------------------

    def render(self, fmt: str = "auto", placeholder: Optional[str] = None) -> str:
        """Render for a terminal surface.

        ``fmt`` is one of ``auto``, ``usd``, ``pct``, ``int``, ``ms``,
        ``tokens``. Non-measured states ignore ``fmt`` entirely and render
        the placeholder — there is no format that can turn them into a number.
        """
        if not self.is_measured:
            return placeholder if placeholder is not None else _PLACEHOLDER[self.state]

        v = self.value
        assert v is not None  # guaranteed by __post_init__
        if fmt == "usd":
            return f"${v:,.2f}"
        if fmt == "pct":
            return f"{v:.1f}%"
        if fmt == "int" or fmt == "tokens":
            return f"{int(v):,}"
        if fmt == "ms":
            return f"{v:.1f}ms"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return f"{v:,}"

    def explain(self) -> str:
        """Render with the reason appended, for diagnostic surfaces."""
        base = self.render()
        if not self.is_measured and self.reason:
            return f"{base} ({self.reason})"
        return base

    # -- serialization ------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        """Machine-readable form. ``value`` is ``null`` unless measured."""
        out: Dict[str, Any] = {
            "state": self.state.value,
            "value": self.value if self.is_measured else None,
        }
        if self.unit:
            out["unit"] = self.unit
        if self.source:
            out["source"] = self.source
        if self.reason:
            out["reason"] = self.reason
        return out


# -- constructors -----------------------------------------------------------


def measured(value: Number, *, source: str = "", unit: str = "") -> Measured:
    """A real observation. ``value`` may legitimately be ``0``."""
    if value is None:  # pragma: no cover - defensive
        raise ValueError("measured() requires a value; use no_data()/unavailable()")
    return Measured(DataState.MEASURED, value=value, source=source, unit=unit)


def no_data(reason: str = "", *, source: str = "", unit: str = "") -> Measured:
    """Source is healthy, nothing recorded yet (the fresh-install state)."""
    return Measured(DataState.NO_DATA, reason=reason, source=source, unit=unit)


def unavailable(reason: str = "", *, source: str = "", unit: str = "") -> Measured:
    """Source could not be consulted; existence of a value is unknown."""
    return Measured(DataState.UNAVAILABLE, reason=reason, source=source, unit=unit)


def error(reason: str, *, source: str = "", unit: str = "") -> Measured:
    """Source was consulted and failed. ``reason`` is required."""
    return Measured(DataState.ERROR, reason=reason, source=source, unit=unit)
