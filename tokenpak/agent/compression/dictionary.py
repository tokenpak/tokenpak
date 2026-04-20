"""
CompressionDictionary — Project-specific phrase replacement for TokenPak.

Loads a user-defined dictionary from ~/.tokenpak/compression_dict.json
and applies string replacements as a pre-compression pass.

Also tracks repeated long phrases across calls and surfaces suggestions
for new dictionary entries via :attr:`suggestions`.

Dictionary file format
----------------------
~/.tokenpak/compression_dict.json::

    {
        "environment variable configuration mismatch": "ENV_MISMATCH",
        "authentication token expired": "AUTH_EXPIRED",
        "connection refused": "CONN_REFUSED"
    }

Keys are phrases to replace; values are the short tokens to substitute.
Matching is case-sensitive by default (pass ``case_sensitive=False`` to
the constructor for case-insensitive replacement).

Auto-learn
----------
Every :meth:`apply` call accumulates phrase frequencies from the supplied
messages.  After the configured threshold is reached (default 3 occurrences,
min phrase length 30 chars), the phrase appears in :attr:`suggestions` and
can be exported via :meth:`suggest_entries`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_DICT_PATH = Path.home() / ".tokenpak" / "compression_dict.json"

# Minimum phrase length to be tracked for auto-learn
_MIN_PHRASE_LEN = 30
# Word boundary for phrase extraction (5+ word runs, up to 20 words)
_RE_PHRASE = re.compile(r"(?<!\w)(?:\w+(?:[  \t]+\w+){4,19})(?!\w)")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DictionaryResult:
    """Output of a single :meth:`CompressionDictionary.apply` call."""

    messages: List[Dict[str, Any]]
    """Messages with dictionary replacements applied."""

    replacements_made: int
    """Total number of substitutions performed across all messages."""

    tokens_saved_est: int
    """Estimated token savings (chars_removed // 4, rough heuristic)."""

    applied_entries: Dict[str, str] = field(default_factory=dict)
    """Mapping of phrase → token for entries that were actually triggered."""


@dataclass
class SuggestedEntry:
    """A phrase the auto-learner thinks should be added to the dictionary."""

    phrase: str
    occurrences: int
    suggested_token: str

    def as_dict(self) -> Dict[str, str]:
        return {self.phrase: self.suggested_token}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class CompressionDictionary:
    """
    Project-specific phrase → token replacement pass.

    Parameters
    ----------
    dict_path:
        Path to the JSON dictionary file.  Defaults to
        ``~/.tokenpak/compression_dict.json``.
    case_sensitive:
        Whether phrase matching is case-sensitive (default ``True``).
    auto_learn_threshold:
        Minimum occurrences before a phrase appears in :attr:`suggestions`.
    auto_learn_min_length:
        Minimum character length of a phrase to track for auto-learn.
    """

    def __init__(
        self,
        dict_path: Optional[Path | str] = None,
        *,
        case_sensitive: bool = True,
        auto_learn_threshold: int = 3,
        auto_learn_min_length: int = _MIN_PHRASE_LEN,
    ) -> None:
        self._dict_path = Path(dict_path) if dict_path else _DEFAULT_DICT_PATH
        self._case_sensitive = case_sensitive
        self._auto_learn_threshold = auto_learn_threshold
        self._auto_learn_min_length = auto_learn_min_length

        self._dictionary: Dict[str, str] = {}
        self._phrase_counter: Counter = Counter()

        self._load()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def dictionary(self) -> Dict[str, str]:
        """Current phrase → token mapping (read-only copy)."""
        return dict(self._dictionary)

    @property
    def suggestions(self) -> List[SuggestedEntry]:
        """
        Phrases that have been seen at least ``auto_learn_threshold`` times
        and are not already in the dictionary.
        """
        results: List[SuggestedEntry] = []
        for phrase, count in self._phrase_counter.most_common():
            if count < self._auto_learn_threshold:
                break
            if phrase in self._dictionary:
                continue
            if (not self._case_sensitive) and phrase.lower() in {
                k.lower() for k in self._dictionary
            }:
                continue
            token = self._make_token(phrase)
            results.append(SuggestedEntry(phrase=phrase, occurrences=count, suggested_token=token))
        return results

    def reload(self) -> None:
        """Re-read the dictionary file from disk."""
        self._load()

    def apply(self, messages: List[Dict[str, Any]]) -> DictionaryResult:
        """
        Apply dictionary replacements to a list of messages.

        Each message must have at least a ``"content"`` key (str).
        System / user / assistant messages are all processed.

        Parameters
        ----------
        messages:
            List of message dicts (OpenAI chat format).

        Returns
        -------
        DictionaryResult
            Modified messages plus replacement statistics.
        """
        if not self._dictionary:
            # Still run auto-learn even if no replacements to make
            for msg in messages:
                content = msg.get("content")
                if isinstance(content, str):
                    self._learn(content)
            return DictionaryResult(
                messages=messages,
                replacements_made=0,
                tokens_saved_est=0,
            )

        new_messages: List[Dict[str, Any]] = []
        total_replacements = 0
        total_chars_saved = 0
        applied: Dict[str, str] = {}

        # Build sorted list so longer phrases take priority
        sorted_entries = sorted(self._dictionary.items(), key=lambda kv: len(kv[0]), reverse=True)

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str):
                new_messages.append(msg)
                continue

            new_content = content
            for phrase, token in sorted_entries:
                if self._case_sensitive:
                    count = new_content.count(phrase)
                    if count:
                        new_content = new_content.replace(phrase, token)
                        total_replacements += count
                        total_chars_saved += (len(phrase) - len(token)) * count
                        applied[phrase] = token
                else:
                    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                    count = len(pattern.findall(new_content))
                    if count:
                        new_content = pattern.sub(token, new_content)
                        total_replacements += count
                        total_chars_saved += (len(phrase) - len(token)) * count
                        applied[phrase] = token

            new_msg = dict(msg)
            new_msg["content"] = new_content
            new_messages.append(new_msg)

            # Auto-learn from *original* content
            self._learn(content)

        tokens_saved_est = max(0, total_chars_saved // 4)
        return DictionaryResult(
            messages=new_messages,
            replacements_made=total_replacements,
            tokens_saved_est=tokens_saved_est,
            applied_entries=applied,
        )

    def suggest_entries(self) -> List[Dict[str, str]]:
        """
        Return a list of ``{phrase: suggested_token}`` dicts for auto-learned
        suggestions.  Suitable for merging directly into the dictionary file.
        """
        return [s.as_dict() for s in self.suggestions]

    def save_suggestions(self, min_occurrences: int = 1) -> int:
        """
        Append accepted suggestions to the dictionary file on disk.

        Only suggestions with ``occurrences >= min_occurrences`` are written.
        Existing entries are preserved; duplicates are skipped.

        Returns the number of new entries added.
        """
        candidates = [s for s in self.suggestions if s.occurrences >= min_occurrences]
        if not candidates:
            return 0

        current = self._load_raw()
        added = 0
        for suggestion in candidates:
            if suggestion.phrase not in current:
                current[suggestion.phrase] = suggestion.suggested_token
                self._dictionary[suggestion.phrase] = suggestion.suggested_token
                added += 1

        if added:
            self._dict_path.parent.mkdir(parents=True, exist_ok=True)
            self._dict_path.write_text(json.dumps(current, indent=2, ensure_ascii=False))

        return added

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._dictionary = self._load_raw()

    def _load_raw(self) -> Dict[str, str]:
        if not self._dict_path.exists():
            return {}
        try:
            data = json.loads(self._dict_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _learn(self, text: str) -> None:
        """Extract multi-word phrases from text and update the frequency counter."""
        for match in _RE_PHRASE.finditer(text):
            phrase = match.group(0).strip()
            if len(phrase) >= self._auto_learn_min_length:
                self._phrase_counter[phrase] += 1

    def _make_token(self, phrase: str) -> str:
        """
        Generate a suggested short token from a phrase.

        Strategy: take the first letter of each significant word (skipping
        short stop-words), uppercase, and join with underscores.
        E.g. "environment variable configuration mismatch" → "ENV_VAR_CFG_MISMATCH"
        """
        _STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is", "be"}
        words = phrase.split()
        significant = [w for w in words if w.lower() not in _STOP]
        if not significant:
            significant = words
        # Take up to 4 significant words, abbreviated to 3 chars
        abbrevs = [w[:3].upper() for w in significant[:4]]
        return "_".join(abbrevs)
