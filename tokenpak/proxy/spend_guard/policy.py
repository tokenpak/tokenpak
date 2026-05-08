# SPDX-License-Identifier: Apache-2.0
"""Threshold-based policy engine for spend guard.

Turns a :class:`RiskEstimate` into a :class:`PreflightDecision` by comparing
projected tokens and projected cost against four configurable bands:

    allow < warn < block < hard_block

- ``allow`` — quietly forward; not surfaced.
- ``warn`` — forward but emit a warn audit row (advisory; no client UX yet).
- ``block`` — caller can release with Yes/[TIP].
- ``hard_block`` — cannot be released; even ``[TIP: bypass=on]`` does not
  override (proposal §4 second example).

Defaults match Kevin's 2026-05-07 overrides on the published proposal:
    warn:        100,000 tokens / $2.00
    block:       500,000 tokens / $10.00     (proposal had 250,000 / $5)
    hard_block: 1,000,000 tokens / $50.00
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .contracts import PreflightDecision, RiskEstimate, TIPDirective


@dataclass
class SpendGuardConfig:
    """Resolved threshold + behavior config.

    Loaded from ``~/.tokenpak/config.yaml`` ``spend_guard:`` block by
    :func:`load_config`. Env-var overrides take precedence.
    """

    enabled: bool = True
    warn_tokens: int = 100_000
    warn_cost_usd: float = 2.0
    block_tokens: int = 500_000          # Kevin override, was 250_000
    block_cost_usd: float = 10.0         # Kevin override, was 5.0
    hard_block_tokens: int = 1_000_000
    hard_block_cost_usd: float = 50.0
    pending_ttl_seconds: int = 600
    audit_db_path: str = "~/.tokenpak/spend_guard.db"
    # Below this projected-cost floor we don't even audit (avoid noise).
    audit_min_cost_usd: float = 0.10


# ---------------------------------------------------------------------------
# Config loader — single source of truth for thresholds
# ---------------------------------------------------------------------------

def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load_config(raw_config: Optional[dict] = None) -> SpendGuardConfig:
    """Resolve the live SpendGuardConfig.

    ``raw_config`` is the parsed ``~/.tokenpak/config.yaml`` (or any dict-like
    with a ``spend_guard`` section). When omitted, config is read fresh from
    ``tokenpak.proxy.config``. Env vars override file config.
    """
    import os

    cfg = SpendGuardConfig()

    # File defaults
    if raw_config is not None:
        sg = (raw_config or {}).get("spend_guard") or {}
    else:
        try:
            from tokenpak.core.config_loader import load_config as _load_yaml
            sg = (_load_yaml() or {}).get("spend_guard") or {}
        except Exception:
            sg = {}

    if "enabled" in sg:
        cfg.enabled = _coerce_bool(sg["enabled"])
    for k in (
        "warn_tokens",
        "block_tokens",
        "hard_block_tokens",
        "pending_ttl_seconds",
    ):
        if k in sg:
            try:
                setattr(cfg, k, int(sg[k]))
            except (TypeError, ValueError):
                pass
    for k in (
        "warn_cost_usd",
        "block_cost_usd",
        "hard_block_cost_usd",
        "audit_min_cost_usd",
    ):
        if k in sg:
            try:
                setattr(cfg, k, float(sg[k]))
            except (TypeError, ValueError):
                pass
    if "audit_db_path" in sg and sg["audit_db_path"]:
        cfg.audit_db_path = str(sg["audit_db_path"])

    # Env overrides (highest priority)
    env = os.environ
    if "TOKENPAK_SPEND_GUARD_ENABLED" in env:
        cfg.enabled = _coerce_bool(env["TOKENPAK_SPEND_GUARD_ENABLED"])
    for env_key, attr, caster in (
        ("TOKENPAK_SPEND_GUARD_WARN_TOKENS", "warn_tokens", int),
        ("TOKENPAK_SPEND_GUARD_BLOCK_TOKENS", "block_tokens", int),
        ("TOKENPAK_SPEND_GUARD_HARD_BLOCK_TOKENS", "hard_block_tokens", int),
        ("TOKENPAK_SPEND_GUARD_WARN_COST_USD", "warn_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_BLOCK_COST_USD", "block_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_HARD_BLOCK_COST_USD", "hard_block_cost_usd", float),
        ("TOKENPAK_SPEND_GUARD_PENDING_TTL", "pending_ttl_seconds", int),
    ):
        if env_key in env:
            try:
                setattr(cfg, attr, caster(env[env_key]))
            except (TypeError, ValueError):
                pass

    # Sanity: hard_block must be the strictest band.
    cfg.hard_block_tokens = max(cfg.hard_block_tokens, cfg.block_tokens)
    cfg.hard_block_cost_usd = max(cfg.hard_block_cost_usd, cfg.block_cost_usd)
    return cfg


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------

def decide(
    estimate: RiskEstimate,
    cfg: Optional[SpendGuardConfig] = None,
    tip: Optional[TIPDirective] = None,
) -> PreflightDecision:
    """Compare the estimate to thresholds and return a verdict.

    Order of checks (most specific first):

    1. ``hard_block`` — if EITHER cost OR tokens cross the hard-block band.
       Cannot be bypassed. Fires regardless of TIP directive.
    2. TIP-declared ceiling, if present and within hard-block band.
       ``[TIP: allow=once max=$X]`` lets the request through provided
       ``estimate.projected_cost_usd <= X`` and ``X < hard_block_cost_usd``.
    3. ``block`` — if EITHER cost OR tokens cross the block band.
    4. ``warn`` — advisory only.
    5. ``allow`` — default.
    """
    if cfg is None:
        cfg = load_config()

    cost = estimate.projected_cost_usd
    tokens = estimate.projected_input_tokens + estimate.projected_output_tokens

    # 1. Hard-block (immutable ceiling)
    if cost >= cfg.hard_block_cost_usd:
        return PreflightDecision(
            decision="hard_block",
            reason="projected_cost_exceeds_hard_block",
            requires_approval=False,
            threshold_hit=f"hard_block_cost_usd>={cfg.hard_block_cost_usd}",
            risk=estimate,
        )
    if tokens >= cfg.hard_block_tokens:
        return PreflightDecision(
            decision="hard_block",
            reason="projected_tokens_exceed_hard_block",
            requires_approval=False,
            threshold_hit=f"hard_block_tokens>={cfg.hard_block_tokens}",
            risk=estimate,
        )

    # 2. TIP-declared ceiling
    if tip is not None and (tip.bypass or tip.allow_scope or tip.max_cost_usd is not None or tip.max_tokens is not None):
        # When a TIP ceiling is specified, only the *specified* dimensions
        # bind. Unspecified dimensions are treated as user-authorized — the
        # caller has explicitly opted in. Hard-block (already checked above)
        # is the only immutable ceiling.
        cost_ok = (tip.max_cost_usd is None) or (cost <= tip.max_cost_usd)
        tokens_ok = (tip.max_tokens is None) or (tokens <= tip.max_tokens)
        # bypass=on with NO declared ceiling: still gated by config's block
        # band so a bare bypass=on can't swallow $40 silently.
        if tip.bypass and tip.max_cost_usd is None and tip.max_tokens is None:
            cost_ok = cost < cfg.block_cost_usd
            tokens_ok = tokens < cfg.block_tokens
        if cost_ok and tokens_ok:
            return PreflightDecision(
                decision="allow",
                reason="tip_bypass_within_ceiling",
                requires_approval=False,
                threshold_hit="tip_directive",
                risk=estimate,
            )
        # Declared ceiling too low for this request — keep blocking, but tag
        # the reason so the caller knows their TIP wasn't enough.
        return PreflightDecision(
            decision="block",
            reason="projected_exceeds_tip_ceiling",
            requires_approval=True,
            threshold_hit="tip_ceiling",
            risk=estimate,
        )

    # 3. Block band
    if cost >= cfg.block_cost_usd:
        return PreflightDecision(
            decision="block",
            reason="projected_cost_exceeded",
            requires_approval=True,
            threshold_hit=f"block_cost_usd>={cfg.block_cost_usd}",
            risk=estimate,
        )
    if tokens >= cfg.block_tokens:
        return PreflightDecision(
            decision="block",
            reason="projected_tokens_exceeded",
            requires_approval=True,
            threshold_hit=f"block_tokens>={cfg.block_tokens}",
            risk=estimate,
        )

    # 4. Warn band — advisory only
    if cost >= cfg.warn_cost_usd or tokens >= cfg.warn_tokens:
        return PreflightDecision(
            decision="warn",
            reason="projected_in_warn_band",
            requires_approval=False,
            threshold_hit="warn_band",
            risk=estimate,
        )

    # 5. Allow
    return PreflightDecision(
        decision="allow",
        reason="under_thresholds",
        requires_approval=False,
        threshold_hit=None,
        risk=estimate,
    )
