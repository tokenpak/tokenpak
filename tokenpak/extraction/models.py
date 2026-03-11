"""Deterministic extraction models for pre-LLM distillation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EntityType(str, Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    API_ENDPOINT = "api_endpoint"
    DECISION = "decision"
    DEADLINE = "deadline"
    GLOSSARY_TERM = "glossary_term"
    CONFIG_KEY = "config_key"
    FILE_PATH = "file_path"


@dataclass(slots=True)
class SourceRef:
    line: int
    column: int
    snippet: str


@dataclass(slots=True)
class Entity:
    type: EntityType
    value: str
    source: SourceRef
    confidence: float = 1.0


@dataclass(slots=True)
class Decision:
    text: str
    source: SourceRef


@dataclass(slots=True)
class Deadline:
    text: str
    normalized: str | None
    source: SourceRef


@dataclass(slots=True)
class APIEndpoint:
    method: str | None
    path: str
    source: SourceRef


@dataclass(slots=True)
class GlossaryTerm:
    term: str
    definition: str | None
    source: SourceRef


@dataclass(slots=True)
class EntitySet:
    entities: list[Entity] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    api_endpoints: list[APIEndpoint] = field(default_factory=list)
    glossary_terms: list[GlossaryTerm] = field(default_factory=list)

    def by_type(self, entity_type: EntityType) -> list[Entity]:
        return [e for e in self.entities if e.type == entity_type]

    def to_compact_dict(self) -> dict:
        def _ent(t: EntityType) -> list[str]:
            return sorted({e.value for e in self.by_type(t)})

        return {
            "people": _ent(EntityType.PERSON),
            "organizations": _ent(EntityType.ORGANIZATION),
            "config_keys": _ent(EntityType.CONFIG_KEY),
            "file_paths": _ent(EntityType.FILE_PATH),
            "api_endpoints": sorted(
                {f"{a.method} {a.path}".strip() if a.method else a.path for a in self.api_endpoints}
            ),
            "decisions": [d.text for d in self.decisions],
            "deadlines": [d.normalized or d.text for d in self.deadlines],
            "glossary": sorted({g.term for g in self.glossary_terms}),
        }
