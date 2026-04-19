from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from tokenpak.telemetry.segmentizer import compute_coverage_score, extract_query_terms


RetrieveFn = Callable[..., list[dict[str, Any]]]
PackFn = Callable[[list[dict[str, Any]], int], dict[str, Any]]


_INSUFFICIENT_CONTEXT_PATTERNS = (
    re.compile(r"\bi\s+can't\s+see\b", re.IGNORECASE),
    re.compile(r"\byou\s+did(?:\s+not|n't)\s+provide\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:do\s+not|don't)\s+have\s+access\s+to\b", re.IGNORECASE),
)


@dataclass
class EscalationResult:
    tier: int
    used_pass_b: bool
    escalated: bool
    coverage: float
    chunks: list[dict[str, Any]]
    pack: dict[str, Any]


@dataclass
class SignalRecoveryResult:
    triggered: bool
    escalated: bool
    tier: int
    chunks: list[dict[str, Any]]
    pack: dict[str, Any]


def detect_insufficient_context_signal(response_text: str, query: str = "") -> bool:
    text = response_text or ""

    for pat in _INSUFFICIENT_CONTEXT_PATTERNS:
        if pat.search(text):
            return True

    # "answer missing identifiers from query" heuristic
    # If query has strong identifiers and none are present in answer, likely missing context.
    query_terms = [t for t in extract_query_terms(query) if len(t) >= 3]
    if query_terms:
        lower = text.lower()
        strong_terms = [t for t in query_terms if ("/" in t or "_" in t or any(c.isupper() for c in t))]
        if strong_terms and not any(term.lower() in lower for term in strong_terms):
            return True

    return False


def run_escalation_loop(
    *,
    query: str,
    initial_tier: int,
    retrieve_fn: RetrieveFn,
    pack_fn: PackFn,
    coverage_threshold: float = 0.55,
    max_auto_tier: int = 3,
    initial_k: int = 5,
    expanded_k: int = 10,
) -> EscalationResult:
    query_terms = extract_query_terms(query)

    # Pass A
    chunks_a = retrieve_fn(query=query, tier=initial_tier, k=initial_k, expand=False)
    coverage_a = compute_coverage_score(chunks_a, query_terms)
    if coverage_a >= coverage_threshold:
        pack = pack_fn(chunks_a, initial_tier)
        return EscalationResult(initial_tier, False, False, coverage_a, chunks_a, pack)

    # Pass B (expanded retrieval)
    chunks_b = retrieve_fn(query=query, tier=initial_tier, k=expanded_k, expand=True)
    coverage_b = compute_coverage_score(chunks_b, query_terms)
    if coverage_b >= coverage_threshold:
        pack = pack_fn(chunks_b, initial_tier)
        return EscalationResult(initial_tier, True, False, coverage_b, chunks_b, pack)

    # Escalate exactly +1 tier if possible
    next_tier = min(initial_tier + 1, max_auto_tier)
    escalated = next_tier > initial_tier
    chunks_c = retrieve_fn(query=query, tier=next_tier, k=expanded_k, expand=True)
    coverage_c = compute_coverage_score(chunks_c, query_terms)
    pack = pack_fn(chunks_c, next_tier)
    return EscalationResult(next_tier, True, escalated, coverage_c, chunks_c, pack)


def recover_from_insufficient_context_signal(
    *,
    query: str,
    response_text: str,
    current_tier: int,
    retrieve_fn: RetrieveFn,
    pack_fn: PackFn,
    max_auto_tier: int = 3,
) -> SignalRecoveryResult:
    if not detect_insufficient_context_signal(response_text, query=query):
        return SignalRecoveryResult(False, False, current_tier, [], {})

    # Targeted retrieval first at same tier.
    chunks = retrieve_fn(query=query, tier=current_tier, k=12, expand=True, targeted=True)
    pack = pack_fn(chunks, current_tier)

    # Escalate only if packed result does not fit.
    fits = bool(pack.get("fits", True))
    if fits:
        return SignalRecoveryResult(True, False, current_tier, chunks, pack)

    next_tier = min(current_tier + 1, max_auto_tier)
    escalated = next_tier > current_tier
    if escalated:
        chunks = retrieve_fn(query=query, tier=next_tier, k=12, expand=True, targeted=True)
        pack = pack_fn(chunks, next_tier)

    return SignalRecoveryResult(True, escalated, next_tier, chunks, pack)
