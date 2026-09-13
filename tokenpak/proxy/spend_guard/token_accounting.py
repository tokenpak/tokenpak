# SPDX-License-Identifier: Apache-2.0
"""Correlate token-only reservations with committed native provider facts."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

from .request_tokens import RequestTokenObservation
from .request_workload import _count
from .reservation import (
    ReservationRef,
    ReservationStore,
    ReservationUnavailable,
    _generation_changed,
    _identity,
    _number,
    _private_connection,
)

_FIELDS = (
    "guard_reservation_id",
    "guard_ledger_key",
    "guard_token_usage_complete",
    "guard_usage_complete",
    "session_id",
    "agent_id",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_read_tokens",
    "provider_cache_creation_tokens",
    "provider_usage_source",
    "provider_usage_provider",
)


def _rows(store, ledger, guard, *, cutoff=None, reservation_id=None):
    if store.accounting_basis != "provider_tokens":
        raise ReservationUnavailable("provider-token accounting basis required")
    store._check_domain(ledger)
    store._check_basis(guard)
    if cutoff is not None:
        cursor = ledger.execute(
            "SELECT guard_reservation_id, guard_ledger_key, guard_token_usage_complete, "
            "guard_usage_complete, session_id, agent_id, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, provider_input_tokens, "
            "provider_output_tokens, provider_cache_read_tokens, provider_cache_creation_tokens, "
            "provider_usage_source, provider_usage_provider "
            "FROM requests WHERE timestamp >= ? LIMIT 100001",
            (cutoff,),
        )
    else:
        cursor = ledger.execute(
            "SELECT guard_reservation_id, guard_ledger_key, guard_token_usage_complete, "
            "guard_usage_complete, session_id, agent_id, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, provider_input_tokens, "
            "provider_output_tokens, provider_cache_read_tokens, provider_cache_creation_tokens, "
            "provider_usage_source, provider_usage_provider "
            "FROM requests WHERE guard_reservation_id=? AND guard_ledger_key=? LIMIT 100001",
            (reservation_id, store.ledger_key),
        )
    rows = [dict(zip(_FIELDS, row)) for row in cursor]
    if len(rows) > 100000:
        raise ReservationUnavailable("token accounting row limit exceeded")
    result = []
    deadline = time.monotonic() + 2.0
    seen = set()
    for row in rows:
        if time.monotonic() >= deadline:
            raise ReservationUnavailable("token receipt validation deadline exceeded")
        ref = row["guard_reservation_id"]
        if (
            not ref
            or ref in seen
            or row["guard_ledger_key"] != store.ledger_key
            or row["guard_token_usage_complete"] != 1
            or row["guard_usage_complete"] != 0
        ):
            raise ReservationUnavailable("committed token coverage is incomplete")
        seen.add(ref)
        coverage = guard.execute(
            "SELECT session_id, attempts, substr(token_observation_json, 1, 4097), accounting_basis "
            "FROM budget_guard_coverage WHERE reservation_id=? AND ledger_key=?",
            (ref, store.ledger_key),
        ).fetchall()
        reservation = guard.execute(
            "SELECT session_id, agent_id, accounting_basis FROM budget_reservations WHERE reservation_id=? AND ledger_key=?",
            (ref, store.ledger_key),
        ).fetchone()
        if (
            len(coverage) != 1
            or tuple(coverage[0][:2]) != (row["session_id"], 1)
            or coverage[0][3] != "provider_tokens"
            or reservation is None
            or tuple(reservation) != (row["session_id"], row["agent_id"].lower(), "provider_tokens")
        ):
            raise ReservationUnavailable("token reservation correlation differs")
        try:
            observation = RequestTokenObservation.from_json(coverage[0][2])
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ReservationUnavailable("committed token receipt is invalid") from exc
        if not observation.token_usage_complete or not observation.response_complete:
            raise ReservationUnavailable("committed native token usage is incomplete")
        if (
            row["model"] != observation.request_model
            or row["provider_usage_source"] != "provider_usage_object"
            or row["provider_usage_provider"] != observation.provider
        ):
            raise ReservationUnavailable("committed provider identity differs")
        for name, expected in (
            ("input_tokens", observation.input_tokens),
            ("output_tokens", observation.output_tokens),
            ("cache_read_tokens", observation.cache_read_tokens),
            ("cache_creation_tokens", observation.cache_creation_tokens),
            ("provider_input_tokens", observation.uncached_input_tokens),
            ("provider_output_tokens", observation.output_tokens),
            ("provider_cache_read_tokens", observation.cache_read_tokens),
            ("provider_cache_creation_tokens", observation.cache_creation_tokens),
        ):
            if type(row[name]) is not int or row[name] != expected:
                raise ReservationUnavailable("committed provider token counts differ")
        result.append((row, observation))
    return result


def _totals(rows, agent_id):
    totals = {
        name: 0
        for name in (
            "agent_tokens_total",
            "agent_cache_read_tokens",
            "fleet_tokens_total",
            "fleet_cache_read_tokens",
        )
    }
    committed = {}
    for row, observation in rows:
        committed[row["guard_reservation_id"]] = (row["session_id"], row["agent_id"].lower())
        for prefix in ("fleet", "agent"):
            if prefix == "agent" and row["agent_id"].lower() != agent_id:
                continue
            totals[prefix + "_tokens_total"] += observation.input_tokens + observation.output_tokens
            totals[prefix + "_cache_read_tokens"] += observation.cache_read_tokens
    if any(_count(value) is None for value in totals.values()):
        raise ReservationUnavailable("aggregate token counts exceed exact range")
    return totals, committed


def recorded_tokens(
    store: ReservationStore,
    ledger: sqlite3.Connection,
    guard: sqlite3.Connection,
    cutoff: str,
    agent_id: str,
) -> tuple[dict[str, int], dict[str, tuple[str, str]]]:
    """Read exact committed token totals within the caller's locked transactions."""
    return _totals(_rows(store, ledger, guard, cutoff=cutoff), agent_id)


