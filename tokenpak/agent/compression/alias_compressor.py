"""
AliasCompressor — Symbol Table / Alias Compression for TokenPak.

Scans message content for repeated long entities and replaces them with
short deterministic aliases (F1, F2 for files; S1, S2 for services;
U1 for URLs; C1 for class/function names; E1 for env vars).

A compact symbol table is prepended to the first message content:

    [ALIASES: F1=/path/to/pipeline.py | S1=openclaw-gateway]

Runs AFTER dedup, BEFORE final prompt assembly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Regex patterns for entity detection
# ---------------------------------------------------------------------------

_RE_FILE_PATH = re.compile(
    r"(?<!\w)"
    r"(?:[A-Za-z]:[\\/]|~?/)"       # absolute path start: /foo, ~/foo, C:\foo
    r"(?:[^\s,;\"'`<>\[\](){}|]+)"  # path body (no whitespace / delimiters)
)

_RE_URL = re.compile(
    r"https?://"
    r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)"
)

# Python/JS-style CamelCase class names or dotted module names (e.g. CompressionPipeline, my.module.Class)
_RE_CLASS_FUNC = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]{2,}(?:[A-Z][A-Za-z0-9]+)+|"  # CamelCase ≥2 humps
    r"[a-z][a-z0-9_]{4,}\.[a-z][a-z0-9_.]{4,})\b"         # dotted module path
)

# UPPER_SNAKE env vars that are likely multi-word
_RE_ENV_VAR = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+){1,}\b")

# Service names: lowercase-with-hyphens, ≥2 segments, ≥20 chars total
_RE_SERVICE = re.compile(r"\b[a-z][a-z0-9]{2,}(?:-[a-z0-9]+){2,}\b")


# Prefix letters for each entity type
_PREFIX = {
    "file": "F",
    "url": "U",
    "class": "C",
    "env": "E",
    "service": "S",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class AliasResult:
    """Result of an AliasCompressor.compress() call."""

    messages: List[Dict[str, Any]]
    symbol_table: Dict[str, str]        # alias → original
    tokens_saved: int
    entities_aliased: int
    alias_map: Dict[str, str] = field(default_factory=dict)  # original → alias


class AliasCompressor:
    """
    Entity-alias compressor.

    Parameters
    ----------
    min_occurrences:
        Minimum number of times an entity must appear to be aliased.
    min_entity_length:
        Minimum character length of an entity to be considered.
    entity_types:
        Which entity types to alias. Subset of
        ``["file", "url", "class", "env", "service"]``.
    """

    DEFAULT_ENTITY_TYPES = ["file", "url", "class", "env", "service"]

    def __init__(
        self,
        min_occurrences: int = 3,
        min_entity_length: int = 20,
        entity_types: Optional[List[str]] = None,
    ) -> None:
        self.min_occurrences = min_occurrences
        self.min_entity_length = min_entity_length
        self.entity_types = entity_types or self.DEFAULT_ENTITY_TYPES

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def compress(self, messages: List[Dict[str, Any]]) -> AliasResult:
        """
        Alias repeated entities in *messages*.

        Returns an :class:`AliasResult` with the modified messages and
        the generated symbol table.
        """
        # 1. Collect all text
        all_text = _collect_text(messages)

        # 2. Detect & count entities
        entity_counts: Counter[str] = Counter()
        entity_type_map: Dict[str, str] = {}

        for etype in self.entity_types:
            for match in self._pattern_for(etype).finditer(all_text):
                entity = match.group(0)
                if len(entity) >= self.min_entity_length:
                    entity_counts[entity] += 1
                    if entity not in entity_type_map:
                        entity_type_map[entity] = etype

        # 3. Filter to entities that meet occurrence threshold
        candidates = {
            e: t
            for e, t in entity_type_map.items()
            if entity_counts[e] >= self.min_occurrences
        }

        if not candidates:
            tokens_raw = _estimate_tokens(all_text)
            return AliasResult(
                messages=list(messages),
                symbol_table={},
                tokens_saved=0,
                entities_aliased=0,
            )

        # 4. Assign aliases (deterministic: sorted by type then entity)
        alias_map: Dict[str, str] = {}     # original → alias
        symbol_table: Dict[str, str] = {}  # alias → original
        counters: Dict[str, int] = {p: 1 for p in _PREFIX.values()}

        # Sort: by type bucket then by entity string for stability
        for entity in sorted(candidates, key=lambda e: (candidates[e], e)):
            prefix = _PREFIX[candidates[entity]]
            alias = f"{prefix}{counters[prefix]}"
            counters[prefix] += 1
            alias_map[entity] = alias
            symbol_table[alias] = entity

        # 5. Build symbol table header string
        header = _build_header(symbol_table)

        # 6. Apply replacements to all messages (longest entity first to avoid
        #    partial overlaps)
        new_messages = _replace_entities(messages, alias_map)

        # 7. Prepend symbol table to first message
        new_messages = _prepend_header(new_messages, header)

        # 8. Estimate token savings
        original_chars = sum(len(e) * entity_counts[e] for e in alias_map)
        aliased_chars = sum(
            len(alias_map[e]) * entity_counts[e] for e in alias_map
        )
        header_chars = len(header)
        saved_chars = original_chars - aliased_chars - header_chars
        tokens_saved = max(0, saved_chars // 4)

        return AliasResult(
            messages=new_messages,
            symbol_table=symbol_table,
            tokens_saved=tokens_saved,
            entities_aliased=len(alias_map),
            alias_map=alias_map,
        )

    def expand(self, text: str, symbol_table: Dict[str, str]) -> str:
        """
        Reverse alias compression: replace aliases with originals.

        Parameters
        ----------
        text:
            Compressed text containing aliases.
        symbol_table:
            Mapping of alias → original entity.
        """
        # Sort by alias length descending to avoid partial matches
        for alias in sorted(symbol_table, key=len, reverse=True):
            text = re.sub(r"\b" + re.escape(alias) + r"\b", symbol_table[alias], text)
        # Strip the header line if present
        text = re.sub(r"^\[ALIASES:[^\]]+\]\n?", "", text)
        return text

    def _pattern_for(self, etype: str) -> re.Pattern:
        return {
            "file": _RE_FILE_PATH,
            "url": _RE_URL,
            "class": _RE_CLASS_FUNC,
            "env": _RE_ENV_VAR,
            "service": _RE_SERVICE,
        }[etype]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_text(messages: List[Dict[str, Any]]) -> str:
    """Concatenate all text content from messages."""
    parts: List[str] = []
    for msg in messages:
        parts.append(_content_to_str(msg.get("content", "")))
    return "\n".join(parts)


def _content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                texts.append(block.get("text", "") if block.get("type") == "text" else "")
            else:
                texts.append(str(block))
        return "\n".join(texts)
    return ""


def _build_header(symbol_table: Dict[str, str]) -> str:
    """Format: [ALIASES: F1=/path/to/file | S1=my-service]"""
    entries = " | ".join(
        f"{alias}={original}"
        for alias, original in sorted(symbol_table.items())
    )
    return f"[ALIASES: {entries}]"


def _replace_entities(
    messages: List[Dict[str, Any]],
    alias_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Apply alias_map replacements to all message content (longest first)."""
    # Build a single regex for all entities, longest first
    sorted_entities = sorted(alias_map.keys(), key=len, reverse=True)
    if not sorted_entities:
        return messages

    pattern = re.compile(
        "|".join(re.escape(e) for e in sorted_entities)
    )

    def _replace_text(text: str) -> str:
        return pattern.sub(lambda m: alias_map[m.group(0)], text)

    result: List[Dict[str, Any]] = []
    for msg in messages:
        new_msg = dict(msg)
        content = msg.get("content", "")
        if isinstance(content, str):
            new_msg["content"] = _replace_text(content)
        elif isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    new_block = dict(block)
                    new_block["text"] = _replace_text(block.get("text", ""))
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            new_msg["content"] = new_blocks
        result.append(new_msg)
    return result


def _prepend_header(
    messages: List[Dict[str, Any]],
    header: str,
) -> List[Dict[str, Any]]:
    """Prepend the symbol table header to the first message."""
    if not messages:
        return messages

    new_messages = list(messages)
    first = dict(new_messages[0])
    content = first.get("content", "")

    if isinstance(content, str):
        first["content"] = header + "\n" + content
    elif isinstance(content, list):
        prepended = [{"type": "text", "text": header + "\n"}] + list(content)
        first["content"] = prepended
    else:
        first["content"] = header + "\n" + str(content)

    new_messages[0] = first
    return new_messages


def _estimate_tokens(text: str) -> int:
    return len(text) // 4
