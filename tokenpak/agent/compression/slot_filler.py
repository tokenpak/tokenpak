"""Slot Filler — Intent parameterization via regex + keyword extraction.

Sits between IntentClassifier and RecipeEngine. Extracts entity, duration,
enum and other slot values from raw text using patterns defined in
slot_definitions.yaml.

No LLM, no external dependencies: stdlib + re only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# YAML loader (stdlib only — no PyYAML required in tests)
# ---------------------------------------------------------------------------
try:
    import yaml as _yaml  # type: ignore

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f)

except ImportError:  # pragma: no cover
    import json

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_PATTERNS = [
    (re.compile(r"\b(?:last|past)\s+(\d+)\s*days?\b", re.I), lambda m: f"{m.group(1)}d"),
    (re.compile(r"\b(?:last|past)\s+week\b", re.I), lambda _: "7d"),
    (re.compile(r"\b(?:last|past)\s+month\b", re.I), lambda _: "30d"),
    (re.compile(r"\b(\d+)\s*(weeks?)\b", re.I), lambda m: f"{m.group(1)} {m.group(2)}"),
    (re.compile(r"\b(\d+)\s*days?\b", re.I), lambda m: f"{m.group(1)}d"),
    (re.compile(r"\b(\d+)\s*months?\b", re.I), lambda m: f"{int(m.group(1)) * 30}d"),
    (re.compile(r"\btoday\b", re.I), lambda _: "1d"),
    (re.compile(r"\bthis\s+week\b", re.I), lambda _: "7d"),
    (re.compile(r"\b(\d+)d\b", re.I), lambda m: f"{m.group(1)}d"),
]


def _extract_duration(text: str) -> Optional[str]:
    for pat, fmt in _DURATION_PATTERNS:
        m = pat.search(text)
        if m:
            return fmt(m)
    return None


# ---------------------------------------------------------------------------
# Stop words for entity extraction
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    {
        "for",
        "last",
        "past",
        "in",
        "on",
        "at",
        "to",
        "from",
        "as",
        "with",
        "when",
        "before",
        "after",
        "and",
        "or",
        "but",
        "the",
        "a",
        "an",
        "while",
        "during",
        "of",
        "by",
        "about",
        "into",
        "through",
        "next",
        "this",
        "that",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "then",
        "than",
        "so",
        "if",
        "not",
        "no",
        "nor",
        "yet",
        "both",
        "either",
        "quarter",
        "year",
    }
)


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


def _extract_entity(text: str, examples: List[str]) -> Optional[str]:
    lowered = text.lower()

    for ex in sorted(examples, key=len, reverse=True):
        ex_lower = ex.lower()
        idx = lowered.find(ex_lower)
        if idx == -1:
            continue
        if idx > 0 and text[idx - 1] not in (" ", "\t", "\n", ",", ".", "(", "'", '"'):
            continue
        after_pos = idx + len(ex)
        if after_pos < len(text) and text[after_pos].isalpha():
            continue
        rest = text[after_pos:]
        for word in rest.split():
            clean = re.sub(r"[^\w\-/]", "", word).lower()
            if not clean or clean in _STOP_WORDS:
                break
            return (ex_lower + " " + re.sub(r"[^\w\-/]", "", word)).strip()
        return ex_lower

    prep_pat = re.compile(
        r"\b(?:of|in|with|debug|review|edit|check|fix|summarize|about)\s+"
        r"([a-zA-Z_\-/][a-zA-Z_\-/0-9]*)",
        re.I,
    )
    for m in prep_pat.finditer(text):
        candidate = m.group(1).lower()
        if candidate not in _STOP_WORDS:
            return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Enum extraction
# ---------------------------------------------------------------------------


def _extract_enum(text: str, values: List[str]) -> Optional[str]:
    lowered = text.lower()
    for v in values:
        if re.search(rf"\b{re.escape(v.lower())}\b", lowered):
            return v
    for v in values:
        root = v.lower().rstrip("ed")
        if len(root) >= 4 and re.search(rf"\b{re.escape(root)}\b", lowered):
            return v
    return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FilledSlots:
    intent: str
    slots: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# SlotFiller
# ---------------------------------------------------------------------------


class SlotFiller:
    """Extracts slot values from raw text for a given intent.

    Slot definitions are loaded from slot_definitions.yaml (co-located with
    this module).  All extraction is regex/keyword based — no LLM.

    Usage::

        filler = SlotFiller()
        result = filler.fill("summarize", "summarize the vault for last 7 days")
    """

    _DEFINITIONS_PATH = Path(__file__).with_name("slot_definitions.yaml")

    def __init__(self, definitions: Optional[Dict[str, Any]] = None) -> None:
        if definitions is not None:
            self._defs: Dict[str, Any] = definitions
        elif self._DEFINITIONS_PATH.exists():
            self._defs = _load_yaml(str(self._DEFINITIONS_PATH))
        else:
            self._defs = {}

    def fill(self, intent: str, text: str) -> FilledSlots:
        canonical = self._canonicalize(intent)
        intent_def = self._defs.get(canonical)
        if intent_def is None:
            return FilledSlots(intent=canonical, slots={}, missing=[], confidence=0.0)

        slots_schema: Dict[str, Any] = intent_def.get("slots", {})
        filled: Dict[str, Any] = {}
        missing: List[str] = []
        extracted_count = 0
        total_required = sum(1 for s in slots_schema.values() if s.get("required", False))

        for slot_name, slot_cfg in slots_schema.items():
            slot_type = slot_cfg.get("type", "str")
            required = slot_cfg.get("required", False)
            default = slot_cfg.get("default")
            value = None

            if slot_type == "duration":
                value = _extract_duration(text)
            elif slot_type == "enum":
                value = _extract_enum(text, slot_cfg.get("values", []))
            elif slot_type == "entity":
                value = _extract_entity(text, slot_cfg.get("examples", []))
            elif slot_type in ("int", "str"):
                examples = slot_cfg.get("examples", [])
                value = _extract_entity(text, examples) if examples else None

            if value is not None:
                filled[slot_name] = value
                extracted_count += 1
            elif default is not None:
                filled[slot_name] = default
            elif required:
                missing.append(slot_name)

        required_extracted = sum(
            1
            for sn, sc in slots_schema.items()
            if sc.get("required", False) and sn in filled and sn not in missing
        )
        confidence = (
            float(required_extracted) / total_required
            if total_required > 0
            else (1.0 if extracted_count > 0 else 0.5)
        )

        return FilledSlots(
            intent=canonical,
            slots=filled,
            missing=missing,
            confidence=round(confidence, 2),
        )

    @property
    def definitions(self) -> Dict[str, Any]:
        return self._defs

    def known_intents(self) -> List[str]:
        return list(self._defs.keys())

    @staticmethod
    def _canonicalize(intent: str) -> str:
        return intent.strip().lower().replace("-", "_")
