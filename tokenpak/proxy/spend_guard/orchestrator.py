# SPDX-License-Identifier: Apache-2.0
"""Glue layer that turns guard primitives into a single ``GuardOutcome``.

The proxy hook (``proxy/server.py``) calls :func:`evaluate` with the raw
inbound bytes and gets back a tagged outcome. All multi-step interaction
(estimate → policy → pending → intent → replay → audit) lives here so the
proxy hot path stays small.

This module is built up in stages. The initial revision wires
estimate + policy + pending only. Subsequent revisions add intent parsing,
TIP-header handling, and audit logging.
"""

from __future__ import annotations

__all__ = (
    "GuardOutcome",
    "PendingRequest",
    "PendingStore",
    "SpendGuardConfig",
    "build_block",
    "build_block_store_unavailable",
    "build_cancelled",
    "build_estimate",
    "build_hard_block",
    "build_pending_waiting",
    "build_reprompt",
    "decide",
    "evaluate",
    "hash_request",
    "load_config",
    "run_estimate",
)


import logging
import threading
import time
from typing import Any, Optional

from .block_response import (
    block as build_block,
)
from .block_response import (
    block_store_unavailable as build_block_store_unavailable,
)
from .block_response import (
    cancelled as build_cancelled,
)
from .block_response import (
    estimate_only as build_estimate,
)
from .block_response import (
    hard_block as build_hard_block,
)
from .block_response import (
    pending_waiting as build_pending_waiting,
)
from .block_response import (
    reprompt as build_reprompt,
)
from .contracts import GuardOutcome, OutcomeKind, PendingRequest, PreflightDecision
from .estimator import estimate as run_estimate
from .pending import PendingStore, hash_request
from .policy import SpendGuardConfig, decide, load_config

_log = logging.getLogger(__name__)

# Pending-row reconciliation: crashed/abandoned sessions leave rows in
# status='pending' forever unless someone sweeps them. The sweep piggybacks
# on evaluate() — it runs on the first evaluation in a process ("init") and
# at most once per interval thereafter, so the hot path pays one timestamp
# comparison in the common case.
_EXPIRE_SWEEP_INTERVAL_SEC = 300.0
_EXPIRE_SWEEP_LOCK = threading.Lock()
_LAST_EXPIRE_SWEEP = 0.0


def _maybe_expire_pending(store: "PendingStore") -> None:
    """Best-effort TTL sweep of stale pending rows, rate-limited per process."""
    global _LAST_EXPIRE_SWEEP
    now = time.time()
    if now - _LAST_EXPIRE_SWEEP < _EXPIRE_SWEEP_INTERVAL_SEC:
        return
    with _EXPIRE_SWEEP_LOCK:
        if now - _LAST_EXPIRE_SWEEP < _EXPIRE_SWEEP_INTERVAL_SEC:
            return
        _LAST_EXPIRE_SWEEP = now
    try:
        expired = store.expire_old()
        if expired:
            _log.info("spend_guard: expired %d stale pending request(s)", expired)
    except Exception as e:
        _log.debug("spend_guard: pending expiry sweep failed: %s", e)


# Provider hostnames keyed off the target_url for audit/store metadata.
def _provider_from_url(url: str) -> str:
    if not url:
        return ""
    if "anthropic" in url:
        return "anthropic"
    if "openai" in url or "codex" in url:
        return "openai"
    if "googleapis" in url or "vertex" in url:
        return "google"
    return "unknown"


