# SPDX-License-Identifier: Apache-2.0
"""Replay state machine — turns a parsed Intent into a GuardOutcome.

Imported by the orchestrator when a session has a pending block. Sole
function :func:`resolve_pending` is the dispatcher.

Byte-preservation invariant: when intent is POSITIVE, the original held
bytes are returned verbatim (decompressed from gzip storage), with the
original headers attached. This matches the proxy's overall byte-passthrough
guarantee — replay must not re-serialize JSON.

Approval-scope invariant: one typed reply resolves exactly one held request.
A yes / no counts only when it is the newest input item (see ``intent.py``)
and answers the current hold: a re-send of a held request, or of a reply
that already resolved one, is not a new answer.
"""

from __future__ import annotations

from typing import Callable, Optional

from .contracts import GuardOutcome, PendingRequest, TIPDirective
from .intent import Intent
from .pending import PendingStore
from .policy import SpendGuardConfig


def resolve_pending(
    *,
    store: PendingStore,
    pending: PendingRequest,
    intent: Intent,
    tip: Optional[TIPDirective],
    cfg: SpendGuardConfig,
    builders: dict[str, Callable[[PendingRequest], bytes]],
    reply_hash: str,
) -> GuardOutcome:
    """Dispatch on the parsed intent (and optional TIP allow-once).

    Parameters
    ----------
    store : PendingStore
        For consume/discard.
    pending : PendingRequest
        The currently-held request for this session.
    intent : Intent
        Parsed user intent from the new request body.
    tip : TIPDirective | None
        If the new request also carried a ``[TIP: ...]`` header, it can
        force a positive outcome (allow=once / bypass=on) regardless of
        intent classification.
    cfg : SpendGuardConfig
        Live config (for any threshold-aware behavior).
    builders : dict
        Response-body builders, injected by the orchestrator to keep this
        module free of import cycles. Expects keys: ``cancelled``, ``reprompt``,
        ``pending_waiting``.
    reply_hash : str
        :func:`~.pending.hash_request` of the new request's raw bytes. A
        yes / no whose request was already held, or already resolved a
        hold, in this session is re-prompted instead of acted on; a reply
        that resolves this hold is recorded against it.
    """
    # TIP-driven allow-once / bypass: take precedence over text intent. The
    # caller is explicitly authorizing a replay.
    tip_authorizes = tip is not None and (tip.allow_scope == "once" or tip.bypass)
    if tip_authorizes and intent != Intent.NEGATIVE:
        consumed = store.consume(pending.pending_id)
        if consumed is None:
            # Already consumed/discarded — fall through to a fresh pending.
            return GuardOutcome(
                kind="block",
                response_body=builders["pending_waiting"](pending),
                http_status=402,
                pending_id=pending.pending_id,
                audit_event="replay_race",
            )
        return GuardOutcome(
            kind="replay",
            body=consumed.raw_request_blob,
            headers=consumed.raw_request_headers,
            target_url=consumed.target_url,
            pending_id=pending.pending_id,
            audit_event="replay",
        )

    if intent != Intent.AMBIGUOUS and store.reply_seen(pending.session_id, reply_hash):
        # The same trailing yes / no re-sent (a client retry, or a later
        # request carrying the same reply) — not an answer to this hold.
        return GuardOutcome(
            kind="reprompt",
            response_body=builders["reprompt"](pending),
            http_status=402,
            pending_id=pending.pending_id,
            audit_event="stale_reply",
        )

    if intent == Intent.POSITIVE:
        consumed = store.consume(pending.pending_id, answered_by=reply_hash)
        if consumed is None:
            return GuardOutcome(
                kind="block",
                response_body=builders["pending_waiting"](pending),
                http_status=402,
                pending_id=pending.pending_id,
                audit_event="replay_race",
            )
        return GuardOutcome(
            kind="replay",
            body=consumed.raw_request_blob,
            headers=consumed.raw_request_headers,
            target_url=consumed.target_url,
            pending_id=pending.pending_id,
            audit_event="replay",
        )

    if intent == Intent.NEGATIVE:
        store.discard(pending.pending_id, answered_by=reply_hash)
        return GuardOutcome(
            kind="cancel",
            response_body=builders["cancelled"](pending),
            http_status=200,
            pending_id=pending.pending_id,
            audit_event="cancel",
        )

    # AMBIGUOUS (or no intent at all) → re-prompt while keeping the
    # pending in place. This is also the path for follow-up tool turns
    # from an agent that doesn't yet honor the contract — the agent sees
    # repeated 402s and stops.
    return GuardOutcome(
        kind="reprompt",
        response_body=builders["reprompt"](pending),
        http_status=402,
        pending_id=pending.pending_id,
        audit_event="reprompt",
    )


__all__ = ["resolve_pending"]
