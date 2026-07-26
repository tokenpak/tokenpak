# SPDX-License-Identifier: Apache-2.0
"""A preview result must not contradict itself.

When compression would not help, the contract is to keep the original and
report ``applied=False``. The totals did that correctly — ``output_tokens``
was reset to ``input_tokens`` and ``saved_tokens`` came out 0. But the block
list was still built from the pipeline's *attempted* compression, so one
payload said both:

    "applied": false, "saved_tokens": 0        # we kept your input
    {"raw_chars": 3321, "final_chars": 0,      # your input was removed
     "retained": false}

A consumer summing the blocks and a consumer reading the totals would reach
opposite conclusions about the same request. When nothing was applied,
nothing was removed.

These assert measured values on real pipeline output, not JSON shape.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.services.preview import PreviewState, run_preview

# High-entropy text: nothing for dedup or alias extraction to find, so the
# pipeline cannot improve on it and the original must be retained.
INCOMPRESSIBLE = " ".join(
    "".join(chr(97 + (i * 7 + j * 13) % 26) for j in range(9)) for i in range(600)
)

# The same turn repeated: real cross-turn redundancy, which is where
# TokenPak's savings actually come from.
_TURN = "Deployment manifest v1: replicas=3, image=svc:1.2.3, port=8080, region=us-east-1. " * 8
COMPRESSIBLE = json.dumps(
    [{"role": "user", "content": _TURN}] * 6
    + [{"role": "user", "content": "Now summarise the drift between revisions."}]
)


def test_not_applied_means_no_block_reports_a_removal() -> None:
    result = run_preview(INCOMPRESSIBLE, input_source="test")

    assert result.state is PreviewState.MEASURED, result.reason
    assert result.applied is False, "high-entropy input should not compress"
    assert result.saved_tokens == 0
    assert result.output_tokens == result.input_tokens

    for block in result.blocks:
        assert block.final_chars == block.raw_chars, (
            f"applied=False but block {block.block_id} reports "
            f"{block.raw_chars} -> {block.final_chars}"
        )
        assert block.retained is True, (
            f"applied=False but block {block.block_id} is marked not retained"
        )


def test_applied_case_reports_a_real_measured_reduction() -> None:
    """The other direction, so 'never compress anything' cannot pass."""
    result = run_preview(COMPRESSIBLE, input_source="test")

    assert result.state is PreviewState.MEASURED, result.reason
    assert result.applied is True, "repeated turns should compress"
    assert result.input_tokens > result.output_tokens > 0
    assert result.saved_tokens == result.input_tokens - result.output_tokens
    assert 0.0 < result.compression_ratio <= 1.0
    assert result.provenance is not None
    assert result.provenance.input_kind == "conversation"
    assert result.provenance.turns == 7


@pytest.mark.parametrize("text", [INCOMPRESSIBLE, COMPRESSIBLE])
def test_invariants_hold_on_both_paths(text: str) -> None:
    result = run_preview(text, input_source="test")

    assert result.state is PreviewState.MEASURED, result.reason
    assert result.input_tokens is not None and result.input_tokens >= 0
    assert result.output_tokens is not None and result.output_tokens >= 0
    assert result.saved_tokens == result.input_tokens - result.output_tokens
    assert result.saved_tokens >= 0, "a negative saving must never be reported"
    assert 0.0 <= result.compression_ratio <= 1.0
    assert result.duration_ms is not None and result.duration_ms > 0.0
    for block in result.blocks:
        assert block.raw_chars >= 0 and block.final_chars >= 0
        assert block.block_id and block.segment_type


def test_a_contradictory_result_cannot_be_constructed() -> None:
    """The invariant is enforced in the type, not only at the call site."""
    from tokenpak.services.preview import (
        PreviewBlock,
        PreviewInvariantError,
        PreviewProvenance,
        PreviewResult,
    )

    with pytest.raises(PreviewInvariantError, match="nothing was removed"):
        PreviewResult(
            state=PreviewState.MEASURED,
            input_tokens=100,
            output_tokens=100,
            saved_tokens=0,
            compression_ratio=0.0,
            duration_ms=1.0,
            applied=False,
            blocks=[
                PreviewBlock(
                    block_id="b1",
                    segment_type="user",
                    order=0,
                    raw_chars=500,
                    final_chars=0,
                    retained=False,
                )
            ],
            provenance=PreviewProvenance(
                input_sha256="0" * 64,
                input_bytes=500,
                input_source="test",
                input_kind="single_turn",
                turns=1,
                tokenizer="t",
                tokenpak_version="test",
                mode="hybrid",
                stages_run=[],
            ),
        )