def evaluate(
    body: bytes,
    model: str,
    session_id: str,
    headers: dict[str, str],
    *,
    config: Optional[SpendGuardConfig] = None,
    target_url: str = "",
) -> GuardOutcome:
    """Run the full pre-send pipeline.

    Returns a :class:`GuardOutcome` the proxy hook will interpret.
    Failure posture: unmeasurable rolling usage and pending-store write
    failures fail CLOSED (block); anything that still raises out of here
    is converted to a fail-closed 402 by the public ``evaluate`` wrapper
    in ``__init__.py``. Only the estimator keeps an explicit passthrough
    (a request we cannot price is forwarded rather than broken).
    """
    cfg = config or load_config()

    # Disabled → forward unchanged. This is the soft-launch path.
    if not cfg.enabled:
        return GuardOutcome.passthrough(body)

    # TIP header parse + strip. Imported lazily so the earlier stages can
    # run before this lands. Until tip_header.py exists, treat as no-op.
    tip_directive = None
    forward_body = body
    try:
        from .tip_header import parse_and_strip_tip_header

        tip_directive, forward_body = parse_and_strip_tip_header(body)
    except ImportError:
        pass
    except Exception as e:
        _log.debug("spend_guard: TIP header parse failed: %s", e)

    # TIP cancel — discard any pending and acknowledge.
    if tip_directive is not None and tip_directive.cancel:
        store = PendingStore(cfg.audit_db_path)
        existing = store.get_by_session(session_id)
        if existing:
            store.discard(existing.pending_id)
            _audit(
                cfg,
                "cancel",
                session_id,
                decision_str="cancel",
                pending_id=existing.pending_id,
                tip=tip_directive,
            )
            return GuardOutcome(
                kind="cancel",
                response_body=build_cancelled(existing),
                http_status=200,
                pending_id=existing.pending_id,
                audit_event="cancel",
            )
        # Nothing to cancel — treat as no-op forward (with TIP stripped).
        return GuardOutcome(kind="forward_modified", body=forward_body)

    # Pending check + intent parse. Lazy import for the same
    # reason — the earlier stage can land before the intent parser exists.
    store = PendingStore(cfg.audit_db_path)
    # Reconcile stale pending rows (first call in the process + periodic).
    _maybe_expire_pending(store)
    existing_pending = store.get_by_session(session_id)
    if existing_pending is not None:
        try:
            from .intent import parse_intent
            from .replay import resolve_pending

            intent = parse_intent(forward_body)
            outcome = resolve_pending(
                store=store,
                pending=existing_pending,
                intent=intent,
                tip=tip_directive,
                cfg=cfg,
                builders={
                    "cancelled": build_cancelled,
                    "reprompt": build_reprompt,
                    "pending_waiting": build_pending_waiting,
                },
            )
            _audit(
                cfg,
                outcome.audit_event or "pending",
                session_id,
                decision_str=outcome.kind,
                pending_id=existing_pending.pending_id,
                tip=tip_directive,
            )
            return outcome
        except ImportError:
            # Fallback before the intent parser lands: subsequent requests
            # during a pending block are themselves blocked with a
            # "waiting approval" message.
            _audit(
                cfg,
                "pending_waiting",
                session_id,
                decision_str="block",
                pending_id=existing_pending.pending_id,
                tip=tip_directive,
            )
            return GuardOutcome(
                kind="block",
                response_body=build_pending_waiting(existing_pending),
                http_status=402,
                pending_id=existing_pending.pending_id,
                audit_event="pending_waiting",
            )

    # ── Anti-loop: if the same request_hash was blocked very recently,
    #    return the cached block without re-running the estimator.
    h = hash_request(forward_body, model)
    recent = store.recent_block_by_hash(h, within_seconds=30.0)
    if recent is not None and recent.status in ("pending", "expired", "discarded"):
        _audit(
            cfg,
            "anti_loop_hit",
            session_id,
            decision_str="block",
            pending_id=recent.pending_id,
            tip=tip_directive,
        )
        return GuardOutcome(
            kind="block",
            response_body=build_block(_synthetic_decision(recent), recent),
            http_status=402,
            pending_id=recent.pending_id,
            audit_event="anti_loop_hit",
        )

    # ── Estimate + decide
    try:
        est = run_estimate(forward_body, model)
    except Exception as e:
        _log.warning("spend_guard: estimator failure (passthrough): %s", e)
        return GuardOutcome.passthrough(body)

    # ── Rolling/cumulative caps (2026-05-15 post-incident P0).
    # Records the session→agent mapping for future per-agent lookups,
    # then evaluates per-agent and per-fleet rolling caps. If any cap
    # would be exceeded by this request's projected cost, return a
    # block (respect TIP bypass directives). Per-session caps continue
    # to evaluate downstream — rolling caps SUPPLEMENT them.
    #
    # Admission accounting: when the cap check passes, the request's
    # projected spend is registered as in-flight (process-local counter)
    # so concurrent requests can't all pass against the same frozen
    # usage snapshot. The ticket rides on the forward outcome and is
    # settled by the proxy once the actual cost is recorded; any
    # non-forward exit below cancels it immediately.
    admission_ticket: Optional[str] = None

    def _cancel_admission() -> None:
        nonlocal admission_ticket
        if admission_ticket:
            try:
                from .rolling_caps import settle_pending_spend

                settle_pending_spend(admission_ticket)
            except Exception:
                pass
            admission_ticket = None

    try:
        from .block_response import build_rolling_cap_block
        from .rolling_caps import (
            RollingCapsConfig,
            admit_pending_spend,
            check_rolling_caps_and_admit,
            record_session_agent,
        )

        # Agent attribution — case-insensitive header lookup.
        agent_id = ""
        for hk, hv in (headers or {}).items():
            if str(hk).lower() == "x-tokenpak-agent":
                agent_id = str(hv).strip().lower()
                break
        if agent_id and session_id:
            record_session_agent(session_id, agent_id)
        if cfg.rolling_caps_enabled and agent_id:
            rc_cfg = RollingCapsConfig(
                enabled=cfg.rolling_caps_enabled,
                window_seconds=cfg.rolling_caps_window_seconds,
                per_agent_max_cost_usd=cfg.rolling_caps_per_agent_max_cost_usd,
                per_agent_max_tokens_total=cfg.rolling_caps_per_agent_max_tokens_total,
                per_agent_max_cache_read_tokens=cfg.rolling_caps_per_agent_max_cache_read_tokens,
                per_fleet_max_cost_usd=cfg.rolling_caps_per_fleet_max_cost_usd,
                per_fleet_max_tokens_total=cfg.rolling_caps_per_fleet_max_tokens_total,
                per_fleet_max_cache_read_tokens=cfg.rolling_caps_per_fleet_max_cache_read_tokens,
            )
            # Estimator doesn't directly project cache_read; use ratio
            # from est.cache_hit_ratio applied to projected_input_tokens
            # as a conservative estimate.
            projected_cache_read = int(
                est.projected_input_tokens * float(getattr(est, "cache_hit_ratio", 0.0) or 0.0)
            )
            breach, admission_ticket = check_rolling_caps_and_admit(
                agent_id=agent_id,
                projected_cost_usd=float(est.projected_cost_usd),
                projected_input_tokens=int(est.projected_input_tokens),
                projected_output_tokens=int(est.projected_output_tokens),
                projected_cache_read_tokens=projected_cache_read,
                config=rc_cfg,
            )
            if breach is not None:
                # TIP bypass respects existing semantics: [TIP: bypass=on]
                # or [TIP: allow=once] both let this request through.
                tip_allowed = tip_directive is not None and (
                    tip_directive.bypass or tip_directive.allow_scope is not None
                )
                if not tip_allowed:
                    _audit(
                        cfg,
                        "rolling_cap_block",
                        session_id,
                        decision_str="rolling_cap_block",
                        projected_cost=est.projected_cost_usd,
                        tip=tip_directive,
                    )
                    return GuardOutcome(
                        kind="block",
                        response_body=build_rolling_cap_block(breach),
                        http_status=402,
                        audit_event="rolling_cap_block",
                    )
                else:
                    _audit(
                        cfg,
                        "rolling_cap_tip_bypass",
                        session_id,
                        decision_str="allow",
                        projected_cost=est.projected_cost_usd,
                        tip=tip_directive,
                    )
                    # TIP-authorized forward still spends — track it so
                    # concurrent checks see the in-flight cost.
                    admission_ticket = admit_pending_spend(
                        agent_id,
                        float(est.projected_cost_usd),
                        int(est.projected_input_tokens) + int(est.projected_output_tokens),
                        projected_cache_read,
                    )
    except ImportError:
        # rolling_caps module not yet installed — skip silently
        pass
    except Exception as e:
        # The rolling-caps engine itself fails closed on unmeasurable
        # usage, so reaching here means a genuine bug in the cap wiring.
        # Surface it loudly; the request proceeds to the per-session
        # policy checks below (which still gate it).
        _log.warning(
            "spend_guard: rolling-cap check failed unexpectedly "
            "(%s: %s) — continuing with per-session checks only",
            type(e).__name__,
            e,
        )

    # Session-cumulative running cost — read from monitor.db.
    session_running = 0.0
    if cfg.session_block_cost_usd > 0:
        try:
            from .session_state import session_cumulative_cost

            session_running = session_cumulative_cost(
                session_id, window_seconds=cfg.session_window_seconds
            )
        except Exception as e:
            _log.debug("spend_guard: session_state lookup failed: %s", e)
            session_running = 0.0

    # Resolve max context for THIS model so decide() can derive the
    # block-tokens band as 80% of context. None → fallback path inside
    # decide() (uses cfg.block_tokens). Lookup is best-effort; any error
    # falls through to fallback.
    model_max_context_tokens: Optional[int] = None
    try:
        from ._context_window import get_model_max_context

        model_max_context_tokens = get_model_max_context(model)
    except Exception as e:
        _log.debug("spend_guard: context_window lookup failed: %s", e)

    decision = decide(
        est,
        cfg,
        tip=tip_directive,
        session_running_cost_usd=session_running,
        model_max_context_tokens=model_max_context_tokens,
    )

    # ── [TIP: estimate=on] short-circuit (only when allowed by policy)
    if (
        tip_directive is not None
        and tip_directive.estimate_only
        and decision.decision != "hard_block"
    ):
        _cancel_admission()  # nothing forwards, nothing spends
        _audit(
            cfg,
            "estimate",
            session_id,
            decision_str="estimate",
            tip=tip_directive,
            projected_cost=est.projected_cost_usd,
        )
        return GuardOutcome(
            kind="estimate",
            response_body=build_estimate(est),
            http_status=200,
            decision=decision,
            audit_event="estimate",
        )

    # ── Allow / warn → forward
    if decision.decision in ("allow", "warn"):
        if decision.decision == "warn":
            _audit(
                cfg,
                "warn",
                session_id,
                decision_str="warn",
                projected_cost=est.projected_cost_usd,
                tip=tip_directive,
            )
        # Even with TIP-bypass we audit (allow path with tip_directive set)
        if tip_directive is not None:
            _audit(
                cfg,
                "tip_bypass",
                session_id,
                decision_str="allow",
                projected_cost=est.projected_cost_usd,
                tip=tip_directive,
            )
        kind: OutcomeKind = "forward_modified" if forward_body is not body else "forward"
        return GuardOutcome(
            kind=kind, body=forward_body, decision=decision, admission_ticket=admission_ticket
        )

    # ── Hard-block → return immediately, no pending stored
    if decision.decision == "hard_block":
        _cancel_admission()
        _audit(
            cfg,
            "hard_block",
            session_id,
            decision_str="hard_block",
            projected_cost=est.projected_cost_usd,
            tip=tip_directive,
        )
        return GuardOutcome(
            kind="hard_block",
            response_body=build_hard_block(decision),
            http_status=402,
            decision=decision,
            audit_event="hard_block",
        )

    # ── Block → store pending, return block JSON
    _cancel_admission()  # blocked requests never reach the provider
    try:
        pending = store.store(
            session_id=session_id,
            body=body,  # store original (pre-TIP-strip) bytes
            headers=headers,
            target_url=target_url,
            provider=_provider_from_url(target_url),
            model=model,
            projected_tokens=est.projected_input_tokens + est.projected_output_tokens,
            projected_cost_usd=est.projected_cost_usd,
            ttl_seconds=cfg.pending_ttl_seconds,
        )
    except Exception as e:
        # The policy already decided BLOCK. A pending-store failure must
        # never downgrade that into a forward (which is what raising into
        # a permissive outer handler would do) — synthesize the block
        # without a pending row instead. Reply-to-approve is unavailable
        # for this request; the response says so.
        _log.warning(
            "spend_guard: pending-store write failed (%s: %s) — "
            "returning fail-closed block without a pending row",
            type(e).__name__,
            e,
        )
        _audit(
            cfg,
            "block",
            session_id,
            decision_str="block_store_unavailable",
            projected_cost=est.projected_cost_usd,
            tip=tip_directive,
        )
        return GuardOutcome(
            kind="block",
            response_body=build_block_store_unavailable(decision),
            http_status=402,
            decision=decision,
            audit_event="block",
        )
    _audit(
        cfg,
        "block",
        session_id,
        decision_str="block",
        pending_id=pending.pending_id,
        projected_cost=est.projected_cost_usd,
        tip=tip_directive,
    )
    return GuardOutcome(
        kind="block",
        response_body=build_block(decision, pending),
        http_status=402,
        decision=decision,
        pending_id=pending.pending_id,
        audit_event="block",
    )


def _synthetic_decision(recent: PendingRequest) -> PreflightDecision:
    """Reconstruct a minimal PreflightDecision for anti-loop block responses."""
    from .contracts import PreflightDecision, RiskEstimate

    risk = RiskEstimate(
        model=recent.model,
        current_context_tokens=0,
        request_tokens=0,
        projected_input_tokens=recent.projected_tokens,
        projected_output_tokens=0,
        projected_cost_usd=recent.projected_cost_usd,
        cache_hit_ratio=0.0,
        rates={},
    )
    return PreflightDecision(
        decision="block",
        reason="anti_loop_cache",
        requires_approval=True,
        threshold_hit="anti_loop",
        risk=risk,
    )


def _audit(cfg: SpendGuardConfig, event_type: str, session_id: str, **fields: Any) -> None:
    """Best-effort audit-log write. Never raises into the hot path."""
    try:
        from .audit import write_audit

        write_audit(cfg.audit_db_path, event_type=event_type, session_id=session_id, **fields)
    except ImportError:
        # audit module not yet landed
        pass
    except Exception as e:
        _log.debug("spend_guard: audit write failed: %s", e)
