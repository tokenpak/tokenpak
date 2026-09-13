# SPDX-License-Identifier: Apache-2.0
"""Immutable accounting intent for one serving proxy generation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .policy import _coerce_bool

_BASIS_ENV = "TOKENPAK_SPEND_GUARD_ACCOUNTING_BASIS"
_SWITCHES = ("TOKENPAK_SPEND_GUARD_ENABLED", "TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED")


def _basis(value):
    if type(value) is not str or value not in ("priced_usage", "provider_tokens"):
        raise ValueError("unsupported accounting basis")
    return value


def explicitly_disabled() -> bool:
    return any(name in os.environ and not _coerce_bool(os.environ[name]) for name in _SWITCHES)


@dataclass(frozen=True)
class ServingBasis:
    accounting_basis: str

    def __post_init__(self):
        _basis(self.accounting_basis)


def check_environment(intent: ServingBasis | None = None) -> None:
    """No config I/O: disabled switches cannot erase a captured token basis."""
    explicit = _basis(os.environ[_BASIS_ENV]) if _BASIS_ENV in os.environ else None
    if intent is not None and explicit is not None and explicit != intent.accounting_basis:
        raise ValueError("accounting basis changed; start a new serving generation")
    selected = intent.accounting_basis if intent is not None else explicit
    if selected == "provider_tokens" and explicitly_disabled():
        raise ValueError("provider-token accounting requires enabled guard and reservations")


def check_policy(config, intent: ServingBasis | None) -> None:
    if intent is not None and config.accounting_basis != intent.accounting_basis:
        raise ValueError("accounting basis changed; start a new serving generation")


def capture_serving_basis() -> ServingBasis:
    """Resolve once before listening, using the guard's path and precedence.

    Missing/malformed legacy files did not prevent disabled forwarding. Keep
    that behavior when no token declaration is known, without moving reads to
    requests. A parsed declaration is validated outside the decoder fallback.
    """
    from tokenpak.core import config_loader
    from tokenpak.proxy.guard_snapshot_endpoint import _raw_config

    from .policy import load_config

    explicit = _basis(os.environ[_BASIS_ENV]) if _BASIS_ENV in os.environ else None
    if explicit is not None:
        check_environment(ServingBasis(explicit))
    yaml_error = getattr(getattr(config_loader, "_yaml", None), "YAMLError", ValueError)
    try:
        raw = _raw_config()
    except (FileNotFoundError, ValueError, UnicodeError, yaml_error):
        if explicit == "provider_tokens":
            raise
        raw = {}
    legacy = raw.get("spend_guard") or {}
    canonical = raw.get("tip_spend_guard") or {}
    # Invalid legacy section shapes are not a basis declaration. Preserve
    # existing legacy startup behavior; enabled requests still validate policy.
    sections = {
        **(legacy if isinstance(legacy, dict) else {}),
        **(canonical if isinstance(canonical, dict) else {}),
    }
    selected = _basis(
        explicit if explicit is not None else sections.get("accounting_basis", "priced_usage")
    )
    intent = ServingBasis(selected)
    check_environment(intent)
    if selected == "provider_tokens":
        # Validate known token intent with the full policy. No error here may
        # be caught and converted to a legacy priced default.
        check_policy(load_config(raw_config=raw), intent)
    return intent