def settle_tokens(store: ReservationStore, ref: ReservationRef) -> bool:
    """Settle only the exact committed token receipt; no monetary value is read."""
    if (
        ref.accounting_basis != "provider_tokens"
        or ref.store_path != str(store.path)
        or ref.ledger_key != store.ledger_key
    ):
        raise ReservationUnavailable("token settlement accounting scope differs")
    with closing(_private_connection(store.path, write=True)) as guard:
        guard.execute("BEGIN IMMEDIATE")
        try:
            with closing(
                sqlite3.connect(store.monitor_path.as_uri() + "?mode=ro", uri=True)
            ) as ledger:
                rows = _rows(store, ledger, guard, reservation_id=ref.reservation_id)
            if len(rows) != 1:
                raise ReservationUnavailable("token settlement lacks matching committed usage")
            _, observation = rows[0]
            cursor = guard.execute(
                "UPDATE budget_reservations SET status='settled', actual_cost_usd=NULL, actual_tokens=?, actual_cache_read_tokens=?, settled_at=? "
                "WHERE reservation_id=? AND ledger_key=? AND accounting_basis='provider_tokens' AND status IN ('active', 'expired')",
                (
                    observation.input_tokens + observation.output_tokens,
                    observation.cache_read_tokens,
                    time.time(),
                    ref.reservation_id,
                    store.ledger_key,
                ),
            )
            if cursor.rowcount:
                guard.execute(
                    "UPDATE budget_guard_coverage SET state='recorded' WHERE ledger_key=? AND reservation_id=? AND attempts=1 AND accounting_basis='provider_tokens'",
                    (store.ledger_key, ref.reservation_id),
                )
                _generation_changed(guard, store.ledger_key)
            guard.commit()
            return bool(cursor.rowcount)
        except BaseException:
            guard.rollback()
            raise


