# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.3 — config loader for ``~/.tokenpak/policy.yaml``.

Reads the ``intent_policy`` block (per Phase 2.4 spec §3 schema)
into a :class:`PolicyEngineConfig`. Three safety invariants are
**force-applied** regardless of what the file says:

  1. ``dry_run`` is forced ``True``.
  2. ``allow_auto_routing`` is forced ``False``.
  3. ``suggestion_surface.response_headers`` is forced ``False``.

A user who tries to flip any of these via config will see a
``ConfigSafetyOverride`` warning logged but the runtime stays
safe. The loader **never raises on caller paths** — a missing
file, parse error, or unknown key falls back to the hard-coded
default config.

Resolution order:

  1. ``$TOKENPAK_HOME/policy.yaml``
  2. ``~/.tokenpak/policy.yaml``
  3. (no fallback — return default config)

The loader is read-only. It does not write to ``policy.yaml`` and
never modifies any other on-disk state.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tokenpak.proxy.intent_policy_engine import (
    PolicyEngineConfig,
    SuggestionSurfaceConfig,
)

logger = logging.getLogger(__name__)


VALID_MODES: Tuple[str, ...] = ("observe_only", "suggest", "confirm", "enforce")
"""Modes the schema understands. ``confirm`` and ``enforce`` are
reserved for Phase 2.5 / 2.6; loading them in 2.4.3 is treated as
an invalid value and the loader falls back to ``observe_only`` per
the directive's "fail closed" rule.
"""

PERMITTED_MODES_2_4_3: Tuple[str, ...] = ("observe_only", "suggest")
"""Modes Phase 2.4.3 will accept as the resolved value. Anything
else (including ``confirm`` / ``enforce``) is downgraded to
``observe_only`` with a warning."""


def _candidate_paths() -> List[Path]:
    """Return ordered list of candidate config paths."""
    out: List[Path] = []
    home = os.environ.get("TOKENPAK_HOME")
    if home:
        out.append(Path(home) / "policy.yaml")
    out.append(Path.home() / ".tokenpak" / "policy.yaml")
    return out


def _read_yaml_safely(path: Path) -> Optional[Dict[str, Any]]:
    """Read + parse YAML from ``path``. Returns ``None`` on any failure."""
    if not path.is_file():
        return None
    try:
        # PyYAML is already a tokenpak dependency for slot
        # definitions; no new dependency added.
        import yaml  # noqa: I001 — local import keeps import cost off the hot path
    except Exception:  # noqa: BLE001
        logger.warning(
            "intent_policy_config_loader: PyYAML unavailable; falling back to default config"
        )
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intent_policy_config_loader: failed to parse %s: %r — falling back to default",
            path,
            exc,
        )
        return None
    if not isinstance(data, dict):
        logger.warning(
            "intent_policy_config_loader: %s did not parse to a mapping; falling back to default",
            path,
        )
        return None
    return data


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1", "on"}:
            return True
        if v in {"false", "no", "0", "off"}:
            return False
    return default


def _resolve_mode(raw: Any, *, warnings_out: List[str]) -> str:
    if not isinstance(raw, str):
        if raw is not None:
            warnings_out.append(
                f"intent_policy.mode must be a string; got {type(raw).__name__!r}; "
                f"defaulting to observe_only."
            )
        return "observe_only"
    if raw not in PERMITTED_MODES_2_4_3:
        if raw in VALID_MODES:
            # confirm / enforce are reserved; fail closed.
            warnings_out.append(
                f"intent_policy.mode={raw!r} is reserved for a later sub-phase; "
                f"falling back to observe_only."
            )
        else:
            warnings_out.append(
                f"intent_policy.mode={raw!r} is not a known value; falling back to "
                f"observe_only."
            )
        return "observe_only"
    return raw


def _resolve_suggestion_surface(
    raw: Any, *, warnings_out: List[str]
) -> SuggestionSurfaceConfig:
    """Parse the suggestion_surface block. response_headers always
    resolved to False per the safety invariant.
    """
    if raw is None:
        return SuggestionSurfaceConfig()
    if not isinstance(raw, dict):
        warnings_out.append(
            "intent_policy.suggestion_surface must be a mapping; "
            "falling back to defaults."
        )
        return SuggestionSurfaceConfig()
    cli = _safe_bool(raw.get("cli"), True)
    dashboard = _safe_bool(raw.get("dashboard"), True)
    api = _safe_bool(raw.get("api"), True)
    response_headers_raw = _safe_bool(raw.get("response_headers"), False)
    if response_headers_raw:
        warnings_out.append(
            "intent_policy.suggestion_surface.response_headers cannot be "
            "enabled in Phase 2.4.3; forced to False."
        )
    return SuggestionSurfaceConfig(
        cli=cli,
        dashboard=dashboard,
        api=api,
        response_headers=False,  # forced
    )


