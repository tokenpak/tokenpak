# SPDX-License-Identifier: Apache-2.0
"""Rule-based intent classifier for Intent Layer Phase 0.

Maps a raw prompt string to one of the 10 canonical intents declared
in :mod:`tokenpak.proxy.intent_policy` (the existing policy table).
Deterministic; no LLM cost.

Phase 0 scope (telemetry-only): the classifier output drives a TIP
intent contract that flows into local telemetry and — when the
resolved request adapter declares ``tip.intent.contract-headers-v1``
per Standard #23 §4.3 — onto the wire as ``X-TokenPak-Intent-*`` /
``X-TokenPak-Contract-*`` headers. Out of Phase 0 scope: any prompt
reshaping, routing change, or clarification gate.

Algorithm
---------

1. **Empty / too-short fast-fail**: prompt below 3 non-whitespace
   chars → ``query`` with ``catch_all_reason = "prompt_too_short"``
   (or ``empty_prompt`` if zero chars).
2. **Keyword scoring**: each canonical intent has a weighted keyword
   set in :data:`_KEYWORD_TABLE`. Score for an intent is the **max**
   matched weight (not the sum) — a single strong canonical keyword
   classifies confidently without requiring secondary keywords too.
   Secondary patterns provide alternative paths to the same intent
   when the canonical word is absent. Confidence is the top
   intent's score, clamped to ``[0.0, 1.0]``.
3. **Threshold gate**: if top confidence is below
   :data:`CLASSIFY_THRESHOLD` (0.4 by default — same as the existing
   ``intent_policy.CONFIDENCE_THRESHOLD`` for symmetry), fall back to
   ``query`` with ``catch_all_reason = "confidence_below_threshold"``.
4. **Tie-breaker**: when two intents tie on score, the one earlier
   in :data:`_KEYWORD_TABLE` declaration wins (priority encoded in
   ordering: status > usage > debug > … > query). ``query`` always
   loses ties since it's the catch-all.
5. **Slot fill** is delegated to :class:`SlotFiller` (existing
   keyword/regex-driven extractor in
   ``tokenpak/agent/compression/slot_filler.py``).

Catch-all reasons (matches the proposal §5.3 enum)
--------------------------------------------------

- ``empty_prompt`` — prompt was empty or whitespace-only.
- ``prompt_too_short`` — prompt below 3 chars after strip.
- ``keyword_miss`` — every intent scored 0.
- ``confidence_below_threshold`` — top score under
  :data:`CLASSIFY_THRESHOLD`.
- ``slot_ambiguous`` — top intent inferred but a required slot has
  multiple candidate values (reserved; not emitted in v0 since
  SlotFiller picks the first match deterministically).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

from tokenpak.agent.compression.slot_filler import SlotFiller
from tokenpak.proxy.intent_policy import CANONICAL_INTENTS

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


CLASSIFY_THRESHOLD: float = 0.4
"""Minimum normalized keyword score for a non-catch-all classification.

Symmetric with :data:`tokenpak.proxy.intent_policy.CONFIDENCE_THRESHOLD`
so an upstream classification that survives this gate also survives
the downstream policy gate.
"""

INTENT_SOURCE_V0: str = "rule_based_v0"
"""Identifier emitted on every Phase 0 telemetry row.

