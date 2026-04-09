"""Deterministic extraction pipeline for pre-LLM document distillation."""

from __future__ import annotations

import json
from datetime import datetime

from .models import (
    APIEndpoint,
    Deadline,
    Decision,
    Entity,
    EntitySet,
    EntityType,
    GlossaryTerm,
    SourceRef,
)
from .patterns import (
    API_ENDPOINT_RE,
    CONFIG_KEY_RE,
    DATE_RE,
    DECISION_RE,
    FENCED_CODE_RE,
    FILE_PATH_RE,
    GLOSSARY_RE,
    ORG_RE,
    PERSON_RE,
)


class EntityExtractor:
    """Pure regex/keyword/heuristic extractor with stable outputs."""

    def extract(self, text: str) -> EntitySet:
        clean = self._strip_code_blocks(text)
        entity_set = EntitySet()

        for line_no, line in enumerate(clean.splitlines(), start=1):
            self._extract_paths(line, line_no, entity_set)
            self._extract_api(line, line_no, entity_set)
            self._extract_dates(line, line_no, entity_set)
            self._extract_decisions(line, line_no, entity_set)
            self._extract_glossary(line, line_no, entity_set)
            self._extract_config(line, line_no, entity_set)
            self._extract_people_orgs(line, line_no, entity_set)

        return self._dedupe(entity_set)

    def compact_text(self, entity_set: EntitySet) -> str:
        """Compact structured injection format (intended to replace raw docs in context)."""
        compact = entity_set.to_compact_dict()
        return json.dumps(compact, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def choose_injection(raw_text: str, entity_set: EntitySet, prefer_compact: bool = True) -> str:
        """Return compact entities or raw text when compact mode is disabled."""
        if not prefer_compact:
            return raw_text
        return EntityExtractor().compact_text(entity_set)

    def _extract_paths(self, line: str, line_no: int, out: EntitySet) -> None:
        for m in FILE_PATH_RE.finditer(line):
            path = m.group("path")
            if len(path) > 2:
                out.entities.append(
                    self._entity(EntityType.FILE_PATH, path, line_no, m.start(), line)
                )

    def _extract_api(self, line: str, line_no: int, out: EntitySet) -> None:
        for m in API_ENDPOINT_RE.finditer(line):
            path = m.group("path")
            if path.count("/") < 1:
                continue
            method = (m.group("method") or "").upper() or None
            out.api_endpoints.append(
                APIEndpoint(method=method, path=path, source=self._source(line_no, m.start(), line))
            )
            out.entities.append(
                self._entity(
                    EntityType.API_ENDPOINT,
                    f"{method or ''} {path}".strip(),
                    line_no,
                    m.start(),
                    line,
                )
            )

    def _extract_dates(self, line: str, line_no: int, out: EntitySet) -> None:
        for m in DATE_RE.finditer(line):
            raw = m.group(0)
            out.deadlines.append(
                Deadline(
                    text=raw,
                    normalized=self._normalize_date(raw),
                    source=self._source(line_no, m.start(), line),
                )
            )
            out.entities.append(self._entity(EntityType.DEADLINE, raw, line_no, m.start(), line))

    def _extract_decisions(self, line: str, line_no: int, out: EntitySet) -> None:
        m = DECISION_RE.search(line)
        if not m:
            return
        text = (m.group("text") or "").strip()
        if text:
            out.decisions.append(Decision(text=text, source=self._source(line_no, m.start(), line)))
            out.entities.append(self._entity(EntityType.DECISION, text, line_no, m.start(), line))

    def _extract_glossary(self, line: str, line_no: int, out: EntitySet) -> None:
        m = GLOSSARY_RE.search(line)
        if not m:
            return
        term = m.group("term").strip()
        out.glossary_terms.append(
            GlossaryTerm(term=term, definition=None, source=self._source(line_no, m.start(), line))
        )
        out.entities.append(self._entity(EntityType.GLOSSARY_TERM, term, line_no, m.start(), line))

    def _extract_config(self, line: str, line_no: int, out: EntitySet) -> None:
        for m in CONFIG_KEY_RE.finditer(line):
            key = m.group(1)
            out.entities.append(self._entity(EntityType.CONFIG_KEY, key, line_no, m.start(), line))

    def _extract_people_orgs(self, line: str, line_no: int, out: EntitySet) -> None:
        for m in PERSON_RE.finditer(line):
            out.entities.append(
                self._entity(EntityType.PERSON, m.group(1), line_no, m.start(), line)
            )
        for m in ORG_RE.finditer(line):
            out.entities.append(
                self._entity(EntityType.ORGANIZATION, m.group(1), line_no, m.start(), line)
            )

    @staticmethod
    def _strip_code_blocks(text: str) -> str:
        return FENCED_CODE_RE.sub("", text)

    @staticmethod
    def _source(line: int, col: int, snippet: str) -> SourceRef:
        return SourceRef(line=line, column=col, snippet=snippet[:240])

    def _entity(self, t: EntityType, value: str, line: int, col: int, snippet: str) -> Entity:
        return Entity(type=t, value=value.strip(), source=self._source(line, col, snippet))

    @staticmethod
    def _normalize_date(raw: str) -> str | None:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    @staticmethod
    def _dedupe(entity_set: EntitySet) -> EntitySet:
        seen = set()
        deduped_entities: list[Entity] = []
        for e in entity_set.entities:
            key = (e.type.value, e.value.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped_entities.append(e)
        entity_set.entities = deduped_entities

        def _uniq(seq, key_fn):
            out = []
            seen_local = set()
            for item in seq:
                k = key_fn(item)
                if k in seen_local:
                    continue
                seen_local.add(k)
                out.append(item)
            return out

        entity_set.api_endpoints = _uniq(entity_set.api_endpoints, lambda a: (a.method, a.path))
        entity_set.decisions = _uniq(entity_set.decisions, lambda d: d.text.lower())
        entity_set.deadlines = _uniq(
            entity_set.deadlines, lambda d: (d.normalized or d.text).lower()
        )
        entity_set.glossary_terms = _uniq(entity_set.glossary_terms, lambda g: g.term.lower())
        return entity_set
