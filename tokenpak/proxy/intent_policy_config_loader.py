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

Phase PI-3 extension
--------------------

The loader also parses the ``intent_policy.prompt_intervention``
sub-block into a :class:`PromptInterventionRuntimeConfig`:

  - ``enabled`` (default ``False``)
  - ``mode`` — only ``"inject_guidance"``, ``"preview_only"``,
    ``"ask_clarification"`` are accepted; ``"rewrite_prompt"`` is
    rejected to ``preview_only``.
  - ``target`` — only ``"companion_context"`` is accepted in
    PI-3; ``"system"`` is downgraded with a warning;
    ``"user_message"`` is rejected outright.
  - ``require_confirmation`` (default ``True``)
  - ``allow_byte_preserve_override`` — **forced** ``False``
    (PI-3 § 1 invariant).
  - ``surfaces.claude_code_companion`` (default ``False``)
  - ``surfaces.proxy`` — **forced** ``False`` (PI-3 § 1
    invariant; the proxy never injects in PI-3).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Phase PI-3 — prompt_intervention sub-block
# ---------------------------------------------------------------------------


PI_3_VALID_MODES: Tuple[str, ...] = (
    "preview_only",
    "inject_guidance",
    "ask_clarification",
)
"""``rewrite_prompt`` is intentionally absent — PI-4-only."""

PI_3_VALID_TARGETS: Tuple[str, ...] = ("companion_context",)
"""``system`` is reserved (PI-4 ratification); ``user_message`` is
reserved indefinitely. PI-3 only accepts ``companion_context``."""


@dataclass(frozen=True)
class PromptInterventionSurfaces:
    """Per-surface enable flags. ``proxy`` is forced ``False``."""

    claude_code_companion: bool = False
    proxy: bool = False  # forced False per PI-3 § 1


@dataclass(frozen=True)
class PromptInterventionRuntimeConfig:
    """Resolved ``intent_policy.prompt_intervention`` block.

    Mirrors :class:`tokenpak.proxy.intent_prompt_patch.PromptInterventionConfig`
    but adds the ``surfaces`` sub-mapping that PI-3 introduces. Default is
    all-off — same posture as the upstream PI-1 dataclass.
    """

    enabled: bool = False
    mode: str = "preview_only"
    target: str = "companion_context"
    require_confirmation: bool = True
    allow_byte_preserve_override: bool = False  # forced False
    surfaces: PromptInterventionSurfaces = field(
        default_factory=PromptInterventionSurfaces
    )

    def is_claude_code_companion_active(self) -> bool:
        """True iff every gate aligns to allow companion-side injection."""
        return (
            self.enabled
            and self.mode == "inject_guidance"
            and self.target == "companion_context"
            and self.surfaces.claude_code_companion
            and not self.surfaces.proxy
            and not self.allow_byte_preserve_override
        )


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


