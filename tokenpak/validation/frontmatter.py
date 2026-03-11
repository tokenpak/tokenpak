"""Frontmatter parsing and canonicalization helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class _DuplicateKeyLoader(yaml.SafeLoader):
    """YAML loader that records duplicate mapping keys."""


@dataclass
class FrontmatterDiagnostics:
    """Structured parsing diagnostics for frontmatter."""

    mode: str = "lenient"
    duplicate_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    normalized_fields: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.duplicate_keys or self.warnings or self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "duplicate_keys": self.duplicate_keys,
            "warnings": self.warnings,
            "errors": self.errors,
            "normalized_fields": self.normalized_fields,
        }


def _construct_mapping(loader: _DuplicateKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[str, Any] = {}
    duplicates = getattr(loader, "_duplicate_keys", None)

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        value = loader.construct_object(value_node, deep=deep)
        if key in mapping and isinstance(duplicates, list):
            duplicates.append(str(key))
        mapping[key] = value

    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _normalize_assigned_to(data: dict[str, Any], diagnostics: FrontmatterDiagnostics) -> None:
    if "assigned_to" not in data:
        return

    value = data["assigned_to"]
    normalized: list[str] | None = None

    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        if "," in value:
            normalized = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
        elif "assigned_to" in diagnostics.duplicate_keys:
            normalized = [value.strip()] if value.strip() else []

    if normalized is not None:
        data["assigned_to"] = normalized
        diagnostics.normalized_fields.append("assigned_to")


def parse_frontmatter(yaml_block: str, strict: bool = False) -> tuple[dict[str, Any], FrontmatterDiagnostics]:
    """Parse and canonicalize a YAML frontmatter block."""
    diagnostics = FrontmatterDiagnostics(mode="strict" if strict else "lenient")

    loader = _DuplicateKeyLoader(yaml_block)
    loader._duplicate_keys = []  # type: ignore[attr-defined]

    try:
        parsed = loader.get_single_data() or {}
    except yaml.YAMLError as exc:
        diagnostics.errors.append(f"Malformed YAML frontmatter: {exc}")
        if strict:
            raise ValueError(diagnostics.errors[-1]) from exc
        logger.warning(diagnostics.errors[-1])
        return {}, diagnostics
    finally:
        loader.dispose()

    if not isinstance(parsed, dict):
        diagnostics.errors.append("Frontmatter must parse to a mapping/object")
        if strict:
            raise ValueError(diagnostics.errors[-1])
        logger.warning(diagnostics.errors[-1])
        return {}, diagnostics

    duplicate_keys = sorted(set(getattr(loader, "_duplicate_keys", [])))
    if duplicate_keys:
        diagnostics.duplicate_keys.extend(duplicate_keys)
        msg = f"Duplicate frontmatter keys detected: {', '.join(duplicate_keys)}"
        diagnostics.warnings.append(msg)
        if strict:
            diagnostics.errors.append(msg)
            raise ValueError(msg)
        logger.warning(msg)

    _normalize_assigned_to(parsed, diagnostics)

    canonical = {key: parsed[key] for key in sorted(parsed.keys(), key=str)}
    return canonical, diagnostics
