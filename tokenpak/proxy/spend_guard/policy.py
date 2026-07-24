# SPDX-License-Identifier: Apache-2.0
"""Threshold-based policy engine for spend guard.

Turns a :class:`RiskEstimate` into a :class:`PreflightDecision` by comparing
the projected request against the configured policy bands:

    allow < warn < block < hard_block (hard_stop)

- ``allow`` — quietly forward; not surfaced.
- ``warn`` — forward but emit a warn audit row (advisory; no client UX yet).
- ``block`` — caller can release with Yes/[TIP].
- ``hard_block`` — cannot be released; even ``[TIP: bypass=on]`` /
  ``[TIP: allow=once]`` does NOT cross it.

**Canonical default basis (2026-05-11 rev 2):**
context-window utilisation percent against the selected model's max
context window — applied universally to every agent profile.

    default_basis                       = context_window_percent
    default_context_window_percent      = 90    # soft block
    hard_stop_context_window_percent    = 100   # absolute ceiling (no bypass)

The 90% line is the soft-block threshold (operator may release with Yes/
``[TIP: allow=once]``). The 100% line is the absolute hard stop — by
definition the request cannot fit in the model's window, and no override
crosses it.

Dollar-denominated bands (``block_cost_usd``, ``session_block_cost_usd``,
``hard_block_cost_usd``) remain available but are **opt-in only**: they
default to ``0.0`` (disabled) and only engage when an operator explicitly
sets them via ``~/.tokenpak/config.yaml`` or ``TOKENPAK_SPEND_GUARD_*``
env vars (or sets ``dollar_cap_enabled_by_default: true`` on the profile).

Per-request token bands (``warn_tokens``, ``block_tokens``,
``hard_block_tokens``) remain as advisory guardrails against
single-runaway-prompt cases where the model's max context is unknown.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .contracts import PreflightDecision, RiskEstimate, TIPDirective

# Default fraction of a model's max context above which a request is held
# under the LEGACY token-band fallback (used when ``default_basis`` is set
# to ``"context_window_tokens"`` for backward compat; or when explicitly
# called via :func:`derive_block_threshold`).
# Centralized so callers (decide(), tests) reference one value.
DEFAULT_BLOCK_RATIO: float = 0.80

# Canonical default basis name. Kept as a string (not Enum) to avoid forcing
# downstream consumers to import the enum just to inspect the value.
DEFAULT_BASIS_CONTEXT_WINDOW_PERCENT: str = "context_window_percent"
DEFAULT_BASIS_DOLLAR: str = "dollar"  # opt-in alternate basis

# Legacy dollar-band field names — when set explicitly, a DeprecationWarning
# fires and the dollar plane engages even if ``dollar_cap_enabled_by_default``
# is false. These names are matched against both YAML keys and env-var
# overrides during :func:`load_config`.
_LEGACY_DOLLAR_FIELDS: tuple[str, ...] = (
    "block_cost_usd",
    "hard_block_cost_usd",
    "session_block_cost_usd",
    "default_dollar_cap",
)


def derive_block_threshold(
    model_max_context_tokens: Optional[int],
    ratio: float = DEFAULT_BLOCK_RATIO,
    fallback_tokens: Optional[int] = None,
) -> Optional[int]:
    """Derive the soft-block token threshold from a model's max context.

    Pure function. No I/O. No global state. Trivially testable.

    Returns ``floor(model_max_context_tokens * ratio)`` when the context is
    known and positive. Returns ``fallback_tokens`` when the context is
    unknown (``None`` or non-positive) — the caller is then responsible for
    deciding what to do with the fallback (the spend guard's caller layers
    a final ``hard_block_tokens`` cap on whichever value is returned).

    The result is also bounded to never exceed ``model_max_context_tokens``
    (a ``ratio > 1`` is clamped to the model's max).

    Args:
        model_max_context_tokens: Max input+output context the selected
            model accepts, in tokens. ``None`` or non-positive → fallback.
        ratio: Fraction of context above which to block.
            Default :data:`DEFAULT_BLOCK_RATIO` (0.80). Must be in (0, 1]
            for the dynamic path; values outside that range fall through
            to ``fallback_tokens``.
        fallback_tokens: Returned when the dynamic path can't apply. May
            itself be ``None`` — caller handles.

    Returns:
        Integer token threshold, or ``fallback_tokens`` (which may be
        ``None``).

    Examples::

        derive_block_threshold(1_000_000)   # → 800_000
        derive_block_threshold(  200_000)   # → 160_000
        derive_block_threshold(  500_000)   # → 400_000
        derive_block_threshold(None, fallback_tokens=500_000)   # → 500_000
        derive_block_threshold(0,    fallback_tokens=500_000)   # → 500_000
    """
    if not isinstance(ratio, (int, float)) or ratio <= 0 or ratio > 1:
        return fallback_tokens
    if model_max_context_tokens is None:
        return fallback_tokens
    if not isinstance(model_max_context_tokens, int) or model_max_context_tokens <= 0:
        return fallback_tokens
    derived = int(model_max_context_tokens * ratio)
    # Never exceed the model's context (defensive against ratio rounding).
    if derived > model_max_context_tokens:
        derived = model_max_context_tokens
    return derived


@dataclass
class SpendGuardConfig:
    """Resolved threshold + behavior config.

    Loaded from ``~/.tokenpak/config.yaml`` ``spend_guard:`` block by
    :func:`load_config` (``tip_spend_guard:`` is accepted as an alias).
    Env-var overrides take precedence.

    **Defaults (2026-05-11 rev 2):**

    - ``default_basis = "context_window_percent"`` — block is denominated
      in context-window utilisation %, not dollars.
    - ``default_context_window_percent = 90`` — soft block at 90% of the
      selected model's max context window. Operator may release with Yes/
      ``[TIP: allow=once]``.
    - ``hard_stop_context_window_percent = 100`` — absolute hard stop;
      no override crosses it.
    - ``dollar_cap_enabled_by_default = False`` — dollar bands disabled
      unless explicitly configured. Legacy fields (``block_cost_usd``,
      ``session_block_cost_usd``, ``hard_block_cost_usd``,
      ``default_dollar_cap``) ENGAGE the dollar plane and emit a
      ``DeprecationWarning`` when set explicitly.
    - Per-request token bands (``warn_tokens``, ``block_tokens``,
      ``hard_block_tokens``) remain as advisory guardrails for the
      single-runaway-prompt case when the model's max context window is
      unknown.
    """

    enabled: bool = True

    # ── Context-window-percent basis (canonical default) ──
    # Identifies which dimension the policy denominates default blocks in.
    # ``"context_window_percent"`` (default) → use the new % basis.
    # ``"dollar"`` → use the legacy dollar-band basis (opt-in only).
    default_basis: str = DEFAULT_BASIS_CONTEXT_WINDOW_PERCENT
    # Soft-block threshold as a whole-percent integer (e.g. ``90`` = 90%).
    # Block fires when projected_input_tokens / model_max_context_window
    # ≥ this percentage. Bypassable by Yes/``[TIP: allow=once]``.
    default_context_window_percent: int = 90
    # Absolute hard-stop ceiling as a whole-percent integer. Hard-stop
    # fires when projected_input_tokens / model_max_context_window ≥ this
    # percentage. NOT bypassable — neither Yes/no nor any ``[TIP: ...]``
    # directive crosses it. Must be ≥ default_context_window_percent and
    # ≤ 100 (enforced in :func:`load_config`).
    hard_stop_context_window_percent: int = 100
    # Whether the dollar plane is engaged by default for this profile.
    # When ``False`` (default), the ``*_cost_usd`` fields below are read
    # but only enforced if any of them is set to a positive value (e.g.
    # via env-var override). When ``True``, the dollar plane is fully
    # engaged using whatever ``*_cost_usd`` values are configured.
    dollar_cap_enabled_by_default: bool = False
    # Advisory: a single declared dollar ceiling for the profile. When
    # set (and the dollar plane is engaged), the largest engaged
    # ``block_cost_usd`` derives from this value when not otherwise
    # configured. Default ``None`` — no advisory cap.
    default_dollar_cap: Optional[float] = None

    # ── Per-request token bands (advisory guardrails) ──
    # Used as fallback when the selected model's max context window is
    # unknown to the registry (see ``_context_window.get_model_max_context``).
    # When the model's context window IS known, these bands are NOT the
    # canonical defense — the context-window-% basis is.
    warn_tokens: int = 100_000
    warn_cost_usd: float = 2.0
    block_tokens: int = 500_000
    hard_block_tokens: int = 1_000_000

    # ── Dollar bands (opt-in, disabled by default) ──
    # Each defaults to ``0.0`` (disabled). Setting any of these to a
    # positive value engages the dollar plane for that band; setting one
    # also emits a DeprecationWarning at config-load time (deprecation
    # notice for the legacy dollar plane).
    block_cost_usd: float = 0.0
    hard_block_cost_usd: float = 0.0
    # Session-cumulative defense (legacy; v1.5.1). Catches the
    # death-by-1000-cuts pattern: when running spend on a session in the
    # last ``session_window_seconds`` plus this projected request would
    # exceed ``session_block_cost_usd``, the request is blocked. Default
    # ``0.0`` (disabled) under the v1.5.2 policy — the per-request
    # context-window-% guard at 90% catches the same failure mode for
    # large-context spike patterns. Set explicitly to re-enable.
    session_block_cost_usd: float = 0.0
    session_window_seconds: int = 3600

    # ── Operational knobs ──
    pending_ttl_seconds: int = 600
    audit_db_path: str = "~/.tokenpak/spend_guard.db"
    # Below this projected-cost floor we don't even audit (avoid noise).
    audit_min_cost_usd: float = 0.10

    # ── Rolling/cumulative caps (2026-05-15 post-incident P0) ──
    # Supplements the per-session cap. Catches the 2026-05-15 pattern
    # where 64 sub-cap sessions cumulated to $566 in 8 hours. Default
    # values per packet p0-rolling-spend-guard-caps-2026-05-15.md.
    rolling_caps_enabled: bool = True
    rolling_caps_window_seconds: int = 3600
    rolling_caps_per_agent_max_cost_usd: float = 20.0
    rolling_caps_per_agent_max_tokens_total: int = 5_000_000
    # cache_read caps DISABLED by default: cache_read
    # is ~90% cheaper than input/output and inflates without reflecting
    # real risk. cache_read remains recorded for observability + still
    # configurable via spend_guard.rolling_caps.* if an operator opts in.
    rolling_caps_per_agent_max_cache_read_tokens: int = 0
    rolling_caps_per_fleet_max_cost_usd: float = 60.0
    rolling_caps_per_fleet_max_tokens_total: int = 15_000_000
    rolling_caps_per_fleet_max_cache_read_tokens: int = 0


# ---------------------------------------------------------------------------
# Config loader — single source of truth for thresholds
# ---------------------------------------------------------------------------


def _coerce_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load_config(raw_config: Optional[dict[str, Any]] = None) -> SpendGuardConfig:
    """Resolve the live SpendGuardConfig.

    ``raw_config`` is the parsed ``~/.tokenpak/config.yaml`` (or any dict-like
    with a ``spend_guard:`` or ``tip_spend_guard:`` section). When omitted,
    config is read fresh from ``tokenpak.proxy.config``. Env vars override
    file config.

    **Backward compatibility:** explicit configuration of any
    legacy dollar-denominated field (``block_cost_usd``, ``hard_block_cost_usd``,
    ``session_block_cost_usd``, ``default_dollar_cap``) — whether via YAML or
    ``TOKENPAK_SPEND_GUARD_*`` env vars — engages the dollar plane for that
    band and emits a ``DeprecationWarning``. The canonical defense is the
    context-window-% basis; dollar bands are kept reachable as an opt-in
    profile override only.

    **Validation:** percent values are sanity-checked at load time:

    - ``default_context_window_percent`` must be in ``[0, 100]``.
    - ``hard_stop_context_window_percent`` must be in ``[0, 100]``.
    - ``default_context_window_percent <= hard_stop_context_window_percent``.

    Violations raise :class:`ValueError` with the offending key name.
    """
    import os

    cfg = SpendGuardConfig()

    # File defaults — accept both ``spend_guard:`` (legacy) and
    # ``tip_spend_guard:`` (canonical (2026-05-11)) keys.
    # Canonical wins on conflict; legacy is a transparent alias.
    if raw_config is not None:
        sg_legacy = (raw_config or {}).get("spend_guard") or {}
        sg_canonical = (raw_config or {}).get("tip_spend_guard") or {}
    else:
        try:
            from tokenpak.core.config_loader import load_config as _load_yaml

            _raw = _load_yaml() or {}
        except Exception:
            _raw = {}
        sg_legacy = _raw.get("spend_guard") or {}
        sg_canonical = _raw.get("tip_spend_guard") or {}
    sg = {**sg_legacy, **sg_canonical}  # canonical wins

    # Track which legacy dollar fields the operator explicitly set so we can
    # emit a single DeprecationWarning aggregating them at the end.
    legacy_dollar_set_via: dict[str, str] = {}

    if "enabled" in sg:
        cfg.enabled = _coerce_bool(sg["enabled"])

    # New canonical fields
    if "default_basis" in sg:
        cfg.default_basis = str(sg["default_basis"]).strip()
    if "default_context_window_percent" in sg:
        try:
            cfg.default_context_window_percent = int(sg["default_context_window_percent"])
        except (TypeError, ValueError):
            raise ValueError(
                "spend_guard.default_context_window_percent must be an integer "
                f"in [0, 100]; got {sg['default_context_window_percent']!r}"
            )
    if "hard_stop_context_window_percent" in sg:
        try:
            cfg.hard_stop_context_window_percent = int(sg["hard_stop_context_window_percent"])
        except (TypeError, ValueError):
            raise ValueError(
                "spend_guard.hard_stop_context_window_percent must be an "
                f"integer in [0, 100]; got {sg['hard_stop_context_window_percent']!r}"
            )
    if "dollar_cap_enabled_by_default" in sg:
        cfg.dollar_cap_enabled_by_default = _coerce_bool(sg["dollar_cap_enabled_by_default"])
    if "default_dollar_cap" in sg:
        legacy_dollar_set_via["default_dollar_cap"] = "config"
        v = sg["default_dollar_cap"]
        if v is None:
            cfg.default_dollar_cap = None
        else:
            try:
                cfg.default_dollar_cap = float(v)
            except (TypeError, ValueError):
                pass

    # Per-request token bands + operational knobs + rolling-cap integer fields
    for k in (
        "warn_tokens",
        "block_tokens",
        "hard_block_tokens",
        "pending_ttl_seconds",
        "session_window_seconds",
        "rolling_caps_window_seconds",
        "rolling_caps_per_agent_max_tokens_total",
        "rolling_caps_per_agent_max_cache_read_tokens",
        "rolling_caps_per_fleet_max_tokens_total",
        "rolling_caps_per_fleet_max_cache_read_tokens",
    ):
        if k in sg:
            try:
                setattr(cfg, k, int(sg[k]))
            except (TypeError, ValueError):
                pass

    # Rolling caps — bool + float fields
    if "rolling_caps_enabled" in sg:
        cfg.rolling_caps_enabled = _coerce_bool(sg["rolling_caps_enabled"])
    for k in (
        "rolling_caps_per_agent_max_cost_usd",
        "rolling_caps_per_fleet_max_cost_usd",
    ):
        if k in sg:
            try:
                setattr(cfg, k, float(sg[k]))
            except (TypeError, ValueError):
                pass

    # Rolling-cap nested style: also accept `rolling_caps:` subsection
    rc_block = sg.get("rolling_caps") or {}
    if isinstance(rc_block, dict):
        if "enabled" in rc_block:
            cfg.rolling_caps_enabled = _coerce_bool(rc_block["enabled"])
        if "window_seconds" in rc_block:
            try:
                cfg.rolling_caps_window_seconds = int(rc_block["window_seconds"])
            except (TypeError, ValueError):
                pass
        for sub, mapping in (
            (
                "per_agent",
                {
                    "max_cost_usd": ("rolling_caps_per_agent_max_cost_usd", float),
                    "max_tokens_total": ("rolling_caps_per_agent_max_tokens_total", int),
                    "max_cache_read_tokens": ("rolling_caps_per_agent_max_cache_read_tokens", int),
                },
            ),
            (
                "per_fleet",
                {
                    "max_cost_usd": ("rolling_caps_per_fleet_max_cost_usd", float),
                    "max_tokens_total": ("rolling_caps_per_fleet_max_tokens_total", int),
                    "max_cache_read_tokens": ("rolling_caps_per_fleet_max_cache_read_tokens", int),
                },
            ),
        ):
            sub_block = rc_block.get(sub) or {}
            if isinstance(sub_block, dict):
                for yaml_key, (attr, caster) in mapping.items():
                    if yaml_key in sub_block:
                        try:
                            setattr(cfg, attr, caster(sub_block[yaml_key]))
                        except (TypeError, ValueError):
                            pass

    # Legacy dollar bands + warn-cost band
    for k in (
        "warn_cost_usd",
        "block_cost_usd",
        "hard_block_cost_usd",
        "session_block_cost_usd",
        "audit_min_cost_usd",
    ):
        if k in sg:
            try:
                setattr(cfg, k, float(sg[k]))
                if k in _LEGACY_DOLLAR_FIELDS:
                    legacy_dollar_set_via[k] = "config"
            except (TypeError, ValueError):
                pass
    if "audit_db_path" in sg and sg["audit_db_path"]:
        cfg.audit_db_path = str(sg["audit_db_path"])

    # Env overrides (highest priority)
    env = os.environ
    if "TOKENPAK_SPEND_GUARD_ENABLED" in env:
        cfg.enabled = _coerce_bool(env["TOKENPAK_SPEND_GUARD_ENABLED"])
    if "TOKENPAK_SPEND_GUARD_DEFAULT_BASIS" in env:
        cfg.default_basis = env["TOKENPAK_SPEND_GUARD_DEFAULT_BASIS"].strip()
    if "TOKENPAK_SPEND_GUARD_CONTEXT_WINDOW_PERCENT" in env:
        try:
            cfg.default_context_window_percent = int(
                env["TOKENPAK_SPEND_GUARD_CONTEXT_WINDOW_PERCENT"]
            )
        except (TypeError, ValueError):
            raise ValueError(
                "TOKENPAK_SPEND_GUARD_CONTEXT_WINDOW_PERCENT must be an "
                f"integer in [0, 100]; got "
                f"{env['TOKENPAK_SPEND_GUARD_CONTEXT_WINDOW_PERCENT']!r}"
            )
    if "TOKENPAK_SPEND_GUARD_HARD_STOP_CONTEXT_WINDOW_PERCENT" in env:
        try:
            cfg.hard_stop_context_window_percent = int(
                env["TOKENPAK_SPEND_GUARD_HARD_STOP_CONTEXT_WINDOW_PERCENT"]
            )
        except (TypeError, ValueError):
            raise ValueError(
                "TOKENPAK_SPEND_GUARD_HARD_STOP_CONTEXT_WINDOW_PERCENT must be "
                f"an integer in [0, 100]; got "
                f"{env['TOKENPAK_SPEND_GUARD_HARD_STOP_CONTEXT_WINDOW_PERCENT']!r}"
            )
    if "TOKENPAK_SPEND_GUARD_DOLLAR_CAP_ENABLED" in env:
        cfg.dollar_cap_enabled_by_default = _coerce_bool(
            env["TOKENPAK_SPEND_GUARD_DOLLAR_CAP_ENABLED"]
        )
    env_overrides: tuple[tuple[str, str, Callable[[Any], Any]], ...] = (
        ("TOKENPAK_SPEND_GUARD_WARN_TOKENS", "warn_tokens", int),
        ("TOKENPAK_SPEND_GUARD_BLOCK_TOKENS", "block_tokens", int),
        ("TOKENPAK_SPEND_GUARD_HARD_BLOCK_TOKENS", "hard_block_tokens", int),
        ("TOKENPAK_SPEND_GUARD_WARN_COST_USD", "warn_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_BLOCK_COST_USD", "block_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_HARD_BLOCK_COST_USD", "hard_block_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_PENDING_TTL", "pending_ttl_seconds", int),
        ("TOKENPAK_SPEND_GUARD_SESSION_BLOCK_COST_USD", "session_block_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_SESSION_WINDOW_SECONDS", "session_window_seconds", int),
        # Rolling caps env overrides
        ("TOKENPAK_SPEND_GUARD_ROLLING_CAPS_ENABLED", "rolling_caps_enabled", _coerce_bool),
        ("TOKENPAK_SPEND_GUARD_ROLLING_WINDOW_SECONDS", "rolling_caps_window_seconds", int),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_AGENT_COST_USD",
            "rolling_caps_per_agent_max_cost_usd",
            float,
        ),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_AGENT_TOKENS",
            "rolling_caps_per_agent_max_tokens_total",
            int,
        ),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_AGENT_CACHE_READ",
            "rolling_caps_per_agent_max_cache_read_tokens",
            int,
        ),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_FLEET_COST_USD",
            "rolling_caps_per_fleet_max_cost_usd",
            float,
        ),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_FLEET_TOKENS",
            "rolling_caps_per_fleet_max_tokens_total",
            int,
        ),
        (
            "TOKENPAK_SPEND_GUARD_ROLLING_PER_FLEET_CACHE_READ",
            "rolling_caps_per_fleet_max_cache_read_tokens",
            int,
        ),
    )
    for env_key, attr, env_caster in env_overrides:
        if env_key in env:
            try:
                setattr(cfg, attr, env_caster(env[env_key]))
                if attr in _LEGACY_DOLLAR_FIELDS:
                    # Env override of a legacy dollar field also engages
                    # the dollar plane and emits the deprecation warning.
                    legacy_dollar_set_via[attr] = env_key
            except (TypeError, ValueError):
                pass

    # Validation: percent values
    for pct_field in ("default_context_window_percent", "hard_stop_context_window_percent"):
        v = getattr(cfg, pct_field)
        if not isinstance(v, int) or v < 0 or v > 100:
            raise ValueError(f"spend_guard.{pct_field} must be an integer in [0, 100]; got {v!r}")
    if cfg.default_context_window_percent > cfg.hard_stop_context_window_percent:
        raise ValueError(
            "spend_guard.default_context_window_percent "
            f"({cfg.default_context_window_percent}) must be <= "
            "spend_guard.hard_stop_context_window_percent "
            f"({cfg.hard_stop_context_window_percent})"
        )

    # Backward-compat: any explicit legacy-dollar-field setting engages the
    # dollar plane for that profile and emits a DeprecationWarning. The
    # values still work — the dollar plane remains reachable as
    # an opt-in profile override.
    if legacy_dollar_set_via:
        cfg.dollar_cap_enabled_by_default = True
        fields_str = ", ".join(
            f"{name} (via {src})" for name, src in sorted(legacy_dollar_set_via.items())
        )
        warnings.warn(
            "TIP Spend Guard: legacy dollar-denominated configuration is "
            "deprecated. The canonical default basis is context-window % "
            "(default 90% block / 100% hard stop). The "
            f"following dollar field(s) remain reachable as opt-in: {fields_str}. "
            "Migrate to default_context_window_percent / "
            "hard_stop_context_window_percent where possible.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Sanity: hard_block_tokens must be ≥ block_tokens (token-band fallback).
    cfg.hard_block_tokens = max(cfg.hard_block_tokens, cfg.block_tokens)
    # When BOTH dollar bands are explicitly engaged, hard_block_cost_usd
    # must be ≥ block_cost_usd. If only one is engaged, the other stays
    # at 0.0 (disabled) and the engaged one keeps its configured value —
    # we don't auto-promote a soft block into a hard block.
    if cfg.hard_block_cost_usd > 0 and cfg.block_cost_usd > 0:
        cfg.hard_block_cost_usd = max(cfg.hard_block_cost_usd, cfg.block_cost_usd)
    return cfg


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------


def decide(
    estimate: RiskEstimate,
    cfg: Optional[SpendGuardConfig] = None,
    tip: Optional[TIPDirective] = None,
    *,
    session_running_cost_usd: float = 0.0,
    model_max_context_tokens: Optional[int] = None,
) -> PreflightDecision:
    """Compare the estimate to thresholds and return a verdict.

    Order of checks (most specific first):

    1. **Context-window-% hard stop** (canonical) — projected
       input tokens ≥ ``hard_stop_context_window_percent`` × max-context.
       NOT bypassable. Fires regardless of any ``[TIP: ...]`` directive.
    2. Legacy hard-block bands (dollar + token-fallback) — when the dollar
       plane is engaged or the model's context window is unknown. Also
       immutable.
    3. TIP-declared ceiling, when present and inside the hard-stop band.
       ``[TIP: allow=once max=$X]`` lets the request through provided the
       projected request fits the declared dimensions.
    4. **Context-window-% soft block** (canonical) — projected
       input tokens ≥ ``default_context_window_percent`` × max-context.
       Bypassable by Yes/``[TIP: allow=once]``.
    5. Legacy block band — dollar (when engaged) + token-fallback (when
       context unknown). Bypassable by Yes/``[TIP: allow=once]``.
    6. Session-cumulative defense (legacy) — only when
       ``session_block_cost_usd > 0`` (opt-in under v1.5.2 defaults).
    7. ``warn`` — advisory only.
    8. ``allow`` — default.

    The canonical default basis is context-window utilisation %. Dollar
    bands stay reachable as an opt-in profile override.
    """
    if cfg is None:
        cfg = load_config()

    cost = estimate.projected_cost_usd
    tokens = estimate.projected_input_tokens + estimate.projected_output_tokens
    # Context-window-% denominator is the projected INPUT (the request
    # context the model receives), not input+output. Output tokens are
    # produced by the model and don't push past the context window.
    input_tokens = estimate.projected_input_tokens

    # Resolve the effective LEGACY block-tokens band (advisory fallback for
    # the case where the model's max context window is unknown).
    derived_block_tokens = derive_block_threshold(
        model_max_context_tokens,
        ratio=DEFAULT_BLOCK_RATIO,
        fallback_tokens=cfg.block_tokens,
    )
    if derived_block_tokens is None:
        effective_block_tokens = cfg.hard_block_tokens
        threshold_source = "block_tokens_unresolved"
    elif model_max_context_tokens is not None and model_max_context_tokens > 0:
        effective_block_tokens = min(derived_block_tokens, cfg.hard_block_tokens)
        threshold_source = "block_tokens_dynamic"
    else:
        effective_block_tokens = min(derived_block_tokens, cfg.hard_block_tokens)
        threshold_source = "block_tokens_fallback"

    # Resolve context-window-% thresholds in absolute tokens, when the
    # model's max context window is known. When unknown, both percent
    # checks degrade to the legacy token-band fallback below.
    pct_block_tokens: Optional[int] = None
    pct_hard_stop_tokens: Optional[int] = None
    if model_max_context_tokens is not None and model_max_context_tokens > 0:
        pct_block_tokens = int(
            model_max_context_tokens * cfg.default_context_window_percent / 100.0
        )
        pct_hard_stop_tokens = int(
            model_max_context_tokens * cfg.hard_stop_context_window_percent / 100.0
        )

    # 1. Context-window-% hard stop — canonical absolute ceiling.
    #    No TIP/Yes/bypass crosses it. Fires before any TIP processing.
    if pct_hard_stop_tokens is not None and input_tokens >= pct_hard_stop_tokens:
        return PreflightDecision(
            decision="hard_block",
            reason="projected_exceeds_context_window_hard_stop",
            requires_approval=False,
            threshold_hit=(
                f"hard_stop_context_window_percent>={cfg.hard_stop_context_window_percent}"
                f" max_context={model_max_context_tokens}"
                f" projected_input={input_tokens}"
            ),
            risk=estimate,
        )

    # 2. Legacy hard-block bands. Dollar plane only engages when the
    #    operator explicitly configured it (>= 0.0 is the disabled state).
    if cfg.hard_block_cost_usd > 0 and cost >= cfg.hard_block_cost_usd:
        return PreflightDecision(
            decision="hard_block",
            reason="projected_cost_exceeds_hard_block",
            requires_approval=False,
            threshold_hit=f"hard_block_cost_usd>={cfg.hard_block_cost_usd}",
            risk=estimate,
        )
    # Token-band hard-block fires only when the model context is unknown
    # (otherwise the context-window-% hard stop above is the ceiling). The
    # token-band ceiling is intentionally a FALLBACK — it protects the
    # single-runaway-prompt case the % basis can't see. For frontier
    # models with known contexts, the % basis is the canonical defense
    # and the token-band ceiling would otherwise pin the user to a
    # sub-context cap.
    if (
        pct_hard_stop_tokens is None
        and cfg.hard_block_tokens > 0
        and tokens >= cfg.hard_block_tokens
    ):
        return PreflightDecision(
            decision="hard_block",
            reason="projected_tokens_exceed_hard_block",
            requires_approval=False,
            threshold_hit=f"hard_block_tokens>={cfg.hard_block_tokens}",
            risk=estimate,
        )

    # 3. TIP-declared ceiling
    if tip is not None and (
        tip.bypass or tip.allow_scope or tip.max_cost_usd is not None or tip.max_tokens is not None
    ):
        # When a TIP ceiling is specified, only the *specified* dimensions
        # bind. Unspecified dimensions are treated as user-authorized — the
        # caller has explicitly opted in. Hard-block / hard-stop (already
        # checked above) are the only immutable ceilings.
        cost_ok = (tip.max_cost_usd is None) or (cost <= tip.max_cost_usd)
        tokens_ok = (tip.max_tokens is None) or (tokens <= tip.max_tokens)
        # bypass=on with NO declared ceiling: still gated by the legacy
        # block bands (when engaged) so a bare bypass=on can't swallow
        # $40 silently or push past the token-band fallback.
        if tip.bypass and tip.max_cost_usd is None and tip.max_tokens is None:
            cost_ok = (cfg.block_cost_usd <= 0) or (cost < cfg.block_cost_usd)
            tokens_ok = tokens < effective_block_tokens
        if cost_ok and tokens_ok:
            return PreflightDecision(
                decision="allow",
                reason="tip_bypass_within_ceiling",
                requires_approval=False,
                threshold_hit="tip_directive",
                risk=estimate,
            )
        return PreflightDecision(
            decision="block",
            reason="projected_exceeds_tip_ceiling",
            requires_approval=True,
            threshold_hit="tip_ceiling",
            risk=estimate,
        )

    # 4. Context-window-% soft block (canonical).
    if pct_block_tokens is not None and input_tokens >= pct_block_tokens:
        return PreflightDecision(
            decision="block",
            reason="projected_exceeds_context_window_percent",
            requires_approval=True,
            threshold_hit=(
                f"default_context_window_percent>={cfg.default_context_window_percent}"
                f" max_context={model_max_context_tokens}"
                f" projected_input={input_tokens}"
            ),
            risk=estimate,
        )

    # 5a. Session-cumulative dollar defense (legacy; opt-in by default).
    if cfg.session_block_cost_usd > 0:
        session_total_after = session_running_cost_usd + cost
        if session_total_after >= cfg.session_block_cost_usd:
            return PreflightDecision(
                decision="block",
                reason="session_cumulative_cost_exceeded",
                requires_approval=True,
                threshold_hit=(
                    f"session_block_cost_usd>={cfg.session_block_cost_usd}"
                    f" running={session_running_cost_usd:.2f}"
                ),
                risk=estimate,
            )

    # 5b. Legacy dollar block band (opt-in).
    if cfg.block_cost_usd > 0 and cost >= cfg.block_cost_usd:
        return PreflightDecision(
            decision="block",
            reason="projected_cost_exceeded",
            requires_approval=True,
            threshold_hit=f"block_cost_usd>={cfg.block_cost_usd}",
            risk=estimate,
        )

    # 5c. Legacy token-band block — advisory fallback. Engaged only when
    #     the context-window-% basis didn't have a known max-context to
    #     denominate against (otherwise pct_block_tokens above handles it).
    if pct_block_tokens is None and tokens >= effective_block_tokens:
        return PreflightDecision(
            decision="block",
            reason="projected_tokens_exceeded",
            requires_approval=True,
            threshold_hit=(
                f"{threshold_source}>={effective_block_tokens}"
                + (f" max_context={model_max_context_tokens}" if model_max_context_tokens else "")
            ),
            risk=estimate,
        )

    # 6. Warn band — advisory only.
    if (cfg.warn_cost_usd > 0 and cost >= cfg.warn_cost_usd) or tokens >= cfg.warn_tokens:
        return PreflightDecision(
            decision="warn",
            reason="projected_in_warn_band",
            requires_approval=False,
            threshold_hit="warn_band",
            risk=estimate,
        )

    # 7. Allow
    return PreflightDecision(
        decision="allow",
        reason="under_thresholds",
        requires_approval=False,
        threshold_hit=None,
        risk=estimate,
    )
