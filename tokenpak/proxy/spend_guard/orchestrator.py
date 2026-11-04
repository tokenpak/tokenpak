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


import json
import logging
import threading
import time
from typing import Any, Optional

from .block_response import (
    block as build_block,
)
from .block_response import (
    block_session_identity_unavailable as build_block_session_identity_unavailable,
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
from .contracts import GuardOutcome, OutcomeKind, PendingRequest, PreflightDecision, TIPDirective
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


def _estimate_anchored(body: bytes, model: str, session_id: str):
    from .usage_anchor import projection_floor

    try:
        floor = projection_floor(body, model, session_id)
    except (ValueError, TypeError, AttributeError):
        floor = 0  # Unsupported history still receives structural estimation.
    return (
        run_estimate(body, model, fallback_session_tokens=floor)
        if floor
        else run_estimate(body, model)
    )


def _held_response(pending: PendingRequest) -> GuardOutcome:
    return GuardOutcome(
        kind="block",
        response_body=pending.block_response or build_block(_synthetic_decision(pending), pending),
        http_status=402,
        pending_id=pending.pending_id,
        audit_event="pending_waiting",
    )


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
    in ``__init__.py``. Estimator failures also block before provider send.
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
        from .approvals import ContextApprovals, scope_key

        ContextApprovals(store.path).revoke(scope_key(session_id, model, headers))
        existing = store.latest_hold(session_id, model=model, headers=headers)
        store.discard_expired(session_id, keep_id=existing.pending_id if existing else "")
        if existing:
            store.discard(existing.pending_id)
            _audit(
                cfg,
                "cancel",
                session_id,
                decision_str="cancel",
                reason="tip_directive_cancel",
                pending_id=existing.pending_id,
                projected_tokens=existing.projected_tokens,
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
    existing_pending = store.latest_hold(session_id, model=model, headers=headers)
    store.discard_expired(
        session_id, keep_id=existing_pending.pending_id if existing_pending else ""
    )
    from .approvals import ContextApprovals, scope_key
    from .intent import Intent, approval_fingerprint, has_human_text

    approvals = ContextApprovals(store.path)
    approval_key = scope_key(session_id, model, headers)
    late_pending = None
    scope_replay = None
    late_reply_hash = ""
    earlier_hold_expired = False
    if existing_pending is not None:
        try:
            from .intent import parse_intent
            from .replay import resolve_pending

            intent = parse_intent(forward_body)
            if not has_human_text(forward_body) and tip_directive is None:
                return _held_response(existing_pending)
            reply_hash = approval_fingerprint(forward_body)
            approving = intent == Intent.POSITIVE or (
                tip_directive is not None
                and (tip_directive.allow_scope or tip_directive.bypass)
                and intent != Intent.NEGATIVE
            )
            expired = existing_pending.expires_at <= time.time()
            if approving and approvals.reply_used(approval_key, reply_hash):
                if not expired:
                    return _held_response(existing_pending)
                # A used reply cannot authorize another send, but it must
                # not pin an expired hold forever either.
                approving = False
                tip_directive = None
            if expired and approving:
                late_pending = existing_pending
                late_reply_hash = reply_hash
            elif expired:
                store.discard(existing_pending.pending_id)
                if intent == Intent.NEGATIVE:
                    approvals.revoke(approval_key)
                    return GuardOutcome(
                        kind="cancel",
                        response_body=build_cancelled(existing_pending),
                        pending_id=existing_pending.pending_id,
                        audit_event="cancel",
                    )
                # A new human request supersedes an expired hold and is
                # evaluated normally; it does not inherit any approval.
            if not expired and approving:
                # All replay approvals use the same estimation and cap path
                # as fresh requests before consuming the held bytes.
                scope_replay = existing_pending
                forward_body = existing_pending.raw_request_blob
                outcome = None
            elif expired:
                outcome = None  # Expired bytes are never replayed.
            else:
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
            if outcome is not None:
                if outcome.kind == "cancel":
                    approvals.revoke(approval_key)
                _audit(
                    cfg,
                    outcome.audit_event or "pending",
                    session_id,
                    decision_str=outcome.kind,
                    reason=f"pending_intent_resolved:{intent.value}",
                    pending_id=existing_pending.pending_id,
                    projected_tokens=existing_pending.projected_tokens,
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
                reason="pending_waiting:intent_parser_module_unavailable",
                pending_id=existing_pending.pending_id,
                projected_tokens=existing_pending.projected_tokens,
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
    recent = store.recent_block_by_hash(
        h,
        within_seconds=30.0,
        session_id=session_id,
    )
    if (
        recent is not None
        and recent.status in ("pending", "expired", "discarded")
        and recent.expires_at > time.time()
        and scope_key(session_id, recent.model, recent.raw_request_headers) == approval_key
        and tip_directive is None
        and late_pending is None
        and scope_replay is None
    ):
        _audit(
            cfg,
            "anti_loop_hit",
            session_id,
            decision_str="block",
            reason="anti_loop_cache_hit:recent_block_same_request_hash",
            pending_id=recent.pending_id,
            projected_tokens=recent.projected_tokens,
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
        est = _estimate_anchored(forward_body, model, session_id)
    except Exception as e:
        from . import _fail_closed_outcome

        _log.warning("spend_guard: estimator failure (blocked): %s", type(e).__name__)
        return _fail_closed_outcome(e)

    if late_pending is not None:
        from ._context_window import get_model_max_context

        window = get_model_max_context(model)
        expired_input = late_pending.projected_input_tokens
        if expired_input is None:
            expired_input = late_pending.projected_tokens
        if window and est.projected_input_tokens <= expired_input + window * 0.1:
            tip_directive = tip_directive or TIPDirective(allow_scope="once")
        else:
            earlier_hold_expired = True
            approvals.remember_reply(approval_key, late_reply_hash)
            tip_directive = None

    # ── Rolling/cumulative caps (2026-05-15 post-incident P0).
    # Records the session→agent mapping for future per-agent lookups,
    # then evaluates per-agent and per-fleet rolling caps. If any cap
    # would be exceeded by this request's projected cost, return a
    # block regardless of approval. Per-session caps continue
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
                _audit(
                    cfg,
                    "rolling_cap_block",
                    session_id,
                    decision_str="rolling_cap_block",
                    reason=f"rolling_cap_breach:{breach.cap_dimension}",
                    projected_cost=est.projected_cost_usd,
                    projected_tokens=int(est.projected_input_tokens)
                    + int(est.projected_output_tokens),
                    tip=tip_directive,
                )
                return GuardOutcome(
                    kind="block",
                    response_body=build_rolling_cap_block(breach),
                    http_status=402,
                    audit_event="rolling_cap_block",
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
        context_soft_approved=scope_replay is not None,
    )
    if earlier_hold_expired and decision.decision != "hard_block":
        decision = PreflightDecision(
            decision="block", reason="earlier_hold_expired", requires_approval=True, risk=est
        )
    elif decision.reason == "projected_exceeds_context_window_percent" and session_id:
        without_context = decide(
            est,
            cfg,
            session_running_cost_usd=session_running,
            model_max_context_tokens=model_max_context_tokens,
            context_soft_approved=True,
        )
        if without_context.decision in ("allow", "warn") and approvals.redeem(approval_key, est):
            decision = without_context
            _audit(
                cfg,
                "context_grant_bypass",
                session_id,
                decision_str="allow",
                projected_tokens=est.projected_input_tokens,
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
            reason="tip_estimate_only",
            tip=tip_directive,
            projected_cost=est.projected_cost_usd,
            projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
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
        if late_pending is not None and not approvals.consume_expired(
            approval_key, late_pending.pending_id, late_reply_hash
        ):
            _cancel_admission()
            return _held_response(late_pending)
        if scope_replay is not None:
            consumed = store.consume(scope_replay.pending_id)
            if consumed is None:
                _cancel_admission()
                return _held_response(scope_replay)
        if tip_directive is not None and session_id:
            approvals.create(approval_key, tip_directive, est)
        if scope_replay is not None:
            _audit(
                cfg,
                "replay",
                session_id,
                decision_str="replay",
                pending_id=scope_replay.pending_id,
                tip=tip_directive,
            )
            return GuardOutcome(
                kind="replay",
                body=consumed.raw_request_blob,
                headers=consumed.raw_request_headers,
                target_url=consumed.target_url,
                pending_id=consumed.pending_id,
                admission_ticket=admission_ticket,
                audit_event="replay",
            )
        if decision.decision == "warn":
            _audit(
                cfg,
                "warn",
                session_id,
                decision_str="warn",
                reason=decision.reason,
                projected_cost=est.projected_cost_usd,
                projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
                tip=tip_directive,
            )
        # Even with TIP-bypass we audit (allow path with tip_directive set)
        if tip_directive is not None:
            _audit(
                cfg,
                "tip_bypass",
                session_id,
                decision_str="allow",
                reason=f"tip_bypass:{decision.reason}",
                projected_cost=est.projected_cost_usd,
                projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
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
            reason=decision.reason,
            projected_cost=est.projected_cost_usd,
            projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
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
    if not session_id:
        _audit(
            cfg,
            "block",
            session_id,
            decision_str="block_session_identity_unavailable",
            reason=f"{decision.reason}:session_id_unavailable",
            projected_cost=est.projected_cost_usd,
            projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
            tip=tip_directive,
        )
        return GuardOutcome(
            kind="block",
            response_body=build_block_session_identity_unavailable(decision),
            http_status=402,
            decision=decision,
            audit_event="block",
        )
    try:
        pending = store.store(
            session_id=session_id,
            body=forward_body,  # a control directive must never reach the provider on replay
            headers=headers,
            target_url=target_url,
            provider=_provider_from_url(target_url),
            model=model,
            projected_tokens=est.projected_input_tokens + est.projected_output_tokens,
            projected_cost_usd=est.projected_cost_usd,
            ttl_seconds=cfg.pending_ttl_seconds,
            projected_input_tokens=est.projected_input_tokens,
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
            reason=f"{decision.reason}:pending_store_write_failed",
            projected_cost=est.projected_cost_usd,
            projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
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
        reason=decision.reason,
        pending_id=pending.pending_id,
        projected_cost=est.projected_cost_usd,
        projected_tokens=int(est.projected_input_tokens) + int(est.projected_output_tokens),
        tip=tip_directive,
    )
    response = build_block(decision, pending)
    if earlier_hold_expired:
        payload = json.loads(response)
        payload["error"]["message"] = (
            "The earlier hold expired and this request grew by more than 10% of the model context window. Review this new estimate and reply yes to approve this request."
        )
        response = json.dumps(payload).encode()
    store.remember_block_response(pending.pending_id, response)
    return GuardOutcome(
        kind="block",
        response_body=response,
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
