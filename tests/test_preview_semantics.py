# SPDX-License-Identifier: Apache-2.0
"""Semantic tests for `tokenpak preview` and the measured-data contract.

These replace shape-only assertions. The previous suite asserted
``"saved_tokens" in data``, which passed against a simulation that computed
``output = input * 0.65`` with hardcoded block names — the test ratified the
fabrication. Every test here would fail if the simulation were reintroduced.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys

import pytest

from tokenpak.core.contracts import DataState, measured, no_data, unavailable
from tokenpak.core.contracts.measured import Measured
from tokenpak.services.preview import (
    PreviewInvariantError,
    PreviewProvenance,
    PreviewResult,
    PreviewState,
    run_preview,
)


def _conversation(turns: int = 12, repeat: int = 3) -> str:
    """A conversation with redundancy repeated across turns."""
    blob = (
        "You are a helpful assistant. Follow the repository conventions "
        "in /srv/app/CONTRIBUTING.md carefully.\n" * repeat
    )
    msgs = []
    for i in range(turns // 2):
        msgs.append({"role": "user", "content": blob + f"Question {i}: why does the deploy fail?"})
        msgs.append({"role": "assistant", "content": blob + "Because the migration lock is held."})
    return json.dumps(msgs)


def _high_entropy(lines: int = 40) -> str:
    rng = random.Random(1234)
    msgs = [
        {"role": "user", "content": f"{rng.random():.17f} unique observation {i} " * 3}
        for i in range(lines)
    ]
    return json.dumps(msgs)


# --------------------------------------------------------------------------
# The defect that shipped: fabricated numbers
# --------------------------------------------------------------------------


def test_redundant_conversation_compresses_more_than_high_entropy():
    """The core semantic claim: savings track actual redundancy.

    A simulation returning a fixed ratio cannot satisfy this — it would report
    the same percentage for both inputs.
    """
    redundant = run_preview(_conversation(), input_source="test")
    unique = run_preview(_high_entropy(), input_source="test")

    assert redundant.state is PreviewState.MEASURED
    assert unique.state is PreviewState.MEASURED
    assert redundant.compression_ratio is not None
    assert unique.compression_ratio is not None
    assert redundant.compression_ratio > unique.compression_ratio, (
        "redundant input must compress more than high-entropy input; "
        f"got {redundant.compression_ratio} vs {unique.compression_ratio}"
    )
    assert redundant.saved_tokens and redundant.saved_tokens > 0
    assert redundant.applied is True


def test_ratio_is_not_a_fixed_constant():
    """The simulation always produced ~0.35. Distinct inputs must differ."""
    ratios = {
        run_preview(_conversation(turns=4, repeat=2), input_source="t").compression_ratio,
        run_preview(_conversation(turns=12, repeat=6), input_source="t").compression_ratio,
        run_preview(_high_entropy(10), input_source="t").compression_ratio,
    }
    assert len(ratios) > 1, f"compression ratio looks constant across inputs: {ratios}"
    assert 0.35 not in ratios or len(ratios) > 1


def test_duration_is_measured_not_hardcoded():
    """The simulation reported exactly 2.3ms every time."""
    durations = [
        run_preview(_conversation(), input_source="t").duration_ms,
        run_preview(_conversation(turns=6), input_source="t").duration_ms,
        run_preview(_high_entropy(20), input_source="t").duration_ms,
    ]
    assert all(d is not None and d > 0 for d in durations)
    # A real measurement may legitimately land on 2.3ms once — small input, fast
    # runner — and it did, on one Python version and not the others. What the
    # simulation did was land there *every* time, which is what this asserts.
    # The ratio check above is already guarded this way; durations were not.
    assert durations.count(2.3) < len(durations), f"duration looks hardcoded: {durations}"
    assert len(set(durations)) > 1, f"durations look hardcoded: {durations}"


def test_block_identities_come_from_the_pipeline():
    """The simulation always emitted the same four invented block names."""
    fabricated = {"system_prompt", "user_context", "debug_logs", "duplicate_text"}
    result = run_preview(_conversation(), input_source="test")
    assert result.state is PreviewState.MEASURED
    for block in result.blocks:
        assert block.block_id, "block must carry a pipeline-assigned id"
        assert block.raw_chars >= 0 and block.final_chars >= 0
    types = {b.segment_type for b in result.blocks}
    assert not (types and types <= fabricated), (
        f"block identities look synthesized rather than measured: {types}"
    )


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"alpha":1,"beta":[2,3,4],"gamma":{"delta":"epsilon"}}',  # no whitespace at all
        "x",
        "short",
        "a b",
        "{}",
        "[]",
        "中文测试",  # non-ASCII, no spaces
    ],
)
def test_no_negative_savings_on_pathological_input(text):
    """The `max(input*0.65, 10)` floor produced -900% savings on JSON input."""
    result = run_preview(text, input_source="test")
    if result.state is not PreviewState.MEASURED:
        return
    assert result.input_tokens is not None and result.input_tokens >= 0
    assert result.output_tokens is not None and result.output_tokens >= 0
    assert result.saved_tokens is not None and result.saved_tokens >= 0
    assert result.compression_ratio is not None
    assert 0.0 <= result.compression_ratio <= 1.0


def test_saved_equals_input_minus_output():
    for text in (_conversation(), _high_entropy(15), "one turn of prose here."):
        r = run_preview(text, input_source="test")
        if r.state is not PreviewState.MEASURED:
            continue
        assert r.saved_tokens == r.input_tokens - r.output_tokens


def test_expansion_retains_original_and_reports_not_applied():
    """If compression would expand the input we keep the original."""
    r = run_preview("hi", input_source="test")
    assert r.state is PreviewState.MEASURED
    assert r.applied is False
    assert r.saved_tokens == 0
    assert r.output_tokens == r.input_tokens


def test_empty_input_is_no_data_with_null_numbers():
    r = run_preview("   \n\t ", input_source="test")
    assert r.state is PreviewState.NO_DATA
    assert r.input_tokens is None
    assert r.output_tokens is None
    assert r.saved_tokens is None
    assert r.compression_ratio is None
    assert r.duration_ms is None
    assert r.reason


def test_measured_result_carries_full_provenance():
    r = run_preview(_conversation(), input_source="unit-test-file")
    assert r.state is PreviewState.MEASURED
    p = r.provenance
    assert p is not None
    assert len(p.input_sha256) == 64
    assert p.input_bytes > 0
    assert p.input_source == "unit-test-file"
    assert p.input_kind == "conversation"
    assert p.turns == 12
    assert p.tokenizer  # the estimator must be named, not implied
    assert p.stages_run, "a measured preview must record which stages ran"
    assert p.tokenpak_version


def test_conversation_shapes_are_recognized():
    conv = [{"role": "user", "content": "a b c"}, {"role": "assistant", "content": "d e f"}]
    for payload in (
        json.dumps(conv),  # bare array
        json.dumps({"messages": conv}),  # provider request body
        "\n".join(json.dumps(m) for m in conv),  # JSONL
    ):
        r = run_preview(payload, input_source="test")
        assert r.provenance is not None
        assert r.provenance.input_kind == "conversation"
        assert r.provenance.turns == 2


def test_invariant_violations_are_rejected_at_construction():
    """The contract must be enforced, not merely documented."""
    prov = PreviewProvenance("a" * 64, 10, "t", "single_turn", 1, "tok", "hybrid", ["dedup"], "0")
    with pytest.raises(PreviewInvariantError):  # saved != input - output
        PreviewResult(
            state=PreviewState.MEASURED,
            input_tokens=100,
            output_tokens=50,
            saved_tokens=999,
            compression_ratio=0.5,
            duration_ms=1.0,
            applied=True,
            provenance=prov,
        )
    with pytest.raises(PreviewInvariantError):  # negative savings
        PreviewResult(
            state=PreviewState.MEASURED,
            input_tokens=10,
            output_tokens=20,
            saved_tokens=-10,
            compression_ratio=0.0,
            duration_ms=1.0,
            applied=False,
            provenance=prov,
        )
    with pytest.raises(PreviewInvariantError):  # ratio out of range
        PreviewResult(
            state=PreviewState.MEASURED,
            input_tokens=10,
            output_tokens=5,
            saved_tokens=5,
            compression_ratio=9.0,
            duration_ms=1.0,
            applied=True,
            provenance=prov,
        )
    with pytest.raises(PreviewInvariantError):  # hardcoded/zero duration
        PreviewResult(
            state=PreviewState.MEASURED,
            input_tokens=10,
            output_tokens=5,
            saved_tokens=5,
            compression_ratio=0.5,
            duration_ms=0.0,
            applied=True,
            provenance=prov,
        )
    with pytest.raises(PreviewInvariantError):  # zeros in an unmeasured state
        PreviewResult(state=PreviewState.UNAVAILABLE, input_tokens=0, reason="x")


# --------------------------------------------------------------------------
# Measured-data contract
# --------------------------------------------------------------------------


def test_unmeasured_values_are_null_never_zero():
    for m in (no_data("nothing yet"), unavailable("db missing")):
        assert m.value is None
        assert m.to_json()["value"] is None
        assert m.render() != "0"
        assert "0.0" not in m.render()
        assert not m.is_measured
        assert not bool(m)


def test_measured_zero_is_still_a_measurement():
    """A real observation of zero must not be confused with absence."""
    m = measured(0, source="monitor_db")
    assert m.is_measured
    assert bool(m) is True
    assert m.to_json()["value"] == 0
    assert m.render(fmt="usd") == "$0.00"


def test_contract_rejects_impossible_states():
    with pytest.raises(ValueError):
        Measured(DataState.MEASURED)  # measured without a value
    with pytest.raises(ValueError):
        Measured(DataState.UNAVAILABLE, value=0)  # unmeasured carrying a number
    with pytest.raises(ValueError):
        Measured(DataState.ERROR)  # error without a reason


def test_render_formats_do_not_fabricate_numbers():
    m = unavailable("monitor_db_not_found")
    for fmt in ("auto", "usd", "pct", "int", "ms", "tokens"):
        out = m.render(fmt=fmt)
        assert not any(ch.isdigit() for ch in out), f"fmt={fmt} produced digits: {out}"


# --------------------------------------------------------------------------
# End-to-end through the real CLI
# --------------------------------------------------------------------------


def test_cli_preview_json_reports_measured_values(tmp_path):
    payload = tmp_path / "conv.json"
    payload.write_text(_conversation())
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak", "preview", "--file", str(payload), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["state"] == "measured"
    assert data["saved_tokens"] == data["input_tokens"] - data["output_tokens"]
    assert data["saved_tokens"] > 0
    assert 0.0 <= data["compression_ratio"] <= 1.0
    assert data["duration_ms"] > 0
    assert data["duration_ms"] != 2.3
    assert data["applied"] is True
    assert data["provenance"]["input_kind"] == "conversation"
    assert data["provenance"]["tokenizer"]
    assert "flags" not in data  # the simulation's hardcoded flags are gone


def test_cli_preview_json_nulls_are_not_zeros(tmp_path):
    payload = tmp_path / "empty.txt"
    payload.write_text("   \n  ")
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak", "preview", "--file", str(payload), "--json"],
        capture_output=True,
        text=True,
    )
    data = json.loads(proc.stdout)
    assert data["state"] == "no_data"
    for key in ("input_tokens", "output_tokens", "saved_tokens", "compression_ratio"):
        assert data[key] is None, f"{key} must be null, got {data[key]!r}"
    assert proc.returncode != 0
