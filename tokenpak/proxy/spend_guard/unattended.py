# SPDX-License-Identifier: Apache-2.0
"""Opt-in unattended denial and local, single-request approval contract."""

from __future__ import annotations

# Internal implementation; the supported adapter surface is the CLI/event contract.
__all__: list[str] = []

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .contracts import GuardOutcome, PendingRequest, RiskEstimate
from .pending import PendingStore, hash_request
from .policy import SpendGuardConfig, decide

HEADER = "X-Tokenpak-Unattended"
ENV = "TOKENPAK_SPEND_GUARD_UNATTENDED"
EVENT = "unattended_escalation"
ERROR = "tokenpak_spend_guard_unattended"


def requested(headers: dict[str, str]) -> bool:
    """Use the internal header convention; never consult serving-process env."""
    return any(k.lower() == HEADER.lower() and str(v).strip() == "1" for k, v in headers.items())


def claude_headers(env: dict[str, str]) -> None:
    """Add the marker to a launch-local environment, preserving other headers."""
    if env.get(ENV) == "1":
        lines = env.get("ANTHROPIC_CUSTOM_HEADERS", "").splitlines()
        lines = [line for line in lines if line.partition(":")[0].lower() != HEADER.lower()]
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join([*lines, f"{HEADER}: 1"])


def resolve(
    store: PendingStore,
    pending: PendingRequest,
    body: bytes,
    model: str,
    target_url: str,
    cfg: SpendGuardConfig,
) -> GuardOutcome:
    """A yes, a TIP directive, or a different body cannot redeem this hold."""
    metadata = pending.block_metadata
    risk = RiskEstimate(
        model=pending.model,
        current_context_tokens=0,
        request_tokens=0,
        projected_input_tokens=metadata.get("projected_input_tokens", pending.projected_tokens),
        projected_output_tokens=metadata.get("projected_output_tokens", 0),
        projected_cost_usd=pending.projected_cost_usd,
        cache_hit_ratio=0.0,
    )
    from ._context_window import get_model_max_context
    from .block_response import hard_block

    # An approval cannot override a subsequently tightened hard ceiling.
    decision = decide(risk, cfg, model_max_context_tokens=get_model_max_context(pending.model))
    if decision.decision == "hard_block":
        return GuardOutcome(
            kind="hard_block",
            response_body=hard_block(decision),
            http_status=402,
            decision=decision,
            audit_event="unattended_block",
        )
    consumed = store.consume_unattended(
        pending.pending_id,
        session_id=pending.session_id,
        request_hash=hash_request(body, model),
        target_url=target_url,
    )
    if consumed is not None:
        from .audit import write_audit

        write_audit(
            cfg.audit_db_path,
            event_type="replay",
            session_id=pending.session_id,
            pending_id=pending.pending_id,
            reason="operator_approval_once",
        )
        return GuardOutcome(
            kind="replay",
            body=consumed.raw_request_blob,
            headers=consumed.raw_request_headers,
            target_url=consumed.target_url,
            pending_id=consumed.pending_id,
            audit_event="replay",
        )
    return GuardOutcome(
        kind="block",
        response_body=b'{"error":{}}',
        http_status=402,
        pending_id=pending.pending_id,
        audit_event="unattended_block",
    )


def finalize_block(outcome: GuardOutcome, cfg: SpendGuardConfig, session_id: str) -> GuardOutcome:
    """Return terminal guidance and durably record a content-free escalation."""
    error = json.loads(outcome.response_body or b'{"error":{}}')["error"]
    error["original_error_type"] = error.get("type", "tokenpak_spend_guard_blocked")
    if outcome.decision and outcome.decision.risk:
        risk = outcome.decision.risk
        error.update(
            reason=outcome.decision.reason,
            projected_input_tokens=risk.projected_input_tokens,
            projected_output_tokens=risk.projected_output_tokens,
            projected_cost_usd=risk.projected_cost_usd,
        )
    pending = (
        PendingStore(cfg.audit_db_path).get_by_id(outcome.pending_id)
        if outcome.pending_id
        else None
    )
    if pending:
        error.update(pending.block_metadata)
        error.update(
            projected_tokens=pending.projected_tokens,
            projected_cost_usd=pending.projected_cost_usd,
            model=pending.model,
            pending_id=pending.pending_id,
            expires_at=pending.expires_at,
        )
    else:
        error.setdefault("pending_id", None)
        error.setdefault(
            "projected_tokens",
            (
                (error.get("projected_input_tokens") or 0)
                + (error.get("projected_output_tokens") or 0)
            )
            or None,
        )
    approvable = bool(
        pending
        and pending.unattended
        and pending.status == "pending"
        and pending.expires_at > time.time()
        and outcome.kind != "hard_block"
    )
    error.update(
        type=ERROR,
        reason=error.get("reason") or error.get("cap_dimension") or "guard_blocked",
        message="Spend Guard stopped this unattended request before provider send. "
        "Report this error and stop; an operator must resolve it outside this session.",
        approval_prompt=None,
        approval_prompt_available=False,
        in_band_approval_allowed=False,
        operator_approval_available=approvable,
        retryable=False,
        recovery_status="operator_action_required",
        mode="unattended",
    )
    for key in ("bypass_directive", "retry_after_seconds"):
        error.pop(key, None)
    event = {
        key: error.get(key)
        for key in (
            "reason",
            "projected_tokens",
            "projected_input_tokens",
            "projected_output_tokens",
            "projected_cost_usd",
            "pending_id",
            "expires_at",
            "operator_approval_available",
        )
    }
    event.update(schema_version=1, event_type=EVENT, session_id=session_id)
    try:
        error["escalation_event_id"] = _record_event(cfg.audit_db_path, event)
        error["escalation_recorded"] = True
    except (OSError, sqlite3.Error):
        # The response itself remains reportable when the event sink is broken.
        error["escalation_recorded"] = False
        error["escalation_error"] = "audit_store_unavailable"
    outcome.response_body = json.dumps({"error": error}).encode()
    outcome.audit_event = EVENT
    return outcome


def _record_event(db_path: str, event: dict) -> int:
    """Use the existing audit stream, with one durable event per pending id."""
    from .audit import _ensure_schema

    with closing(sqlite3.connect(str(Path(db_path).expanduser()), timeout=5)) as conn, conn:
        _ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if event["pending_id"]:
            row = conn.execute(
                "SELECT id FROM spend_guard_audit WHERE event_type=? AND pending_id=?",
                (EVENT, event["pending_id"]),
            ).fetchone()
            if row:
                return row[0]
        cur = conn.execute(
            "INSERT INTO spend_guard_audit "
            "(ts, session_id, event_type, decision, reason, projected_tokens, projected_cost_usd, "
            "pending_id, extra_json) VALUES (?, ?, ?, 'block', ?, ?, ?, ?, ?)",
            (
                time.time(),
                event["session_id"],
                EVENT,
                event["reason"],
                event["projected_tokens"] or 0,
                event["projected_cost_usd"] or 0,
                event["pending_id"] or "",
                json.dumps(event),
            ),
        )
        return cur.lastrowid


def events(db_path: str, *, after: int = 0, limit: int = 100) -> list[dict]:
    """Read ordered events without creating state; adapters own their cursor."""
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='spend_guard_audit'"
        ).fetchone():
            return []
        rows = conn.execute(
            "SELECT id, ts, extra_json FROM spend_guard_audit "
            "WHERE event_type=? AND id>? ORDER BY id LIMIT ?",
            (EVENT, after, limit),
        ).fetchall()
        return [dict(json.loads(data), event_id=key, occurred_at=ts) for key, ts, data in rows]