Lets a future Intent-1 LLM-assisted classifier (Option B in the
proposal) backfill its own rows under a different ``intent_source``
without invalidating Phase 0 baselines.
"""


CATCH_ALL_REASONS: FrozenSet[str] = frozenset({
    "empty_prompt",
    "prompt_too_short",
    "keyword_miss",
    "confidence_below_threshold",
    "slot_ambiguous",
})


@dataclass(frozen=True)
class IntentClassification:
    """Output of :func:`classify_intent` — fully populated."""

    intent_class: str
    confidence: float
    slots_present: Tuple[str, ...]
    slots_missing: Tuple[str, ...]
    catch_all_reason: Optional[str]
    intent_source: str = INTENT_SOURCE_V0


# ---------------------------------------------------------------------------
# Keyword table (priority-ordered; query last as the catch-all)
# ---------------------------------------------------------------------------

# Maps intent → list of (regex, weight). Regexes are compiled once at
# module load. Weight = how confident a single match is that the
# prompt expresses this intent. Score for an intent =
# ``max(weights of patterns that matched)`` (or 0.0 if none matched).
#
# Priority order matters for ties — earlier intents win. ``query`` is
# never ranked above another intent in a tie because its keyword set
# is intentionally weak (broad words).
_KEYWORD_PATTERNS: Dict[str, List[Tuple[str, float]]] = {
    "status": [
        (r"\bstatus\b", 1.0),
        (r"\bhealth\b", 0.8),
        (r"\b(is|are)\s+(it|they|the)\s+\w+\s+(running|up|alive|ok)\b", 0.9),
        (r"\bcheck\b", 0.4),
    ],
    "usage": [
        (r"\busage\b", 1.0),
        (r"\b(token|cost|spend|spent|budget)\b", 0.8),
        (r"\bhow\s+(much|many)\b", 0.5),
        (r"\b(this|last)\s+(week|month|day|hour)\b", 0.4),
    ],
    "debug": [
        (r"\bdebug\b", 1.0),
        (r"\b(trace|diagnose|why|broken|error|stack)\b", 0.7),
        (r"\b(failing|fails|crashed|hung)\b", 0.7),
    ],
    "summarize": [
        (r"\bsummari[sz]e\b", 1.0),
        (r"\b(tl;dr|recap|brief|overview)\b", 0.7),
        (r"\bgive\s+me\s+(a|the)?\s*summary\b", 1.0),
    ],
    "plan": [
        (r"\bplan\b", 1.0),
        (r"\b(roadmap|outline|steps|strategy)\b", 0.7),
        (r"\bhow\s+(should|do)\s+(i|we)\s+(approach|tackle|do)\b", 0.6),
    ],
    "execute": [
        (r"\b(run|execute|kick\s*off|launch)\b", 0.9),
        (r"\bdry[\s-]*run\b", 1.0),
        (r"\bgo\s+(ahead|do\s+it)\b", 0.6),
    ],
    "explain": [
        (r"\bexplain\b", 1.0),
        (r"\b(what\s+does|how\s+does|why\s+does)\b", 0.6),
        (r"\bwalk\s+me\s+through\b", 0.8),
    ],
    "search": [
        (r"\b(search|find|look\s+up|grep)\b", 0.9),
        (r"\bwhere\s+is\b", 0.6),
        (r"\b(locate|hunt\s+for)\b", 0.7),
    ],
    "create": [
        (r"\b(create|generate|make|write|scaffold|new)\b", 0.7),
        (r"\b(add|build)\s+(a|an|the)\b", 0.6),
        (r"\b(implement|draft)\b", 0.7),
    ],
    "query": [
        # Catch-all keywords intentionally weak; this set rarely wins
        # outright. Used so a generic "what's …" or "how about …"
        # prompt still scores nonzero before falling back.
        (r"\bwhat'?s\b", 0.3),
        (r"\b(can|could|would)\s+you\b", 0.2),
        (r"\?$", 0.2),
    ],
}


# Compile once. Tuple shape: (compiled_regex, weight).
_COMPILED: Dict[str, List[Tuple[re.Pattern[str], float]]] = {
    intent: [(re.compile(pat, re.IGNORECASE), w) for pat, w in patterns]
    for intent, patterns in _KEYWORD_PATTERNS.items()
}

# Priority order for tie-breaking. Built from declaration order; query last.
_PRIORITY: Tuple[str, ...] = tuple(_KEYWORD_PATTERNS.keys())


# Sanity guard — the keyword table must cover the canonical-intent set
# from :data:`intent_policy.CANONICAL_INTENTS`. If a future intent is
# added to the policy without a keyword set, the classifier would
# silently never select it. The assertion fires at import time.
_KEYWORD_INTENTS = frozenset(_KEYWORD_PATTERNS.keys())
assert CANONICAL_INTENTS <= _KEYWORD_INTENTS, (
    f"intent_classifier missing keyword sets for: "
    f"{sorted(CANONICAL_INTENTS - _KEYWORD_INTENTS)}"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_intent(prompt: str, *, slot_filler: Optional[SlotFiller] = None) -> IntentClassification:
    """Classify a raw prompt into one of the canonical intents.

    Always returns a populated :class:`IntentClassification`; never
    raises on bad input. Prompts that fail every gate fall back to
    ``query`` with a populated ``catch_all_reason``.

    ``slot_filler`` is optional — pass a configured
    :class:`SlotFiller` to reuse its loaded definitions; otherwise a
    fresh instance is constructed (loads YAML on first call).
    """
    text = (prompt or "").strip()
    if not text:
        return _catch_all("empty_prompt")
    if len(text) < 3:
        return _catch_all("prompt_too_short")

    scores = _score_intents(text)
    top_intent, top_score = _pick_top(scores)
    if top_score == 0.0:
        return _catch_all("keyword_miss")
    if top_score < CLASSIFY_THRESHOLD:
        return _catch_all("confidence_below_threshold", confidence=top_score)

    filler = slot_filler if slot_filler is not None else SlotFiller()
    filled = filler.fill(top_intent, text)
    slots_present = tuple(sorted(filled.slots.keys()))
    slots_missing = tuple(sorted(filled.missing))

    return IntentClassification(
        intent_class=top_intent,
        confidence=round(top_score, 4),
        slots_present=slots_present,
        slots_missing=slots_missing,
        catch_all_reason=None,
    )


def extract_prompt_text(messages: object) -> str:
    """Best-effort prompt extraction for canonical-request messages.

    Concatenates the textual ``content`` of every user-role entry,
    space-separated, lowercased only for the matcher (we keep the
    original case in the return value so the slot filler can pick up
    proper-case entity examples).

    Accepts:
      - a list of ``{"role": ..., "content": ...}`` dicts
      - a string (returned as-is)
      - anything else (returns ``""``)
    """
    if isinstance(messages, str):
        return messages
    if not isinstance(messages, list):
        return ""
    parts: List[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") not in ("user", None, ""):
            continue
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _score_intents(text: str) -> Mapping[str, float]:
    out: Dict[str, float] = {}
    for intent, patterns in _COMPILED.items():
        best = 0.0
        for rgx, weight in patterns:
            if rgx.search(text) and weight > best:
                best = weight
        # Clamp — a misconfigured weight > 1.0 in the table would
        # otherwise inflate confidence beyond the proposal's [0, 1]
        # contract.
        out[intent] = min(best, 1.0)
    return out


def _pick_top(scores: Mapping[str, float]) -> Tuple[str, float]:
    # Iterate in priority order so ties are broken deterministically.
    best_intent = "query"
    best_score = 0.0
    for intent in _PRIORITY:
        s = scores.get(intent, 0.0)
        if s > best_score:
            best_intent = intent
            best_score = s
    return best_intent, best_score


def _catch_all(reason: str, *, confidence: float = 0.0) -> IntentClassification:
    return IntentClassification(
        intent_class="query",
        confidence=round(confidence, 4),
        slots_present=(),
        slots_missing=(),
        catch_all_reason=reason,
    )


__all__ = [
    "CATCH_ALL_REASONS",
    "CLASSIFY_THRESHOLD",
    "INTENT_SOURCE_V0",
    "IntentClassification",
    "classify_intent",
    "extract_prompt_text",
]
