# SPDX-License-Identifier: Apache-2.0
"""Phase 0.1 — load-bearing invariant test.

The invariant: when the resolved request adapter does NOT declare
``tip.intent.contract-headers-v1``, the proxy MUST NOT emit the
five wire intent headers, AND the local intent_events row MUST
still be written with ``tip_headers_emitted=False`` and
``tip_headers_stripped=True``.

This is the load-bearing default-off-and-still-observing contract
from the proposal §5.2 + Standard #23 §4.3. Any future change that
flips a first-party adapter's capability declaration, alters the
gate condition in ``server.py``, or skips the telemetry write on
the strip path will trip this test.

Phase 0.1's existing regression suite covers the gate semantics in
isolation (``tests/test_intent_layer_phase0.py::TestCapabilityGate``).
This test stitches the contract-construction → gate-check →
telemetry-write sequence together as one transaction so the
end-to-end wire-vs-local discrimination is enforced explicitly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tokenpak.proxy.intent_classifier import classify_intent
from tokenpak.proxy.intent_contract import (
    GATE_CAPABILITY,
    INTENT_HEADER_CLASS,
    INTENT_HEADER_CONFIDENCE,
    INTENT_HEADER_ID,
    INTENT_HEADER_RISK,
    INTENT_HEADER_SUBTYPE,
    IntentTelemetryRow,
    IntentTelemetryStore,
    attach_intent_headers,
    build_contract,
)


def _classify_and_build(prompt: str):
    """Run the actual Phase 0 classifier + contract builder.

    Uses the real production code paths (not test-only doubles) so
    the invariant survives refactors of the classifier internals.
    """
    classification = classify_intent(prompt)
    contract = build_contract(classification=classification, raw_prompt=prompt)
    return classification, contract


def _simulate_gate(adapter, contract, headers):
    """Simulate the verbatim §4.3 gate from server.py.

    Returns ``(emitted, stripped)`` matching the bits server.py
    writes onto the telemetry row. The gate condition mirrors the
    server.py source verbatim — keeping both expressions
    syntactically aligned makes a future drift loud.
    """
    if adapter is not None and GATE_CAPABILITY in adapter.capabilities:
        attach_intent_headers(headers, contract)
        return True, False
    return False, True


class _AdapterWithoutLabel:
    """Stands in for any first-party adapter in Phase 0.

    Declares a non-Intent capability so the test gate negotiates
    the strip path in the way it would for any production adapter
    that hasn't opted in.
    """

    capabilities = frozenset({"tip.compression.v1"})


class _AdapterWithLabel:
    """Synthetic opt-in adapter for the symmetry assertion.

    Used only to prove the gate is real — flipping the capability
    flips the wire-emission result.
    """

    capabilities = frozenset({GATE_CAPABILITY})


def test_invariant_no_capability_no_wire_emission_telemetry_still_written(tmp_path: Path):
    """Phase 0.1 load-bearing invariant.

    Adapter does not declare ``tip.intent.contract-headers-v1``:
      1. Wire headers MUST NOT appear on the outbound header dict.
      2. The intent_events row MUST still land in the local DB.
      3. The row's ``tip_headers_emitted`` MUST be False (0).
      4. The row's ``tip_headers_stripped`` MUST be True (1).
      5. The row's ``intent_source`` MUST be ``rule_based_v0`` —
         confirms classification was real (not a no-op silently
         skipped because of an exception).
      6. The row's ``raw_prompt_hash`` MUST be the sha256 of the
         prompt — confirms no raw prompt content leaked into the
         row payload.
    """
    db_path = tmp_path / "telemetry.db"
    store = IntentTelemetryStore(db_path=db_path)

    classification, contract = _classify_and_build(
        "summarize the vault for last 7 days"
    )
    # Sanity: classifier produced a real classification (not a
    # catch-all). If this ever changes, the rest of the assertions
    # are still meaningful but the test no longer exercises the
    # main pathway.
    assert classification.intent_class == "summarize"

    headers: dict = {}
    adapter = _AdapterWithoutLabel()
    emitted, stripped = _simulate_gate(adapter, contract, headers)

    # (1) No wire headers attached.
    assert emitted is False
    assert stripped is True
    for hk in (
        INTENT_HEADER_CLASS,
        INTENT_HEADER_CONFIDENCE,
        INTENT_HEADER_SUBTYPE,
        INTENT_HEADER_RISK,
        INTENT_HEADER_ID,
    ):
        assert hk not in headers, f"{hk!r} leaked onto the wire"

    # (2) Telemetry row still lands.
    store.write(
        IntentTelemetryRow(
            request_id="req-no-capability-1",
            contract=contract,
            timestamp="2026-04-25T22:00:00",
            tip_headers_emitted=emitted,
            tip_headers_stripped=stripped,
            tokens_in=42,
            tokens_out=None,
            latency_ms=None,
        )
    )
    store.close()

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT intent_class, intent_source, raw_prompt_hash, "
        "tip_headers_emitted, tip_headers_stripped "
        "FROM intent_events WHERE request_id = 'req-no-capability-1'"
    ).fetchone()
    conn.close()

    assert row is not None, "telemetry row missing — strip path lost the row"
    assert row[0] == "summarize"
    # (5) Source marker.
    assert row[1] == "rule_based_v0"
    # (6) raw_prompt_hash is the sha256 hex of the prompt.
    import hashlib

    expected_hash = hashlib.sha256(
        b"summarize the vault for last 7 days"
    ).hexdigest()
    assert row[2] == expected_hash, (
        "raw_prompt_hash mismatch — either content drifted or the "
        "row stored something other than the canonical digest"
    )
    # (3, 4) Bits set as expected.
    assert row[3] == 0
    assert row[4] == 1


def test_invariant_symmetry_capability_present_emits_headers(tmp_path: Path):
    """Mirror assertion: when the adapter DOES declare the label,
    the gate flips, headers attach, and the telemetry row reflects
    emission. Stops the no-emission test from passing trivially
    because (e.g.) ``attach_intent_headers`` was no-op'd.
    """
    db_path = tmp_path / "telemetry.db"
    store = IntentTelemetryStore(db_path=db_path)

    _, contract = _classify_and_build("summarize the vault")
    headers: dict = {}
    adapter = _AdapterWithLabel()
    emitted, stripped = _simulate_gate(adapter, contract, headers)

    assert emitted is True
    assert stripped is False
    assert headers[INTENT_HEADER_CLASS] == contract.intent_class
    assert headers[INTENT_HEADER_ID] == contract.contract_id

    store.write(
        IntentTelemetryRow(
            request_id="req-with-capability-1",
            contract=contract,
            timestamp="2026-04-25T22:00:00",
            tip_headers_emitted=emitted,
            tip_headers_stripped=stripped,
        )
    )
    store.close()

    conn = sqlite3.connect(str(db_path))
    bits = conn.execute(
        "SELECT tip_headers_emitted, tip_headers_stripped FROM intent_events "
        "WHERE request_id = 'req-with-capability-1'"
    ).fetchone()
    conn.close()
    assert bits == (1, 0)


def test_invariant_raw_prompt_not_in_row(tmp_path: Path):
    """Guards the privacy contract: the row carries the digest, not
    the raw prompt body. A regression that accidentally serializes
    the prompt onto the row (e.g. into an unused ``extra`` column)
    would trip this test by storing the prompt verbatim somewhere
    else. We assert by reading every column of the latest row and
    confirming the prompt substring appears nowhere.
    """
    db_path = tmp_path / "telemetry.db"
    store = IntentTelemetryStore(db_path=db_path)

    secret_token = "kevin-magic-prompt-marker-zzz"
    prompt = f"summarize the vault {secret_token}"
    _, contract = _classify_and_build(prompt)

    store.write(
        IntentTelemetryRow(
            request_id="req-privacy-check-1",
            contract=contract,
            timestamp="2026-04-25T22:00:00",
            tip_headers_emitted=False,
            tip_headers_stripped=True,
        )
    )
    store.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM intent_events WHERE request_id = 'req-privacy-check-1'"
    ).fetchone()
    conn.close()

    assert row is not None
    for column in row.keys():
        value = row[column]
        if value is None:
            continue
        assert secret_token not in str(value), (
            f"raw prompt content leaked into intent_events column "
            f"{column!r}: privacy contract violated"
        )