def parse_intent_policy_block(
    block: Optional[Dict[str, Any]]
) -> Tuple[PolicyEngineConfig, List[str]]:
    """Parse the ``intent_policy`` mapping into a config + warnings list.

    Pure function — no I/O. Used both by the file loader below and
    by the ``tokenpak intent config --validate`` CLI to validate
    arbitrary YAML strings.

    Returns ``(config, warnings)``. ``warnings`` may be non-empty
    even when the loader resolves successfully — every safety
    override / invalid value emits exactly one warning string so
    the caller can surface them to the operator.
    """
    warnings_out: List[str] = []
    if block is None:
        return PolicyEngineConfig(), warnings_out
    if not isinstance(block, dict):
        warnings_out.append(
            "intent_policy block must be a mapping; falling back to default config."
        )
        return PolicyEngineConfig(), warnings_out

    mode = _resolve_mode(block.get("mode"), warnings_out=warnings_out)

    # dry_run — forced True per the safety invariant.
    raw_dry_run = block.get("dry_run", True)
    parsed_dry_run = _safe_bool(raw_dry_run, True)
    if not parsed_dry_run:
        warnings_out.append(
            "intent_policy.dry_run cannot be disabled in Phase 2.4.3; forced to True."
        )

    # allow_auto_routing — forced False per the safety invariant.
    raw_aar = block.get("allow_auto_routing", False)
    parsed_aar = _safe_bool(raw_aar, False)
    if parsed_aar:
        warnings_out.append(
            "intent_policy.allow_auto_routing cannot be enabled in Phase 2.4.3; "
            "forced to False."
        )

    # allow_unverified_providers — caller-controlled (Phase 2.1
    # already had it).
    allow_unverified = _safe_bool(
        block.get("allow_unverified_providers", False), False
    )

    # low_confidence_threshold — caller-controlled.
    raw_thr = block.get("low_confidence_threshold", 0.65)
    try:
        threshold = float(raw_thr)
    except (TypeError, ValueError):
        warnings_out.append(
            f"intent_policy.low_confidence_threshold={raw_thr!r} is not a number; "
            f"defaulting to 0.65."
        )
        threshold = 0.65
    if not (0.0 <= threshold <= 1.0):
        warnings_out.append(
            f"intent_policy.low_confidence_threshold={threshold} out of [0, 1]; "
            f"defaulting to 0.65."
        )
        threshold = 0.65

    # show_suggestions — defaults follow the directive: False
    # except when mode == "suggest" (auto-on).
    if mode == "suggest":
        show_default = True
    else:
        show_default = False
    show_suggestions = _safe_bool(
        block.get("show_suggestions", show_default), show_default
    )

    surface = _resolve_suggestion_surface(
        block.get("suggestion_surface"), warnings_out=warnings_out
    )

    cfg = PolicyEngineConfig(
        mode=mode,
        dry_run=True,  # forced
        allow_auto_routing=False,  # forced
        allow_unverified_providers=allow_unverified,
        low_confidence_threshold=threshold,
        show_suggestions=show_suggestions,
        suggestion_surface=surface,
    )
    return cfg, warnings_out


def load_policy_config_safely(
    *, candidate_path: Optional[Path] = None
) -> PolicyEngineConfig:
    """Resolve + parse the active host config.

    ``candidate_path`` overrides the search path (used in tests).
    Always returns a :class:`PolicyEngineConfig`; never raises.
    """
    path = candidate_path
    if path is None:
        for cand in _candidate_paths():
            if cand.is_file():
                path = cand
                break
    if path is None or not path.is_file():
        return PolicyEngineConfig()

    raw = _read_yaml_safely(path)
    if raw is None:
        return PolicyEngineConfig()

    block = raw.get("intent_policy") if isinstance(raw, dict) else None
    cfg, warnings = parse_intent_policy_block(block)
    for w in warnings:
        logger.warning("intent_policy_config_loader: %s", w)
    return cfg


def resolve_active_config_path() -> Optional[Path]:
    """Return the first candidate path that exists, or ``None``."""
    for cand in _candidate_paths():
        if cand.is_file():
            return cand
    return None


# Helper used by surfaces (CLI / dashboard / API) to decide whether
# to render the "Suggest mode active" badge for a given surface.
def is_surface_active(cfg: PolicyEngineConfig, surface: str) -> bool:
    """Return True when the host has opted in to suggest mode AND
    the named surface flag is enabled.

    Surface names: ``"cli"`` / ``"dashboard"`` / ``"api"`` /
    ``"response_headers"``.
    """
    if not cfg.is_suggest_mode():
        return False
    if not cfg.show_suggestions:
        return False
    s = cfg.suggestion_surface
    return {
        "cli": s.cli,
        "dashboard": s.dashboard,
        "api": s.api,
        "response_headers": s.response_headers,
    }.get(surface, False)


__all__ = [
    "PERMITTED_MODES_2_4_3",
    "VALID_MODES",
    "is_surface_active",
    "load_policy_config_safely",
    "parse_intent_policy_block",
    "resolve_active_config_path",
]
