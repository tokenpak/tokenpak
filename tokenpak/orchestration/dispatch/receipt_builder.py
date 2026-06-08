"""DispatchReceipt builder — assemble a §4.7 receipt from a finished run.

The :class:`~tokenpak.orchestration.dispatch.runner.FulfillmentLine` finalizes a
:class:`~tokenpak.orchestration.dispatch.models.run.DispatchRun` but does **not**
itself emit a :class:`~tokenpak.orchestration.dispatch.models.receipt.DispatchReceipt`
— in v0.1-alpha the CLI ``tokenpak dispatch receipt`` verb only *reads* a
persisted receipt, and nothing in the runtime writes one. This module closes
that gap: :func:`build_receipt` walks a finished run's station-run / decision /
effect records (read back from the :class:`~tokenpak.orchestration.dispatch.ledger.db.RunLedger`)
and assembles the receipt, aggregating per-station telemetry into the §4.7
:class:`~tokenpak.orchestration.dispatch.models.receipt.ReceiptTelemetry` block.

The receipt is a pure projection of records already persisted by the runner; it
makes no LLM call, no network call, and is fully deterministic given the same
run. :func:`build_and_write_receipt` additionally persists the receipt and links
its id onto the run (``DispatchRun.receipt_id``), which is the shape the
``tokenpak dispatch receipt`` reader expects.

Telemetry note (v0.1-alpha): the per-station token spend is not yet threaded
back from TIP into the persisted :class:`DispatchStationRun` records, so token
totals here are aggregated from whatever the caller supplies via
``token_overrides`` (the deterministic fixtures pass the mocked turn spend). When
the proxy attribution columns (Standards Delta v0 §7) are wired through, this
builder reads them directly and the override seam is removed. Until then the
override keeps the receipt's telemetry block assertable without fabricating
numbers the runtime cannot yet observe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Optional
from uuid import uuid4

from .ledger.db import RunLedger
from .models.receipt import (
    DispatchReceipt,
    ReceiptDecision,
    ReceiptEffect,
    ReceiptStation,
    ReceiptTelemetry,
)
from .models.run import DispatchRun

# First-N characters of a station's result payload kept on the receipt (§4.7:
# "first 500 chars; full in DispatchStationRun").
_EXCERPT_LIMIT = 500


def _excerpt(payload: Optional[dict]) -> str:
    """Return a <=500-char string excerpt of a station's result payload (§4.7)."""

    if not payload:
        return ""
    text = str(payload)
    return text[:_EXCERPT_LIMIT]


def build_receipt(
    *,
    run: DispatchRun,
    ledger: RunLedger,
    final_status: str,
    receipt_id: Optional[str] = None,
    token_overrides: Optional[Mapping[str, int]] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> DispatchReceipt:
    """Assemble a :class:`DispatchReceipt` for a finished ``run`` (§4.7).

    Reads the run's station runs, decisions, and effects back from ``ledger`` and
    projects them onto the receipt's station / decision / effect rows. Per-station
    telemetry is aggregated into the :class:`ReceiptTelemetry` block. ``run`` is
    expected to be the *finalized* run record (terminal status, ``ended_at`` set);
    ``final_status`` is the receipt's headline status (typically ``run.status``).

    ``token_overrides`` maps a station_run id → that station's output-token spend
    (the v0.1-alpha telemetry seam; see the module docstring). Absent entries
    contribute 0 — the receipt never fabricates a number the runtime cannot
    observe.
    """

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    overrides = dict(token_overrides or {})

    station_runs = ledger.read_station_runs_for_run(run.id)
    stations: list[ReceiptStation] = []
    total_output_tokens = 0
    for sr in station_runs:
        stations.append(
            ReceiptStation(
                station_run_id=sr.id,
                worker_id=sr.worker_id,
                status=sr.status.value,
                tip_request_ids=list(sr.tip_request_ids),
                result_payload_excerpt=_excerpt(sr.result_payload),
            )
        )
        total_output_tokens += int(overrides.get(sr.id, 0))

    decisions: list[ReceiptDecision] = []
    for decision_id in run.decisions:
        decision = ledger.read_decision(decision_id)
        if decision is not None:
            decisions.append(
                ReceiptDecision(decision_id=decision.id, status=decision.status.value)
            )

    effects: list[ReceiptEffect] = []
    for effect_id in run.effects:
        effect = ledger.read_effect(effect_id)
        if effect is not None:
            effects.append(
                ReceiptEffect(
                    effect_id=effect.id,
                    status=effect.status.value,
                    target=effect.target,
                )
            )

    return DispatchReceipt(
        id=receipt_id or f"receipt_{uuid4().hex}",
        job_id=run.job_id,
        run_id=run.id,
        route_id=run.route_id,
        stations=stations,
        decisions=decisions,
        effects=effects,
        telemetry=ReceiptTelemetry(total_output_tokens=total_output_tokens),
        final_status=final_status,
        created_at=now,
    )


def build_and_write_receipt(
    *,
    run: DispatchRun,
    ledger: RunLedger,
    final_status: str,
    receipt_id: Optional[str] = None,
    token_overrides: Optional[Mapping[str, int]] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> DispatchReceipt:
    """Build a receipt, persist it, and link its id onto the run (§4.4 ``receipt_id``).

    Writes the receipt to the Run Ledger and updates the run's ``receipt_id`` so
    the ``tokenpak dispatch receipt`` reader (which queries ``dispatch_receipts``
    by job id) finds it. Returns the persisted receipt.
    """

    receipt = build_receipt(
        run=run,
        ledger=ledger,
        final_status=final_status,
        receipt_id=receipt_id,
        token_overrides=token_overrides,
        clock=clock,
    )
    ledger.write_receipt(receipt)
    linked = run.model_copy(update={"receipt_id": receipt.id})
    ledger.write_run(linked)
    return receipt


__all__ = ["build_receipt", "build_and_write_receipt"]
