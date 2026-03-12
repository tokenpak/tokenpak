"""
SemanticMapLoader — Load and validate semantic_map.yaml.

Validates:
  - Required keys (version, intents, entities)
  - Each canonical key has an 'aliases' list of strings
  - No alias appears under more than one canonical key (conflict detection)
  - No duplicate aliases within the same canonical key
  - All keys and aliases are lowercase strings

Raises SemanticMapError with a descriptive message on any violation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# YAML loader (stdlib fallback to json for test environments)
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
# Default path
# ---------------------------------------------------------------------------
_DEFAULT_MAP_PATH = Path(__file__).parent.parent / "config" / "semantic_map.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class SemanticMapError(Exception):
    """Raised when the semantic map fails validation."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CanonicalEntry:
    """Represents one canonical key (intent or entity) with its aliases."""
    canonical: str
    description: str
    aliases: List[str]
    # For intents: optional slot_hints
    slot_hints: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class SemanticMap:
    """Parsed and validated semantic map."""
    version: str
    intents: Dict[str, CanonicalEntry]   # canonical_key → entry
    entities: Dict[str, CanonicalEntry]  # canonical_key → entry
    # Flat alias → canonical lookup tables (built during validation)
    intent_alias_index: Dict[str, str] = field(default_factory=dict)
    entity_alias_index: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
class SemanticMapLoader:
    """
    Load, validate, and expose the semantic map.

    Args:
        path: Path to semantic_map.yaml. Defaults to the bundled config.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or str(_DEFAULT_MAP_PATH)
        self._map: Optional[SemanticMap] = None

    def load(self) -> SemanticMap:
        """Load and validate the semantic map. Cached after first call."""
        if self._map is not None:
            return self._map
        raw = _load_yaml(self._path)
        self._map = self._validate(raw)
        return self._map

    def reload(self) -> SemanticMap:
        """Force reload from disk."""
        self._map = None
        return self.load()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self, raw: dict) -> SemanticMap:
        if not isinstance(raw, dict):
            raise SemanticMapError("semantic_map.yaml must be a YAML mapping at root level")

        version = str(raw.get("version", "unknown"))

        # Validate intents section
        intents_raw = raw.get("intents", {})
        if not isinstance(intents_raw, dict):
            raise SemanticMapError("'intents' must be a mapping of canonical_key → entry")

        entities_raw = raw.get("entities", {})
        if not isinstance(entities_raw, dict):
            raise SemanticMapError("'entities' must be a mapping of canonical_key → entry")

        intents, intent_index = self._parse_section(intents_raw, section_name="intents")
        entities, entity_index = self._parse_section(entities_raw, section_name="entities")

        return SemanticMap(
            version=version,
            intents=intents,
            entities=entities,
            intent_alias_index=intent_index,
            entity_alias_index=entity_index,
        )

    def _parse_section(
        self,
        section: dict,
        section_name: str,
    ) -> tuple[Dict[str, CanonicalEntry], Dict[str, str]]:
        """Parse one section (intents or entities) and build alias index."""
        entries: Dict[str, CanonicalEntry] = {}
        alias_index: Dict[str, str] = {}  # normalized_alias → canonical_key

        for canonical_key, entry_raw in section.items():
            canonical_key = str(canonical_key).strip().lower()
            if not canonical_key:
                raise SemanticMapError(f"[{section_name}] Empty canonical key found")

            if not re.match(r'^[a-z0-9_]+$', canonical_key):
                raise SemanticMapError(
                    f"[{section_name}] Canonical key '{canonical_key}' must be lowercase "
                    f"alphanumeric/underscore only"
                )

            if not isinstance(entry_raw, dict):
                raise SemanticMapError(
                    f"[{section_name}] Entry for '{canonical_key}' must be a mapping"
                )

            description = str(entry_raw.get("description", ""))
            aliases_raw = entry_raw.get("aliases", [])

            if not isinstance(aliases_raw, list):
                raise SemanticMapError(
                    f"[{section_name}/{canonical_key}] 'aliases' must be a list"
                )

            # Validate and normalize aliases
            aliases: List[str] = []
            seen_in_entry: Set[str] = set()

            for alias in aliases_raw:
                alias_norm = str(alias).strip().lower()
                if not alias_norm:
                    raise SemanticMapError(
                        f"[{section_name}/{canonical_key}] Empty alias found"
                    )

                # Duplicate within same entry
                if alias_norm in seen_in_entry:
                    raise SemanticMapError(
                        f"[{section_name}/{canonical_key}] Duplicate alias: '{alias_norm}'"
                    )
                seen_in_entry.add(alias_norm)

                # Conflict: same alias in another canonical key
                if alias_norm in alias_index:
                    conflict_key = alias_index[alias_norm]
                    raise SemanticMapError(
                        f"[{section_name}] Alias conflict: '{alias_norm}' appears under "
                        f"both '{conflict_key}' and '{canonical_key}'"
                    )

                alias_index[alias_norm] = canonical_key
                aliases.append(alias_norm)

            # Parse slot_hints (intents only, optional)
            slot_hints_raw = entry_raw.get("slot_hints", {})
            slot_hints: Dict[str, Dict[str, str]] = {}
            if isinstance(slot_hints_raw, dict):
                for slot_name, hint_map in slot_hints_raw.items():
                    if isinstance(hint_map, dict):
                        slot_hints[str(slot_name)] = {
                            str(k).lower(): str(v).lower()
                            for k, v in hint_map.items()
                        }

            entries[canonical_key] = CanonicalEntry(
                canonical=canonical_key,
                description=description,
                aliases=aliases,
                slot_hints=slot_hints,
            )

        return entries, alias_index
