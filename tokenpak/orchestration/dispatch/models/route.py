"""DispatchRoute record."""

from __future__ import annotations

from pydantic import Field, field_validator

from .common import DispatchBaseModel, StationLoopPolicy, _validate_capability_list
from .enums import RiskLevel


class RouteStation(DispatchBaseModel):
    """A single station definition within a route.

    Either ``required_role`` (worker stations) or ``system_component``
    (system-component stations) is set; ``required_capabilities`` is
    registry-bound and validated against the capability registry.
    """

    id: str
    required_role: str | None = Field(
        default=None, description='e.g. "builder", "reviewer"; null for system_component'
    )
    system_component: str | None = Field(
        default=None, description='e.g. "delivery_dock"; null for worker stations'
    )
    prompt_overlay: str | None = Field(
        default=None, description='e.g. "overlay.code_builder.v1"'
    )
    required_capabilities: list[str] = Field(default_factory=list)
    loop_policy: StationLoopPolicy | None = Field(
        default=None, description="overrides worker/system default"
    )
    gates: list[str] = Field(
        default_factory=list, description='e.g. ["acceptance_gate", "delivery_gate"]'
    )
    output_schema: str = Field(description='e.g. "station_result.v1"')

    @field_validator("required_capabilities")
    @classmethod
    def _check_capabilities(cls, value: list[str]) -> list[str]:
        return _validate_capability_list(value)


class RouteTriggers(DispatchBaseModel):
    """Route trigger declaration."""

    intents: list[str] = Field(default_factory=list)


class RouteRetryPolicy(DispatchBaseModel):
    """Route-level retry policy."""

    max_station_retries: int = 1
    escalate_after_failure: bool = False


class RouteDelivery(DispatchBaseModel):
    """Route-level delivery package composition flags."""

    include_summary: bool = True
    include_files_changed: bool = True
    include_tests: bool = True
    include_risks: bool = True
    include_next_steps: bool = True


class DispatchRoute(DispatchBaseModel):
    """A named, versioned workflow route."""

    id: str = Field(description='"route.<name>.v<n>"')
    name: str
    description: str
    triggers: RouteTriggers = Field(default_factory=RouteTriggers)
    default_risk: RiskLevel

    stations: list[RouteStation] = Field(default_factory=list)
    retry_policy: RouteRetryPolicy = Field(default_factory=RouteRetryPolicy)
    delivery: RouteDelivery = Field(default_factory=RouteDelivery)


__all__ = [
    "RouteStation",
    "RouteTriggers",
    "RouteRetryPolicy",
    "RouteDelivery",
    "DispatchRoute",
]
