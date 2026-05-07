"""
Unit tests for tokenpak/escalation.py

Tests cover:
- detect_insufficient_context_signal: pattern matching and query-term heuristic
- run_escalation_loop: pass A success, pass B success, tier escalation
- run_escalation_loop: cap at max_auto_tier
- recover_from_insufficient_context_signal: no signal, fits, escalation
- Edge cases: empty strings, boundary coverage values
"""

from __future__ import annotations

import pytest

from tokenpak.orchestration.escalation import (
    EscalationResult,
    SignalRecoveryResult,
    detect_insufficient_context_signal,
    recover_from_insufficient_context_signal,
    run_escalation_loop,
)

# ---------------------------------------------------------------------------
# Helpers / Stubs
# ---------------------------------------------------------------------------


def make_chunks(score: float = 0.8, n: int = 3) -> list[dict]:
    """Return fake chunk dicts with a score field."""
    return [{"text": f"chunk {i}", "score": score, "source": f"file_{i}.py"} for i in range(n)]


def make_retrieve_fn(coverage_score: float = 0.8):
    """Return a retrieve_fn whose chunks will produce the given coverage score.

    We embed the intended coverage in the chunk 'score' field;
    the real compute_coverage_score may differ, so we monkey-patch where needed.
    """
    def _retrieve(*, query, tier, k, expand=False, targeted=False):
        return make_chunks(score=coverage_score, n=k)
    return _retrieve


def make_pack_fn(fits: bool = True):
    def _pack(chunks, tier):
        return {"tier": tier, "chunks": len(chunks), "fits": fits}
    return _pack


# ---------------------------------------------------------------------------
# detect_insufficient_context_signal
# ---------------------------------------------------------------------------


class TestDetectInsufficientContextSignal:
    def test_pattern_cant_see(self):
        assert detect_insufficient_context_signal("I can't see the file you mentioned.")

    def test_pattern_did_not_provide(self):
        assert detect_insufficient_context_signal("You did not provide the source code.")

    def test_pattern_dont_have_access(self):
        assert detect_insufficient_context_signal("I don't have access to that module.")

    def test_pattern_do_not_have_access(self):
        assert detect_insufficient_context_signal("I do not have access to the config.")

    def test_no_signal_generic_response(self):
        assert not detect_insufficient_context_signal(
            "Here is the answer based on the provided context."
        )

    def test_empty_response(self):
        assert not detect_insufficient_context_signal("")

    def test_query_term_heuristic_missing_identifier(self):
        # query has a CamelCase identifier that does NOT appear in the response
        assert detect_insufficient_context_signal(
            "The function processes data correctly.",
            query="explain TokenPakRouter in detail",
        )

    def test_query_term_heuristic_identifier_present(self):
        # query term IS present → no signal
        assert not detect_insufficient_context_signal(
            "TokenPakRouter handles request routing.",
            query="explain TokenPakRouter in detail",
        )

    def test_query_term_heuristic_no_strong_terms(self):
        # query has no CamelCase or path tokens → heuristic skipped → no signal
        assert not detect_insufficient_context_signal(
            "The answer is forty-two.",
            query="what is the answer",
        )


# ---------------------------------------------------------------------------
# run_escalation_loop
# ---------------------------------------------------------------------------


class TestRunEscalationLoop:
    """Tests use monkeypatching so compute_coverage_score is fully controlled."""

    def _loop(self, coverage_a, coverage_b, coverage_c=0.3, initial_tier=1, monkeypatch=None):
        coverages = iter([coverage_a, coverage_b, coverage_c])
        if monkeypatch is not None:
            monkeypatch.setattr(
                "tokenpak.orchestration.escalation.compute_coverage_score",
                lambda chunks, terms: next(coverages),
            )
        retrieve = make_retrieve_fn()
        pack = make_pack_fn()
        return run_escalation_loop(
            query="explain MyClass",
            initial_tier=initial_tier,
            retrieve_fn=retrieve,
            pack_fn=pack,
            coverage_threshold=0.55,
            max_auto_tier=3,
        )

    def test_pass_a_sufficient_coverage(self, monkeypatch):
        result = self._loop(0.80, 0.60, monkeypatch=monkeypatch)
        assert isinstance(result, EscalationResult)
        assert result.used_pass_b is False
        assert result.escalated is False
        assert result.coverage == pytest.approx(0.80)
        assert result.tier == 1

    def test_pass_b_sufficient_coverage(self, monkeypatch):
        result = self._loop(0.30, 0.70, monkeypatch=monkeypatch)
        assert result.used_pass_b is True
        assert result.escalated is False
        assert result.coverage == pytest.approx(0.70)
        assert result.tier == 1

    def test_escalation_triggered(self, monkeypatch):
        result = self._loop(0.30, 0.40, coverage_c=0.50, monkeypatch=monkeypatch)
        assert result.escalated is True
        assert result.used_pass_b is True
        assert result.tier == 2  # initial_tier 1 → escalated to 2

    def test_escalation_capped_at_max_tier(self, monkeypatch):
        """If already at max_auto_tier, tier must not exceed it."""
        result = self._loop(0.30, 0.40, coverage_c=0.50, initial_tier=3, monkeypatch=monkeypatch)
        assert result.tier == 3
        assert result.escalated is False  # next_tier == initial_tier → no actual escalation

    def test_result_has_chunks_and_pack(self, monkeypatch):
        result = self._loop(0.80, 0.60, monkeypatch=monkeypatch)
        assert isinstance(result.chunks, list)
        assert isinstance(result.pack, dict)

    def test_escalation_only_one_tier(self, monkeypatch):
        """Escalation must be exactly +1 tier, not jump to max."""
        result = self._loop(0.20, 0.20, coverage_c=0.20, initial_tier=1, monkeypatch=monkeypatch)
        assert result.tier == 2  # must not jump to 3


# ---------------------------------------------------------------------------
# recover_from_insufficient_context_signal
# ---------------------------------------------------------------------------


class TestRecoverFromInsufficientContextSignal:
    def _recover(self, response_text, fits=True, current_tier=1, monkeypatch=None):
        retrieve = make_retrieve_fn()
        pack = make_pack_fn(fits=fits)
        return recover_from_insufficient_context_signal(
            query="explain MyRouter",
            response_text=response_text,
            current_tier=current_tier,
            retrieve_fn=retrieve,
            pack_fn=pack,
            max_auto_tier=3,
        )

    def test_no_signal_returns_early(self):
        result = self._recover("Here is a clear answer about MyRouter.")
        assert isinstance(result, SignalRecoveryResult)
        assert result.triggered is False
        assert result.escalated is False
        assert result.chunks == []
        assert result.pack == {}

    def test_signal_detected_fits_no_escalation(self):
        result = self._recover("I can't see the MyRouter implementation.", fits=True)
        assert result.triggered is True
        assert result.escalated is False

    def test_signal_detected_not_fits_escalates(self):
        result = self._recover("I can't see the MyRouter implementation.", fits=False, current_tier=1)
        assert result.triggered is True
        assert result.escalated is True
        assert result.tier == 2

    def test_signal_detected_not_fits_at_max_tier(self):
        result = self._recover("I can't see the MyRouter implementation.", fits=False, current_tier=3)
        assert result.triggered is True
        assert result.escalated is False
        assert result.tier == 3

    def test_result_contains_chunks_and_pack_on_signal(self):
        result = self._recover("I don't have access to the source.", fits=True)
        assert result.triggered is True
        assert isinstance(result.chunks, list)
        assert isinstance(result.pack, dict)
