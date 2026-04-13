"""Unit tests for extraction/models.py — dataclasses and EntitySet."""

import pytest

from extraction.models import (
    APIEndpoint,
    Deadline,
    Decision,
    Entity,
    EntitySet,
    EntityType,
    GlossaryTerm,
    SourceRef,
)


# ---------------------------------------------------------------------------
# EntityType
# ---------------------------------------------------------------------------


class TestEntityType:
    def test_all_values_are_strings(self):
        for member in EntityType:
            assert isinstance(member.value, str)

    def test_expected_members_exist(self):
        expected = {
            "PERSON", "ORGANIZATION", "API_ENDPOINT", "DECISION",
            "DEADLINE", "GLOSSARY_TERM", "CONFIG_KEY", "FILE_PATH",
        }
        assert {m.name for m in EntityType} == expected

    def test_str_subclass(self):
        assert isinstance(EntityType.PERSON, str)
        assert EntityType.PERSON == "person"


# ---------------------------------------------------------------------------
# SourceRef
# ---------------------------------------------------------------------------


class TestSourceRef:
    def test_basic_construction(self):
        ref = SourceRef(line=1, column=0, snippet="hello world")
        assert ref.line == 1
        assert ref.column == 0
        assert ref.snippet == "hello world"

    def test_slots_prevents_new_attributes(self):
        ref = SourceRef(line=1, column=0, snippet="x")
        with pytest.raises(AttributeError):
            ref.nonexistent = "value"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class TestEntity:
    def _ref(self):
        return SourceRef(line=1, column=0, snippet="snippet")

    def test_basic_construction(self):
        e = Entity(type=EntityType.PERSON, value="Alice Smith", source=self._ref())
        assert e.type == EntityType.PERSON
        assert e.value == "Alice Smith"
        assert e.confidence == 1.0

    def test_custom_confidence(self):
        e = Entity(type=EntityType.DECISION, value="use postgres", source=self._ref(), confidence=0.9)
        assert e.confidence == 0.9

    def test_slots_prevents_new_attributes(self):
        e = Entity(type=EntityType.FILE_PATH, value="/tmp", source=self._ref())
        with pytest.raises(AttributeError):
            e.extra = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class TestDecision:
    def test_basic_construction(self):
        ref = SourceRef(line=3, column=0, snippet="decision: use postgres")
        d = Decision(text="use postgres", source=ref)
        assert d.text == "use postgres"
        assert d.source.line == 3


# ---------------------------------------------------------------------------
# Deadline
# ---------------------------------------------------------------------------


class TestDeadline:
    def test_with_normalized(self):
        ref = SourceRef(line=1, column=0, snippet="Due 2026-04-12")
        dl = Deadline(text="2026-04-12", normalized="2026-04-12", source=ref)
        assert dl.normalized == "2026-04-12"

    def test_normalized_can_be_none(self):
        ref = SourceRef(line=1, column=0, snippet="someday")
        dl = Deadline(text="someday", normalized=None, source=ref)
        assert dl.normalized is None


# ---------------------------------------------------------------------------
# APIEndpoint
# ---------------------------------------------------------------------------


class TestAPIEndpoint:
    def test_with_method(self):
        ref = SourceRef(line=1, column=0, snippet="GET /api/v1/users")
        ep = APIEndpoint(method="GET", path="/api/v1/users", source=ref)
        assert ep.method == "GET"
        assert ep.path == "/api/v1/users"

    def test_method_none(self):
        ref = SourceRef(line=1, column=0, snippet="/api/v1/users")
        ep = APIEndpoint(method=None, path="/api/v1/users", source=ref)
        assert ep.method is None


# ---------------------------------------------------------------------------
# GlossaryTerm
# ---------------------------------------------------------------------------


class TestGlossaryTerm:
    def test_with_definition(self):
        ref = SourceRef(line=1, column=0, snippet="term: TokenPak")
        g = GlossaryTerm(term="TokenPak", definition="a token packing tool", source=ref)
        assert g.term == "TokenPak"
        assert g.definition == "a token packing tool"

    def test_definition_none(self):
        ref = SourceRef(line=1, column=0, snippet="term: TokenPak")
        g = GlossaryTerm(term="TokenPak", definition=None, source=ref)
        assert g.definition is None


# ---------------------------------------------------------------------------
# EntitySet
# ---------------------------------------------------------------------------