def _resolve_prompt_intervention(
    raw: Any, *, warnings_out: List[str]
) -> PromptInterventionRuntimeConfig:
    """Parse the ``prompt_intervention`` sub-block per PI-3 § 1.

    Force-applied invariants:
      - ``allow_byte_preserve_override`` → ``False``
      - ``surfaces.proxy`` → ``False``
      - ``target == "user_message"`` → reject (downgrade to default)
      - ``mode == "rewrite_prompt"`` → reject (downgrade to ``preview_only``)
    """
    if raw is None:
        return PromptInterventionRuntimeConfig()
    if not isinstance(raw, dict):
        warnings_out.append(
            "intent_policy.prompt_intervention must be a mapping; "
            "falling back to defaults (disabled)."
        )
        return PromptInterventionRuntimeConfig()

    enabled = _safe_bool(raw.get("enabled"), False)

    raw_mode = raw.get("mode", "preview_only")
    if not isinstance(raw_mode, str):
        warnings_out.append(
            f"intent_policy.prompt_intervention.mode must be a string; got "
            f"{type(raw_mode).__name__!r}; defaulting to preview_only."
        )
        mode = "preview_only"
    elif raw_mode == "rewrite_prompt":
        warnings_out.append(
            "intent_policy.prompt_intervention.mode='rewrite_prompt' is "
            "unsupported in PI-3; downgrading to preview_only."
        )
        mode = "preview_only"
    elif raw_mode not in PI_3_VALID_MODES:
        warnings_out.append(
            f"intent_policy.prompt_intervention.mode={raw_mode!r} is not a "
            f"known value; defaulting to preview_only."
        )
        mode = "preview_only"
    else:
        mode = raw_mode

    raw_target = raw.get("target", "companion_context")
    if not isinstance(raw_target, str):
        warnings_out.append(
            f"intent_policy.prompt_intervention.target must be a string; got "
            f"{type(raw_target).__name__!r}; defaulting to companion_context."
        )
        target = "companion_context"
    elif raw_target == "user_message":
        warnings_out.append(
            "intent_policy.prompt_intervention.target='user_message' is "
            "reserved indefinitely; rejecting and defaulting to "
            "companion_context."
        )
        target = "companion_context"
    elif raw_target == "system":
        warnings_out.append(
            "intent_policy.prompt_intervention.target='system' is reserved "
            "for PI-4; downgrading to companion_context."
        )
        target = "companion_context"
    elif raw_target not in PI_3_VALID_TARGETS:
        warnings_out.append(
            f"intent_policy.prompt_intervention.target={raw_target!r} is not "
            f"a known value; defaulting to companion_context."
        )
        target = "companion_context"
    else:
        target = raw_target

    require_confirmation = _safe_bool(raw.get("require_confirmation"), True)

    raw_byte_preserve = _safe_bool(
        raw.get("allow_byte_preserve_override"), False
    )
    if raw_byte_preserve:
        warnings_out.append(
            "intent_policy.prompt_intervention.allow_byte_preserve_override "
            "cannot be enabled in PI-3; forced to False."
        )
    allow_byte_preserve_override = False  # forced

    raw_surfaces = raw.get("surfaces")
    if raw_surfaces is None:
        surfaces = PromptInterventionSurfaces()
    elif not isinstance(raw_surfaces, dict):
        warnings_out.append(
            "intent_policy.prompt_intervention.surfaces must be a mapping; "
            "falling back to defaults (all surfaces off)."
        )
        surfaces = PromptInterventionSurfaces()
    else:
        cc = _safe_bool(raw_surfaces.get("claude_code_companion"), False)
        proxy_raw = _safe_bool(raw_surfaces.get("proxy"), False)
        if proxy_raw:
            warnings_out.append(
                "intent_policy.prompt_intervention.surfaces.proxy cannot be "
                "enabled in PI-3; forced to False."
            )
        surfaces = PromptInterventionSurfaces(
            claude_code_companion=cc,
            proxy=False,  # forced
        )

    return PromptInterventionRuntimeConfig(
        enabled=enabled,
        mode=mode,
        target=target,
        require_confirmation=require_confirmation,
        allow_byte_preserve_override=allow_byte_preserve_override,
        surfaces=surfaces,
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


def parse_prompt_intervention_block(
    raw: Any,
) -> Tuple[PromptInterventionRuntimeConfig, List[str]]:
    """Public wrapper around :func:`_resolve_prompt_intervention`.

    Pure function. Returns ``(config, warnings)``. Used by the
    PI-3 application library and the CLI validator.
    """
    warnings_out: List[str] = []
    cfg = _resolve_prompt_intervention(raw, warnings_out=warnings_out)
    return cfg, warnings_out


def load_prompt_intervention_config_safely(
    *, candidate_path: Optional[Path] = None
) -> PromptInterventionRuntimeConfig:
    """Resolve + parse the active host's ``prompt_intervention`` block.

    Always returns a :class:`PromptInterventionRuntimeConfig`; never
    raises. ``candidate_path`` overrides the search path.
    """
    path = candidate_path
    if path is None:
        for cand in _candidate_paths():
            if cand.is_file():
                path = cand
                break
    if path is None or not path.is_file():
        return PromptInterventionRuntimeConfig()

    raw = _read_yaml_safely(path)
    if raw is None:
        return PromptInterventionRuntimeConfig()

    intent_block = raw.get("intent_policy") if isinstance(raw, dict) else None
    if not isinstance(intent_block, dict):
        return PromptInterventionRuntimeConfig()
    pi_block = intent_block.get("prompt_intervention")
    cfg, warnings = parse_prompt_intervention_block(pi_block)
    for w in warnings:
        logger.warning("intent_policy_config_loader: %s", w)
    return cfg


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
    "PI_3_VALID_MODES",
    "PI_3_VALID_TARGETS",
    "PromptInterventionRuntimeConfig",
    "PromptInterventionSurfaces",
    "VALID_MODES",
    "is_surface_active",
    "load_policy_config_safely",
    "load_prompt_intervention_config_safely",
    "parse_intent_policy_block",
    "parse_prompt_intervention_block",
    "resolve_active_config_path",
]
