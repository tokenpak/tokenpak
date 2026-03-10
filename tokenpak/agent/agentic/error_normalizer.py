from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Pattern, Set

def _default_pattern_path() -> Path:
    return Path.home() / ".tokenpak" / "error_patterns.json"


@dataclass(frozen=True)
class MergeSuggestion:
    recipe: str
    signatures: List[str]
    suggested_normalized: str


@dataclass
class FailureRecord:
    signature: str
    count: int = 0
    repair_recipes: Set[str] = field(default_factory=set)


class ErrorNormalizer:
    """Normalizes semantically-equivalent error strings to stable signatures."""

    def __init__(self, extra_pattern_path: Optional[Path] = None) -> None:
        self._patterns: List[tuple[Pattern[str], str]] = self._default_patterns()
        path = extra_pattern_path or _default_pattern_path()
        self._patterns.extend(self._load_external_patterns(path))

    def normalize(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return "UNKNOWN_ERROR"

        for pattern, normalized in self._patterns:
            if pattern.search(text):
                return normalized

        return self._fallback_signature(text)

    def suggest_merges_from_recipes(
        self,
        recipe_to_signatures: Mapping[str, Iterable[str]],
    ) -> List[MergeSuggestion]:
        suggestions: List[MergeSuggestion] = []
        for recipe, signatures in recipe_to_signatures.items():
            uniq = sorted({s for s in signatures if s})
            if len(uniq) < 2:
                continue
            suggestions.append(
                MergeSuggestion(
                    recipe=recipe,
                    signatures=uniq,
                    suggested_normalized=self.normalize(uniq[0]),
                )
            )
        return suggestions

    @staticmethod
    def _fallback_signature(raw: str) -> str:
        collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().upper())
        return re.sub(r"_+", "_", collapsed).strip("_")[:80] or "UNKNOWN_ERROR"

    @staticmethod
    def _default_patterns() -> List[tuple[Pattern[str], str]]:
        return [
            (re.compile(r"\bEADDRINUSE\b", re.IGNORECASE), "PORT_BIND_FAILURE"),
            (re.compile(r"address\s+already\s+in\s+use", re.IGNORECASE), "PORT_BIND_FAILURE"),
            (re.compile(r"bind\s+failed.*0\.0\.0\.0", re.IGNORECASE), "PORT_BIND_FAILURE"),
            (re.compile(r"port\s+unavailable", re.IGNORECASE), "PORT_BIND_FAILURE"),
            (re.compile(r"connection\s+refused", re.IGNORECASE), "CONNECTION_REFUSED"),
            (re.compile(r"(timed\s*out|timeout)", re.IGNORECASE), "TIMEOUT"),
            (re.compile(r"rate\s*limit|HTTP\s*429", re.IGNORECASE), "RATE_LIMIT"),
            (re.compile(r"HTTP\s*(401|403)|unauthorized|forbidden", re.IGNORECASE), "AUTH_FAILURE"),
        ]

    @staticmethod
    def _load_external_patterns(path: Path) -> List[tuple[Pattern[str], str]]:
        if not path.exists():
            return []

        try:
            payload = json.loads(path.read_text())
        except Exception:
            return []

        if not isinstance(payload, list):
            return []

        loaded: List[tuple[Pattern[str], str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            regex = item.get("regex")
            normalized = item.get("normalized_signature")
            if not isinstance(regex, str) or not isinstance(normalized, str):
                continue
            try:
                loaded.append((re.compile(regex, re.IGNORECASE), normalized.strip().upper()))
            except re.error:
                continue
        return loaded


class FailureSignatureDB:
    """In-memory signature DB with normalization-aware lookup/merge accounting."""

    def __init__(self, normalizer: Optional[ErrorNormalizer] = None) -> None:
        self.normalizer = normalizer or ErrorNormalizer()
        self.records: Dict[str, FailureRecord] = {}
        self.alias_to_normalized: Dict[str, str] = {}

    def lookup(self, raw_signature: str) -> Optional[FailureRecord]:
        normalized = self.normalizer.normalize(raw_signature)
        return self.records.get(normalized)

    def record_failure(self, raw_signature: str, repair_recipe: Optional[str] = None) -> FailureRecord:
        normalized = self.normalizer.normalize(raw_signature)
        record = self.records.setdefault(normalized, FailureRecord(signature=normalized))
        record.count += 1
        if repair_recipe:
            record.repair_recipes.add(repair_recipe)

        self.alias_to_normalized[raw_signature] = normalized
        return record

    def merge_synonym_stats(self, signatures: Iterable[str]) -> Optional[FailureRecord]:
        sigs = [s for s in signatures if s]
        if not sigs:
            return None

        canonical = self.normalizer.normalize(sigs[0])
        target = self.records.setdefault(canonical, FailureRecord(signature=canonical))

        for sig in sigs:
            normalized = self.normalizer.normalize(sig)
            self.alias_to_normalized[sig] = canonical
            if normalized == canonical:
                continue
            source = self.records.pop(normalized, None)
            if source:
                target.count += source.count
                target.repair_recipes.update(source.repair_recipes)
        return target

    def auto_learn_merge_suggestions(self) -> List[MergeSuggestion]:
        recipe_to_signatures: Dict[str, Set[str]] = {}
        for record in self.records.values():
            for recipe in record.repair_recipes:
                recipe_to_signatures.setdefault(recipe, set()).add(record.signature)
        return self.normalizer.suggest_merges_from_recipes(recipe_to_signatures)
