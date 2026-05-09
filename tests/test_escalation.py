from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.escalation", reason="module not available in current build")
from tokenpak.escalation import (
    detect_insufficient_context_signal,
    recover_from_insufficient_context_signal,
    run_escalation_loop,
)


def test_loop_stays_at_initial_tier_when_coverage_strong() -> None:
    calls: list[tuple[int, bool]] = []

    def retrieve_fn(**kwargs):
        calls.append((kwargs["tier"], kwargs.get("expand", False)))
        return [{"text": "Need file src/main.py for MyClass", "score": 1.0, "path": "src/main.py"}]

    def pack_fn(chunks, tier):
        return {"fits": True, "tier": tier, "count": len(chunks)}

    result = run_escalation_loop(
        query="Find MyClass in src/main.py",
        initial_tier=1,
        retrieve_fn=retrieve_fn,
        pack_fn=pack_fn,
    )

    assert result.tier == 1
    assert result.used_pass_b is False
    assert result.escalated is False
    assert calls == [(1, False)]


def test_loop_expands_retrieval_on_weak_coverage() -> None:
    calls: list[tuple[int, bool]] = []

    def retrieve_fn(**kwargs):
        calls.append((kwargs["tier"], kwargs.get("expand", False)))
        if kwargs.get("expand", False):
            return [{"text": "Contains MyClass and src/main.py", "score": 1.0, "path": "src/main.py"}]
        return [{"text": "unrelated", "score": 0.01, "path": "a.txt"}]

    def pack_fn(chunks, tier):
        return {"fits": True, "tier": tier, "count": len(chunks)}

    result = run_escalation_loop(
        query="Find MyClass in src/main.py",
        initial_tier=1,
        retrieve_fn=retrieve_fn,
        pack_fn=pack_fn,
    )

    assert result.tier == 1
    assert result.used_pass_b is True
    assert result.escalated is False
    assert calls == [(1, False), (1, True)]


def test_loop_escalates_exactly_one_tier() -> None:
    def retrieve_fn(**kwargs):
        if kwargs["tier"] == 2:
            return [{"text": "MyClass src/main.py", "score": 1.0, "path": "src/main.py"}]
        return [{"text": "weak", "score": 0.01, "path": "x.txt"}]

    def pack_fn(chunks, tier):
        return {"fits": True, "tier": tier, "count": len(chunks)}

    result = run_escalation_loop(
        query="Find MyClass in src/main.py",
        initial_tier=1,
        retrieve_fn=retrieve_fn,
        pack_fn=pack_fn,
    )

    assert result.tier == 2
    assert result.escalated is True


def test_loop_respects_max_auto_tier() -> None:
    seen_tiers: list[int] = []

    def retrieve_fn(**kwargs):
        seen_tiers.append(kwargs["tier"])
        return [{"text": "still weak", "score": 0.01, "path": "x.txt"}]

    def pack_fn(chunks, tier):
        return {"fits": True, "tier": tier, "count": len(chunks)}

    result = run_escalation_loop(
        query="Find MyClass in src/main.py",
        initial_tier=3,
        retrieve_fn=retrieve_fn,
        pack_fn=pack_fn,
        max_auto_tier=3,
    )

    assert result.tier == 3
    assert result.escalated is False
    assert seen_tiers[-1] == 3


def test_signal_detection_catches_insufficient_context_patterns() -> None:
    assert detect_insufficient_context_signal("I don't have access to that file.") is True
    assert detect_insufficient_context_signal("You didn't provide enough details.") is True


def test_signal_detection_ignores_false_positives() -> None:
    assert detect_insufficient_context_signal("I can see the issue and will fix it.") is False


def test_signal_recovery_escalates_only_if_pack_does_not_fit() -> None:
    seen_tiers: list[int] = []

    def retrieve_fn(**kwargs):
        seen_tiers.append(kwargs["tier"])
        return [{"text": "MyClass src/main.py", "score": 1.0, "path": "src/main.py"}]

    def pack_fn(_chunks, tier):
        # same-tier repack doesn't fit, escalated tier fits
        return {"fits": tier >= 2, "tier": tier}

    out = recover_from_insufficient_context_signal(
        query="Find MyClass in src/main.py",
        response_text="I can't see the file contents.",
        current_tier=1,
        retrieve_fn=retrieve_fn,
        pack_fn=pack_fn,
        max_auto_tier=3,
    )

    assert out.triggered is True
    assert out.escalated is True
    assert out.tier == 2
    assert seen_tiers == [1, 2]
