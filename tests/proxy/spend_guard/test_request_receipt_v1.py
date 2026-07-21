# SPDX-License-Identifier: Apache-2.0
"""Receipt v1 proof-object tests.

Covers the AC-5 cases: a complete receipt, missing cost data, dropped-context
reasons, and redaction behavior — plus the honesty contract (AC-3) that an
unobservable field is explicit-unavailable, never a fabricated value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from tokenpak.proxy.spend_guard.receipt import (
    SCHEMA_VERSION,
    ProofField,
    ReceiptDebugPointer,
    build_request_receipt,
    render_receipt,
)

_FIXED = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return _FIXED


def _complete_row() -> dict:
    """A fully-populated monitor ``requests`` row."""
    return {
        "id": "42",
        "model": "claude-sonnet",
        "request_type": "chat",
        "endpoint": "/v1/messages",
        "input_tokens": 200,
        "output_tokens": 50,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 20,
        "estimated_cost": 0.018,
        "would_have_saved": 6000,
        "status": "success",
        "session_id": "sess-1",
        "agent_id": "proxy-test",
        "cycle_id": "cyc-9",
        "dispatch_job_id": "job-3",
    }


# --- complete receipt -------------------------------------------------------


def test_complete_receipt_is_fully_proven():
    r = build_request_receipt(_complete_row(), clock=_clock)
    d = r.to_dict()

    assert d["schema_version"] == SCHEMA_VERSION
    assert d["receipt_id"] == "rcpt_42"  # stable id derived from request id
    assert d["created_at"] == _FIXED.isoformat()

    assert d["route"]["provider"] == {"available": True, "value": "anthropic"}
    assert d["route"]["model"] == {"available": True, "value": "claude-sonnet"}
    assert d["cost"]["input_tokens"]["value"] == 200
    assert d["cost"]["estimated_cost_usd"] == {"available": True, "value": 0.018}
    assert d["optimization"]["would_have_saved_tokens"] == {"available": True, "value": 6000}
    assert d["context"]["cache_read_tokens"] == {"available": True, "value": 80}
    assert d["trail"]["agent_id"]["value"] == "proxy-test"
    assert d["trail"]["dispatch_job_id"]["value"] == "job-3"


def test_stable_id_is_deterministic():
    r1 = build_request_receipt(_complete_row(), clock=_clock)
    r2 = build_request_receipt(_complete_row(), clock=_clock)
    assert r1.receipt_id == r2.receipt_id == "rcpt_42"


def test_provider_derivation():
    def provider(model: str):
        d = build_request_receipt({"id": "x", "model": model}, clock=_clock).to_dict()
        return d["route"]["provider"]

    assert provider("claude-opus")["value"] == "anthropic"
    assert provider("gpt-4o")["value"] == "openai"
    assert provider("gemini-1.5")["value"] == "google"
    # Unknown provider is explicitly unavailable, never guessed.
    unknown = provider("some-local-model")
    assert unknown["available"] is False
    assert unknown["reason"] == "provider_not_derivable"


# --- missing cost data (AC-5) ----------------------------------------------


def test_missing_cost_is_explicit_unavailable_not_zero():
    row = {"id": "7", "model": "gpt-4o", "input_tokens": 10, "output_tokens": 5}
    d = build_request_receipt(row, clock=_clock).to_dict()

    cost = d["cost"]["estimated_cost_usd"]
    assert cost == {"available": False, "reason": "cost_not_recorded"}
    # The honesty contract: a missing cost is NOT silently rendered as 0.0.
    assert "value" not in cost

    savings = d["optimization"]["would_have_saved_tokens"]
    assert savings == {"available": False, "reason": "savings_not_recorded"}


def test_present_zero_cost_is_proven_not_unavailable():
    # A genuine 0.0 (key present) is PROVEN — distinct from a missing key.
    row = {"id": "8", "model": "claude-haiku", "estimated_cost": 0.0}
    d = build_request_receipt(row, clock=_clock).to_dict()
    assert d["cost"]["estimated_cost_usd"] == {"available": True, "value": 0.0}


def test_empty_record_marks_everything_unavailable():
    d = build_request_receipt(None, clock=_clock).to_dict()
    assert d["request_id"]["available"] is False
    assert d["route"]["model"]["available"] is False
    assert d["cost"]["estimated_cost_usd"]["available"] is False
    assert d["spend_guard"]["decision"]["available"] is False
    assert d["receipt_id"].startswith("rcpt_")  # still has a stable shape


# --- conservative / proxy-only savings attribution -------------------------
# The surfaced per-request saving must never exceed what the canonical savings
# aggregate (which credits only ``cache_origin == 'proxy'``) would credit. By
# construction a positive ``would_have_saved`` is proxy-compression, but a row
# that *explicitly* attributes itself to a non-proxy origin must not surface a
# positive saving — that is the regression this gate locks out.


def _saved_row(**over) -> dict:
    row = {"id": "55", "model": "claude-sonnet", "would_have_saved": 6000}
    row.update(over)
    return row


def test_positive_savings_on_proxy_row_is_proven():
    d = build_request_receipt(_saved_row(cache_origin="proxy"), clock=_clock).to_dict()
    assert d["optimization"]["would_have_saved_tokens"] == {"available": True, "value": 6000}


def test_positive_savings_without_cache_origin_is_proven():
    # Production rows do not carry cache_origin; the conservative-by-construction
    # invariant (positive saving => proxy compression) keeps this honest, so a
    # recorded positive saving is still surfaced — the gate must NOT hide it.
    d = build_request_receipt(_saved_row(), clock=_clock).to_dict()
    assert d["optimization"]["would_have_saved_tokens"] == {"available": True, "value": 6000}


def test_positive_savings_on_client_row_is_unavailable_not_raw():
    # A non-proxy-attributed (byte-preserved / client-cached) request must NOT
    # surface the raw would_have_saved as a saving — explicit-unavailable, never
    # the raw value, never a fabricated 0.
    d = build_request_receipt(
        _saved_row(would_have_saved=5, cache_origin="client"), clock=_clock
    ).to_dict()
    savings = d["optimization"]["would_have_saved_tokens"]
    assert savings == {"available": False, "reason": "savings_not_proxy_attributed"}
    assert "value" not in savings


def test_positive_savings_on_unknown_origin_is_unavailable():
    d = build_request_receipt(
        _saved_row(would_have_saved=12, cache_origin="unknown"), clock=_clock
    ).to_dict()
    assert d["optimization"]["would_have_saved_tokens"]["available"] is False
    assert d["optimization"]["would_have_saved_tokens"]["reason"] == "savings_not_proxy_attributed"


def test_zero_savings_on_client_row_is_proven_zero():
    # A byte-preserved row records would_have_saved == 0 (no compression); a
    # present 0 carries no saving claim, so it stays proven — distinct from the
    # positive-non-proxy case above and from a genuinely-absent saving.
    d = build_request_receipt(
        _saved_row(would_have_saved=0, cache_origin="client"), clock=_clock
    ).to_dict()
    assert d["optimization"]["would_have_saved_tokens"] == {"available": True, "value": 0}


# --- dropped-context reasons (AC-5) ----------------------------------------


def test_dropped_context_reasons_are_carried():
    dropped = [
        {"ref": "vault/old-notes.md", "reason": "low_relevance"},
        {"ref": "tool/result-12", "reason": "duplicate_of_cache"},
    ]
    included = [{"ref": "vault/spec.md"}]
    d = build_request_receipt(
        _complete_row(),
        context_included=included,
        context_dropped=dropped,
        clock=_clock,
    ).to_dict()

    assert d["context"]["included"] == {"available": True, "value": included}
    assert d["context"]["dropped"]["available"] is True
    assert d["context"]["dropped"]["value"][0]["reason"] == "low_relevance"


def test_context_selection_unavailable_by_default():
    # Until the context-selection proof is threaded through, include/drop is
    # explicitly unavailable — not an empty list implying "nothing dropped".
    d = build_request_receipt(_complete_row(), clock=_clock).to_dict()
    assert d["context"]["included"] == {
        "available": False,
        "reason": "context_selection_not_captured",
    }
    assert d["context"]["dropped"]["reason"] == "context_selection_not_captured"


# --- spend-guard decision ---------------------------------------------------


@dataclass
class _FakeRisk:
    model: str = "claude-opus"
    projected_cost_usd: float = 1.23


@dataclass
class _FakeDecision:
    decision: str = "block"
    reason: str = "projected_tokens_exceeded"
    requires_approval: bool = True
    threshold_hit: Optional[str] = "hard_block_ratio"
    risk: Optional[_FakeRisk] = None


def test_spend_guard_decision_populates_block():
    decision = _FakeDecision(risk=_FakeRisk())
    d = build_request_receipt(None, decision=decision, clock=_clock).to_dict()

    sg = d["spend_guard"]
    assert sg["decision"] == {"available": True, "value": "block"}
    assert sg["reason"]["value"] == "projected_tokens_exceeded"
    assert sg["requires_approval"]["value"] is True
    assert sg["threshold_hit"]["value"] == "hard_block_ratio"
    # The attached RiskEstimate backfills model + projected cost on a record-less
    # pre-send receipt.
    assert d["route"]["model"]["value"] == "claude-opus"
    assert d["cost"]["estimated_cost_usd"]["value"] == 1.23


def test_no_decision_marks_guard_unavailable():
    d = build_request_receipt(_complete_row(), clock=_clock).to_dict()
    sg = d["spend_guard"]
    assert sg["decision"] == {"available": False, "reason": "guard_decision_not_recorded"}
    assert sg["requires_approval"]["available"] is False


# --- redaction (AC-5) -------------------------------------------------------


def test_redaction_drops_capture_path():
    pointer = ReceiptDebugPointer(
        present=True,
        trace_id="t-42",
        capture_mode="encrypted",
        path="/home/someuser/.tokenpak/debug/t-42.enc",
    )
    r = build_request_receipt(_complete_row(), debug_pointer=pointer, clock=_clock)

    redacted = r.to_dict(redact=True)
    assert "path" not in redacted["debug_pointer"]
    assert redacted["debug_pointer"]["trace_id"] == "t-42"
    assert redacted["debug_pointer"]["capture_mode"] == "encrypted"

    raw = r.to_dict(redact=False)
    assert raw["debug_pointer"]["path"] == "/home/someuser/.tokenpak/debug/t-42.enc"


def test_render_is_redaction_safe_json():
    pointer = ReceiptDebugPointer(
        present=True,
        trace_id="t-42",
        capture_mode="hash_only",
        path="/home/someuser/.tokenpak/debug/t-42.hash",
    )
    out = render_receipt(
        build_request_receipt(_complete_row(), debug_pointer=pointer, clock=_clock),
        redact=True,
    )
    # Valid JSON, and the redacted render never leaks the on-disk path.
    parsed = json.loads(out)
    assert parsed["schema_version"] == SCHEMA_VERSION
    assert "/home/someuser" not in out


def test_proof_field_helpers():
    known = ProofField.known(5)
    assert known.to_dict() == {"available": True, "value": 5}
    missing = ProofField.unavailable("nope")
    assert missing.to_dict() == {"available": False, "reason": "nope"}