def _ref(line=1):
    return SourceRef(line=line, column=0, snippet="test")


class TestEntitySetInit:
    def test_empty_by_default(self):
        es = EntitySet()
        assert es.entities == []
        assert es.decisions == []
        assert es.deadlines == []
        assert es.api_endpoints == []
        assert es.glossary_terms == []

    def test_independent_default_lists(self):
        """Verify field(default_factory=list) gives independent lists."""
        a = EntitySet()
        b = EntitySet()
        a.entities.append(Entity(type=EntityType.PERSON, value="x", source=_ref()))
        assert b.entities == []


class TestEntitySetByType:
    def test_filter_by_type(self):
        es = EntitySet()
        es.entities = [
            Entity(type=EntityType.PERSON, value="Alice Smith", source=_ref()),
            Entity(type=EntityType.FILE_PATH, value="/tmp/foo", source=_ref()),
            Entity(type=EntityType.PERSON, value="Bob Jones", source=_ref()),
        ]
        people = es.by_type(EntityType.PERSON)
        assert len(people) == 2
        assert all(e.type == EntityType.PERSON for e in people)

    def test_returns_empty_when_no_match(self):
        es = EntitySet()
        es.entities = [
            Entity(type=EntityType.FILE_PATH, value="/tmp/foo", source=_ref()),
        ]
        assert es.by_type(EntityType.PERSON) == []

    def test_empty_entity_set(self):
        es = EntitySet()
        assert es.by_type(EntityType.DECISION) == []


class TestEntitySetToCompactDict:
    def test_empty_entity_set_produces_empty_lists(self):
        es = EntitySet()
        d = es.to_compact_dict()
        assert d["people"] == []
        assert d["organizations"] == []
        assert d["config_keys"] == []
        assert d["file_paths"] == []
        assert d["api_endpoints"] == []
        assert d["decisions"] == []
        assert d["deadlines"] == []
        assert d["glossary"] == []

    def test_people_deduplicated_and_sorted(self):
        es = EntitySet()
        es.entities = [
            Entity(type=EntityType.PERSON, value="Bob Jones", source=_ref()),
            Entity(type=EntityType.PERSON, value="Alice Smith", source=_ref()),
            Entity(type=EntityType.PERSON, value="Alice Smith", source=_ref()),
        ]
        d = es.to_compact_dict()
        assert d["people"] == ["Alice Smith", "Bob Jones"]

    def test_api_endpoints_with_method(self):
        ref = _ref()
        es = EntitySet()
        es.api_endpoints = [
            APIEndpoint(method="GET", path="/api/v1/users", source=ref),
            APIEndpoint(method=None, path="/api/v2/items", source=ref),
        ]
        d = es.to_compact_dict()
        assert "GET /api/v1/users" in d["api_endpoints"]
        assert "/api/v2/items" in d["api_endpoints"]

    def test_decisions_ordered_as_encountered(self):
        es = EntitySet()
        es.decisions = [
            Decision(text="use postgres", source=_ref(1)),
            Decision(text="deploy on friday", source=_ref(2)),
        ]
        d = es.to_compact_dict()
        assert d["decisions"] == ["use postgres", "deploy on friday"]

    def test_deadlines_prefer_normalized(self):
        es = EntitySet()
        es.deadlines = [
            Deadline(text="Apr 12, 2026", normalized="2026-04-12", source=_ref()),
            Deadline(text="unknown date", normalized=None, source=_ref()),
        ]
        d = es.to_compact_dict()
        assert "2026-04-12" in d["deadlines"]
        assert "unknown date" in d["deadlines"]

    def test_glossary_sorted(self):
        es = EntitySet()
        es.glossary_terms = [
            GlossaryTerm(term="Zeta", definition=None, source=_ref()),
            GlossaryTerm(term="Alpha", definition=None, source=_ref()),
        ]
        d = es.to_compact_dict()
        assert d["glossary"] == ["Alpha", "Zeta"]

    def test_config_keys_sorted(self):
        es = EntitySet()
        es.entities = [
            Entity(type=EntityType.CONFIG_KEY, value="ZEBRA_FLAG", source=_ref()),
            Entity(type=EntityType.CONFIG_KEY, value="ALPHA_FLAG", source=_ref()),
        ]
        d = es.to_compact_dict()
        assert d["config_keys"] == ["ALPHA_FLAG", "ZEBRA_FLAG"]
