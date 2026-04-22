"""``PolicyResolver`` — ``RouteClass`` → :class:`Policy`.

Presets live as YAML in ``tokenpak/services/policy_service/presets/``,
one file per :class:`~tokenpak.core.routing.route_class.RouteClass`.
At import time the resolver loads every preset into a dict and returns
immutable :class:`Policy` instances on lookup.

Missing preset files are non-fatal — the resolver falls back to
:data:`~tokenpak.core.routing.policy.DEFAULT_POLICY` and logs a warning.

Environment overrides respect existing Policy fields but NEVER add new
fields — that's the "no env toggles parallel to Policy" anti-pattern.
Overrides are intended for operators debugging one route in production,
not as a general configuration layer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Optional

from tokenpak.core.routing.policy import DEFAULT_POLICY, Policy
from tokenpak.core.routing.route_class import RouteClass

logger = logging.getLogger(__name__)


_PRESETS_DIR = Path(__file__).parent / "presets"


def _coerce_policy(raw: dict) -> Policy:
    """Build a Policy from a preset dict, trusting field types."""
    # Filter to known Policy fields so presets can carry documentation
    # keys (e.g. "_description") without breaking construction.
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(Policy)}
    kwargs = {k: v for k, v in raw.items() if k in field_names}
    return Policy(**kwargs)


class PolicyResolver:
    """Loads preset YAMLs, resolves ``RouteClass`` → :class:`Policy`."""

    def __init__(self, presets_dir: Optional[Path] = None) -> None:
        self._presets_dir = presets_dir or _PRESETS_DIR
        self._cache: dict[RouteClass, Policy] = {}
        self._load()

    def _load(self) -> None:
        # Local yaml import — avoids a hard dep if an entrypoint lands
        # before PyYAML is installed (yaml is already a tokenpak runtime
        # dependency, but this keeps the import failure message clean).
        try:
            import yaml
        except ImportError:
            logger.warning(
                "PyYAML not installed; all routes will use DEFAULT_POLICY"
            )
            return

        if not self._presets_dir.exists():
            logger.warning(
                "Policy presets dir missing at %s — all routes use DEFAULT_POLICY",
                self._presets_dir,
            )
            return

        for rc in RouteClass:
            preset_file = self._presets_dir / f"{rc.value}.yaml"
            if not preset_file.exists():
                continue
            try:
                raw = yaml.safe_load(preset_file.read_text()) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to parse preset %s: %s — using DEFAULT_POLICY",
                    preset_file,
                    exc,
                )
                continue
            try:
                self._cache[rc] = _coerce_policy(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Preset %s invalid: %s — using DEFAULT_POLICY", preset_file, exc
                )

    def resolve(self, route_class: RouteClass) -> Policy:
        """Return the Policy for ``route_class`` (or DEFAULT_POLICY)."""
        policy = self._cache.get(route_class, DEFAULT_POLICY)
        return self._apply_env_overrides(policy)

    # ── private ─────────────────────────────────────────────────────────

    def _apply_env_overrides(self, policy: Policy) -> Policy:
        """Apply TOKENPAK_POLICY_* env overrides to a resolved Policy.

        Only overrides fields that already exist on Policy. Typos like
        ``TOKENPAK_POLICY_INJECTED_ENABLED`` are silently ignored — the
        canonical field list is the Policy dataclass, not env vars.
        """
        overrides = {}
        for env_key, value in os.environ.items():
            if not env_key.startswith("TOKENPAK_POLICY_"):
                continue
            field_name = env_key[len("TOKENPAK_POLICY_"):].lower()
            import dataclasses

            field_names = {f.name for f in dataclasses.fields(Policy)}
            if field_name not in field_names:
                continue
            # Best-effort coercion — match the field's current type.
            current = getattr(policy, field_name)
            try:
                if isinstance(current, bool):
                    overrides[field_name] = value.lower() in ("1", "true", "yes", "on")
                elif isinstance(current, int):
                    overrides[field_name] = int(value)
                else:
                    overrides[field_name] = value
            except (TypeError, ValueError):
                continue
        if overrides:
            return replace(policy, **overrides)
        return policy


_default: Optional[PolicyResolver] = None


def get_resolver() -> PolicyResolver:
    """Shared module-level resolver — safe to reuse across pipelines."""
    global _default
    if _default is None:
        _default = PolicyResolver()
    return _default


__all__ = ["PolicyResolver", "get_resolver"]