def token_snapshot(
    store: ReservationStore, session_id: str, window_seconds: int, *, owner_instance_id: str
) -> dict:
    """Read one generation-fenced token view with no migrations or cleanup."""
    _identity(session_id, "session_id")
    _identity(owner_instance_id, "owner_instance_id")
    if not _number(window_seconds, "window_seconds", integer=True):
        raise ValueError("token snapshot window must be positive")
    now = time.time()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - window_seconds))
    deadline = time.monotonic() + 2.0
    reasons = set()
    observation_result = {
        "available": False,
        "reason_codes": ["session_unobserved"],
        "request_id_sha256": None,
        "started_at": None,
        "completed_at": None,
        "observation": None,
    }
    with closing(_private_connection(store.path, write=False)) as guard:
        guard.execute("BEGIN")
        guard.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        store._check_basis(guard)
        generation = store._read_generation(guard)
        coverage = guard.execute(
            "SELECT * FROM budget_guard_coverage WHERE ledger_key=? AND (ended_at IS NULL OR ended_at>=?) LIMIT 100001",
            (store.ledger_key, now - window_seconds),
        ).fetchall()
        if len(coverage) > 100000:
            raise ReservationUnavailable("token coverage row limit exceeded")
        with closing(sqlite3.connect(store.monitor_path.as_uri() + "?mode=ro", uri=True)) as ledger:
            ledger.execute("BEGIN")
            ledger.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = _rows(store, ledger, guard, cutoff=cutoff)
            agents = {row["agent_id"].lower() for row, _ in rows if row["session_id"] == session_id}
            agent = next(iter(agents)) if len(agents) == 1 else None
            recorded, committed = _totals(rows, agent or "")
            pending = store._active(guard, agent or "", now, committed)
            if guard.execute(
                "SELECT 1 FROM budget_reservations r WHERE r.ledger_key=? AND r.status IN ('active','expired') AND NOT EXISTS "
                "(SELECT 1 FROM budget_guard_coverage c WHERE c.ledger_key=r.ledger_key AND c.reservation_id=r.reservation_id) LIMIT 1",
                (store.ledger_key,),
            ).fetchone():
                reasons.add("reservation_coverage_incomplete")
            for record in coverage:
                if record["accounting_basis"] != "provider_tokens":
                    reasons.add("accounting_basis_mismatch")
                if record["ended_at"] is None:
                    reasons.add("request_in_progress")
                elif record["attempts"] and not (
                    record["attempts"] == 1 and record["reservation_id"] in committed
                ):
                    reasons.add("forward_usage_unresolved")
            if not agents:
                reasons.add("session_unobserved")
            if len(agents) > 1:
                reasons.add("session_agent_contradiction")
            candidates = [
                row for row in coverage if row["session_id"] == session_id and row["attempts"]
            ]
            if candidates:
                latest = max(candidates, key=lambda row: (row["started_at"], row["coverage_id"]))
                local_reasons = set()
                if (
                    latest["attempts"] != 1
                    or latest["ended_at"] is None
                    or latest["reservation_id"] not in committed
                ):
                    local_reasons.add("latest_request_unresolved")
                if latest["owner_instance_id"] != owner_instance_id:
                    local_reasons.add("request_owner_mismatch")
                if any(
                    row["coverage_id"] != latest["coverage_id"]
                    and (row["ended_at"] is None or row["ended_at"] > latest["started_at"])
                    for row in candidates
                ):
                    local_reasons.add("session_request_order_ambiguous")
                matching = [
                    (row, item)
                    for row, item in rows
                    if row["guard_reservation_id"] == latest["reservation_id"]
                ]
                identity = guard.execute(
                    "SELECT request_id FROM budget_reservations WHERE reservation_id=? AND ledger_key=?",
                    (latest["reservation_id"], store.ledger_key),
                ).fetchone()
                if len(matching) != 1 or identity is None:
                    local_reasons.add("token_ledger_mismatch")
                if not local_reasons:
                    started = _number(latest["started_at"], "started_at")
                    ended = _number(latest["ended_at"], "ended_at")
                    if not now - window_seconds < started <= ended <= now:
                        local_reasons.add("request_time_unavailable")
                    else:
                        _identity(identity[0], "request_id")
                        observation_result.update(
                            request_id_sha256=hashlib.sha256(identity[0].encode()).hexdigest(),
                            started_at=datetime.fromtimestamp(started, timezone.utc).isoformat(),
                            completed_at=datetime.fromtimestamp(ended, timezone.utc).isoformat(),
                            observation=matching[0][1].to_dict(),
                        )
                observation_result["reason_codes"] = sorted(local_reasons)
                observation_result["available"] = not local_reasons
    with closing(_private_connection(store.path, write=False)) as fence:
        store._check_basis(fence)
        if store._read_generation(fence) != generation:
            raise ReservationUnavailable("accounting changed during token observation")
    if time.monotonic() >= deadline:
        raise ReservationUnavailable("token snapshot deadline exceeded")
    if not agent:
        for values in (recorded, pending):
            for key in values:
                if key.startswith("agent_"):
                    values[key] = None
    for values in (recorded, pending):
        values["agent_applicable"] = bool(agent)
    if reasons:
        observation_result["available"] = False
        observation_result["reason_codes"] = sorted(
            set(observation_result["reason_codes"]) | {"accounting_ineligible"}
        )
    return dict(
        observed_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
        ledger_scope_sha256=store.ledger_key,
        accounting_generation=generation,
        accounting_basis="provider_tokens",
        token_coverage_complete=not reasons,
        token_coverage_reason_codes=sorted(reasons),
        recorded_token_usage=recorded,
        pending_projected_token_usage=pending,
        token_observation=observation_result,
    )
