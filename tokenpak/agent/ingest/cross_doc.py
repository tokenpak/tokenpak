# SPDX-License-Identifier: Apache-2.0
"""tokenpak/agent/ingest/cross_doc.py

Cross-Document Normalization — Phase 5C
========================================
Normalize N documents of the same type into compact research cards,
then compare them across three modes:

    from tokenpak.agent.ingest.cross_doc import CrossDocAnalyzer

    analyzer = CrossDocAnalyzer()
    cards = analyzer.normalize(docs)                         # list[DocCard]
    report = analyzer.compare(cards, mode="side_by_side")    # ComparisonReport
    print(report.summary())

Comparison modes:
  - ``side_by_side``  — field-by-field schema comparison across all docs
  - ``merged``        — combine findings / synthesis across docs
  - ``conflict``      — detect and surface disagreements between docs

Artifacts produced:
  - AgreementMap      — per-field consensus / disagreement map
  - EvidenceMatrix    — doc × claim evidence matrix
  - MetricTable       — numeric metric comparison table
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# DocCard — compact normalized schema for a single document
# ---------------------------------------------------------------------------


@dataclass
class DocCard:
    """Compact research card: normalized representation of a single document.

    10 papers → 10 DocCards instead of 10 long texts.
    Each card is a structured, token-efficient summary.
    """

    source: str
    """Source identifier (filename, URL, title, etc.)."""

    title: Optional[str] = None
    """Document title."""

    authors: List[str] = field(default_factory=list)
    """Author list."""

    abstract: Optional[str] = None
    """Abstract or executive summary (≤300 chars)."""

    key_findings: List[str] = field(default_factory=list)
    """Top-level findings / claims."""

    methods: List[str] = field(default_factory=list)
    """Methods / approaches used."""

    metrics: Dict[str, Any] = field(default_factory=dict)
    """Numeric metrics extracted (name → value)."""

    conclusions: List[str] = field(default_factory=list)
    """Conclusions / recommendations."""

    keywords: List[str] = field(default_factory=list)
    """Domain keywords."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Passthrough metadata from the caller."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "key_findings": self.key_findings,
            "methods": self.methods,
            "metrics": self.metrics,
            "conclusions": self.conclusions,
            "keywords": self.keywords,
            "metadata": self.metadata,
        }

    def token_estimate(self) -> int:
        """Rough token estimate (≈4 chars/token)."""
        raw = " ".join(
            [
                self.title or "",
                " ".join(self.authors),
                self.abstract or "",
                " ".join(self.key_findings),
                " ".join(self.methods),
                str(self.metrics),
                " ".join(self.conclusions),
            ]
        )
        return max(1, len(raw) // 4)

    def __repr__(self) -> str:
        return (
            f"<DocCard source={self.source!r} "
            f"findings={len(self.key_findings)} "
            f"metrics={len(self.metrics)} "
            f"~{self.token_estimate()}tok>"
        )


# ---------------------------------------------------------------------------
# SchemaConverter — lightweight text → DocCard normalizer
# ---------------------------------------------------------------------------

# Regex helpers
_TITLE_RE = re.compile(r"(?i)^(?:title[:\s]+|#\s*)(.+)")
_AUTHOR_RE = re.compile(r"(?i)^(?:author[s]?[:\s]+)(.+)")
_ABSTRACT_RE = re.compile(r"(?i)(?:abstract|summary)[:\s]*\n?(.*?)(?=\n\n|\Z)", re.DOTALL)
_SECTION_HDR_RE = re.compile(r"(?i)^#{1,3}\s+(.+)|^([A-Z][A-Z\s]{3,}):?\s*$", re.MULTILINE)
_METRIC_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9\s_\-]{0,30})"
    r"[:\s=]+"
    r"(?P<value>\d+\.?\d*)\s*"
    r"(?P<unit>%|ms|s|MB|GB|tokens?|pts?|score)?"
)
_FINDING_TRIGGERS = frozenset(
    [
        "we show",
        "we find",
        "we found",
        "we demonstrate",
        "results show",
        "our method",
        "significantly",
        "outperforms",
        "improves",
        "achieves",
        "reduces",
        "increases",
    ]
)
_CONCLUSION_TRIGGERS = frozenset(
    [
        "in conclusion",
        "to conclude",
        "in summary",
        "we conclude",
        "future work",
        "we recommend",
        "this paper presents",
    ]
)
_METHOD_TRIGGERS = frozenset(
    [
        "we use",
        "we propose",
        "we apply",
        "our approach",
        "our model",
        "using a",
        "based on",
        "fine-tuned",
        "trained on",
        "implemented",
    ]
)


class SchemaConverter:
    """Convert raw document text into a compact DocCard.

    Designed to be reusable across document types: research papers,
    technical reports, meeting notes, blog posts.
    """

    def __init__(
        self,
        max_abstract_chars: int = 300,
        max_findings: int = 5,
        max_methods: int = 5,
        max_conclusions: int = 5,
        max_keywords: int = 10,
    ):
        self.max_abstract_chars = max_abstract_chars
        self.max_findings = max_findings
        self.max_methods = max_methods
        self.max_conclusions = max_conclusions
        self.max_keywords = max_keywords

    def convert(
        self,
        text: str,
        source: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DocCard:
        """Normalize raw text into a DocCard."""
        metadata = metadata or {}
        lines = text.splitlines()

        title = self._extract_title(lines, metadata)
        authors = self._extract_authors(lines, metadata)
        abstract = self._extract_abstract(text)
        key_findings = self._extract_sentences(text, _FINDING_TRIGGERS, self.max_findings)
        methods = self._extract_sentences(text, _METHOD_TRIGGERS, self.max_methods)
        conclusions = self._extract_sentences(text, _CONCLUSION_TRIGGERS, self.max_conclusions)
        metrics = self._extract_metrics(text)
        keywords = self._extract_keywords(text)

        return DocCard(
            source=source,
            title=title,
            authors=authors,
            abstract=abstract,
            key_findings=key_findings,
            methods=methods,
            metrics=metrics,
            conclusions=conclusions,
            keywords=keywords,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    def _extract_title(
        self, lines: List[str], metadata: Dict[str, Any]
    ) -> Optional[str]:
        if "title" in metadata:
            return str(metadata["title"])
        for line in lines[:15]:
            m = _TITLE_RE.match(line.strip())
            if m:
                return m.group(1).strip()
        # Fallback: first non-empty line
        for line in lines[:5]:
            stripped = line.strip()
            if stripped and len(stripped) > 5:
                return stripped[:120]
        return None

    def _extract_authors(
        self, lines: List[str], metadata: Dict[str, Any]
    ) -> List[str]:
        if "authors" in metadata:
            raw = metadata["authors"]
            if isinstance(raw, list):
                return raw
            return [a.strip() for a in str(raw).split(",")]
        for line in lines[:20]:
            m = _AUTHOR_RE.match(line.strip())
            if m:
                return [a.strip() for a in re.split(r",|;|and ", m.group(1)) if a.strip()]
        return []

    def _extract_abstract(self, text: str) -> Optional[str]:
        m = _ABSTRACT_RE.search(text)
        if m:
            raw = " ".join(m.group(1).split())
            return raw[: self.max_abstract_chars]
        # Fallback: first substantive paragraph
        paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 80]
        if paras:
            return paras[0][: self.max_abstract_chars]
        return None

    def _extract_sentences(self, text: str, triggers: frozenset, limit: int) -> List[str]:
        """Extract sentences that contain any trigger phrase."""
        results: List[str] = []
        # Split into rough sentences
        sentences = re.split(r"(?<=[.!?])\s+", text)
        lower_text = text.lower()
        for sent in sentences:
            sl = sent.lower().strip()
            if any(t in sl for t in triggers):
                clean = sent.strip()
                if 20 < len(clean) < 300:
                    results.append(clean)
                    if len(results) >= limit:
                        break
        return results

    def _extract_metrics(self, text: str) -> Dict[str, Any]:
        """Extract numeric metrics as name → value dict."""
        metrics: Dict[str, Any] = {}
        for m in _METRIC_RE.finditer(text):
            name = m.group("name").strip().lower().replace(" ", "_")
            try:
                value: Any = float(m.group("value"))
                if value == int(value):
                    value = int(value)
            except ValueError:
                continue
            unit = m.group("unit") or ""
            if unit:
                metrics[name] = f"{value}{unit}"
            else:
                metrics[name] = value
            if len(metrics) >= 20:
                break
        return metrics

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from keyword lines or fallback frequency."""
        kw_match = re.search(r"(?i)keywords?[:\s]+(.+?)(?:\n|$)", text)
        if kw_match:
            return [
                k.strip()
                for k in re.split(r",|;", kw_match.group(1))
                if k.strip()
            ][: self.max_keywords]
        # Fallback: capitalized multi-word phrases (crude)
        caps = re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", text)
        seen: Dict[str, int] = {}
        for c in caps:
            seen[c] = seen.get(c, 0) + 1
        return sorted(seen, key=seen.get, reverse=True)[: self.max_keywords]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Comparison artifacts
# ---------------------------------------------------------------------------


@dataclass
class AgreementMap:
    """Per-field consensus / disagreement map across N documents.

    Each field maps to:
      - ``agreement``: fields where all docs agree (same value)
      - ``partial``:   fields where some docs agree
      - ``conflict``:  fields where all docs disagree
    """

    field: str
    values: List[Tuple[str, Any]]  # (source, value)
    consensus: Optional[str]       # None if no consensus

    @property
    def agreement_ratio(self) -> float:
        if not self.values:
            return 0.0
        counts: Dict[Any, int] = {}
        for _, v in self.values:
            key = str(v)
            counts[key] = counts.get(key, 0) + 1
        top = max(counts.values())
        return top / len(self.values)

    @property
    def status(self) -> str:
        r = self.agreement_ratio
        if r >= 0.9:
            return "agreement"
        if r >= 0.5:
            return "partial"
        return "conflict"

    def __repr__(self) -> str:
        return f"<AgreementMap field={self.field!r} status={self.status} ratio={self.agreement_ratio:.2f}>"


@dataclass
class EvidenceMatrix:
    """Document × claim evidence matrix.

    Rows = documents, columns = claims (from all findings).
    Cell = True if the doc supports the claim, False if it contradicts, None if silent.
    """

    claims: List[str]
    rows: List[Dict[str, Any]]  # each: {source, evidence: {claim_idx: bool|None}}

    def to_table(self) -> str:
        """Render as ASCII table."""
        header = "Source".ljust(20) + " | " + " | ".join(
            f"C{i+1}".center(4) for i in range(len(self.claims))
        )
        sep = "-" * len(header)
        lines = [header, sep]
        for row in self.rows:
            ev = row["evidence"]
            cells = []
            for i in range(len(self.claims)):
                v = ev.get(i)
                cells.append(("✓" if v else ("✗" if v is False else "·")).center(4))
            lines.append(row["source"][:20].ljust(20) + " | " + " | ".join(cells))
        return "\n".join(lines)


@dataclass
class MetricTable:
    """Numeric metric comparison table across N documents."""

    metric_names: List[str]
    rows: List[Dict[str, Any]]  # each: {source, metrics: {name: value}}

    def to_table(self) -> str:
        """Render as ASCII table."""
        col_w = 12
        header = "Source".ljust(20) + " | " + " | ".join(
            m[:col_w].center(col_w) for m in self.metric_names
        )
        sep = "-" * len(header)
        lines = [header, sep]
        for row in self.rows:
            cells = []
            for m in self.metric_names:
                v = row["metrics"].get(m, "—")
                cells.append(str(v).center(col_w))
            lines.append(row["source"][:20].ljust(20) + " | " + " | ".join(cells))
        return "\n".join(lines)

    def divergence(self) -> Dict[str, float]:
        """For numeric metrics, compute coefficient of variation (std/mean).

        Higher → more disagreement between docs.
        """
        import math

        result: Dict[str, float] = {}
        for m in self.metric_names:
            vals: List[float] = []
            for row in self.rows:
                v = row["metrics"].get(m)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
            if len(vals) < 2:
                continue
            mean = sum(vals) / len(vals)
            if mean == 0:
                continue
            variance = sum((x - mean) ** 2 for x in vals) / len(vals)
            result[m] = math.sqrt(variance) / abs(mean)
        return result


# ---------------------------------------------------------------------------
# ComparisonReport — unified output of CrossDocAnalyzer.compare()
# ---------------------------------------------------------------------------


@dataclass
class ComparisonReport:
    """Unified output from a cross-document comparison run."""

    mode: str
    cards: List[DocCard]
    agreement_maps: List[AgreementMap]
    evidence_matrix: EvidenceMatrix
    metric_table: MetricTable
    synthesis: Optional[str] = None  # only for mode=merged
    conflicts: List[str] = field(default_factory=list)  # only for mode=conflict

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"=== Cross-Document Report ({self.mode}) ===",
            f"Documents : {len(self.cards)}",
            f"Claims    : {len(self.evidence_matrix.claims)}",
            f"Metrics   : {len(self.metric_table.metric_names)}",
        ]

        # Agreement summary
        by_status: Dict[str, int] = {}
        for am in self.agreement_maps:
            by_status[am.status] = by_status.get(am.status, 0) + 1
        if by_status:
            lines.append(
                "Fields    : "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
            )

        if self.mode == "merged" and self.synthesis:
            lines.append("\n--- Merged Synthesis ---")
            lines.append(self.synthesis)

        if self.mode == "conflict" and self.conflicts:
            lines.append(f"\n--- Conflicts ({len(self.conflicts)}) ---")
            lines.extend(f"  • {c}" for c in self.conflicts[:10])

        if self.metric_table.metric_names:
            lines.append("\n--- Metric Table ---")
            lines.append(self.metric_table.to_table())

        div = self.metric_table.divergence()
        if div:
            lines.append("\n--- Metric Divergence (CoV) ---")
            for m, v in sorted(div.items(), key=lambda x: -x[1]):
                lines.append(f"  {m}: {v:.2f}")

        lines.append("\n--- Evidence Matrix ---")
        lines.append(self.evidence_matrix.to_table())
        if self.evidence_matrix.claims:
            lines.append("\nClaim legend:")
            for i, c in enumerate(self.evidence_matrix.claims):
                lines.append(f"  C{i+1}: {c[:100]}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CrossDocAnalyzer — main entrypoint
# ---------------------------------------------------------------------------


class CrossDocAnalyzer:
    """Normalize and compare N documents via compact DocCards.

    Usage::

        analyzer = CrossDocAnalyzer()

        # Option A: pass raw texts
        docs = [
            {"source": "paper_a.pdf", "text": "..."},
            {"source": "paper_b.pdf", "text": "..."},
        ]
        cards = analyzer.normalize(docs)
        report = analyzer.compare(cards, mode="side_by_side")
        print(report.summary())

        # Option B: pass pre-built DocCards
        report = analyzer.compare(my_cards, mode="conflict")
    """

    def __init__(self, converter: Optional[SchemaConverter] = None):
        self.converter = converter or SchemaConverter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(
        self,
        docs: Sequence[Dict[str, Any]],
    ) -> List[DocCard]:
        """Normalize a list of raw document dicts into DocCards.

        Each doc dict must have:
          - ``text`` (str): raw document text
          - ``source`` (str, optional): identifier (defaults to index)
          - any extra keys → passed through as metadata
        """
        cards: List[DocCard] = []
        for i, doc in enumerate(docs):
            text = doc.get("text", "")
            source = doc.get("source", f"doc_{i}")
            meta = {k: v for k, v in doc.items() if k not in ("text", "source")}
            card = self.converter.convert(text, source=source, metadata=meta)
            cards.append(card)
        return cards

    def compare(
        self,
        cards: Sequence[DocCard],
        mode: str = "side_by_side",
    ) -> ComparisonReport:
        """Compare N DocCards using the given mode.

        Args:
            cards: Sequence of normalized DocCards.
            mode:  ``side_by_side`` | ``merged`` | ``conflict``

        Returns:
            ComparisonReport with all comparison artifacts.
        """
        if not cards:
            raise ValueError("compare() requires at least one DocCard")

        agreement_maps = self._build_agreement_maps(cards)
        evidence_matrix = self._build_evidence_matrix(cards)
        metric_table = self._build_metric_table(cards)

        synthesis: Optional[str] = None
        conflicts: List[str] = []

        if mode == "merged":
            synthesis = self._synthesize(cards)
        elif mode == "conflict":
            conflicts = self._detect_conflicts(cards, agreement_maps, metric_table)
        elif mode != "side_by_side":
            raise ValueError(f"Unknown mode: {mode!r}. Use side_by_side, merged, or conflict.")

        return ComparisonReport(
            mode=mode,
            cards=list(cards),
            agreement_maps=agreement_maps,
            evidence_matrix=evidence_matrix,
            metric_table=metric_table,
            synthesis=synthesis,
            conflicts=conflicts,
        )

    # ------------------------------------------------------------------
    # Artifact builders
    # ------------------------------------------------------------------

    def _build_agreement_maps(self, cards: Sequence[DocCard]) -> List[AgreementMap]:
        """Build per-field agreement maps."""
        fields = {
            "title": lambda c: c.title,
            "abstract_length": lambda c: len(c.abstract) if c.abstract else 0,
            "num_findings": lambda c: len(c.key_findings),
            "num_methods": lambda c: len(c.methods),
            "num_metrics": lambda c: len(c.metrics),
            "num_conclusions": lambda c: len(c.conclusions),
        }
        maps: List[AgreementMap] = []
        for fname, extractor in fields.items():
            values = [(c.source, extractor(c)) for c in cards]
            # Compute consensus: most common value
            counts: Dict[Any, int] = {}
            for _, v in values:
                key = str(v)
                counts[key] = counts.get(key, 0) + 1
            top_val = max(counts, key=counts.__getitem__) if counts else None
            consensus = top_val if (counts.get(top_val or "", 0) > 1) else None
            maps.append(AgreementMap(field=fname, values=values, consensus=consensus))
        return maps

    def _build_evidence_matrix(self, cards: Sequence[DocCard]) -> EvidenceMatrix:
        """Build a doc × claim evidence matrix.

        Claims = union of all key_findings across cards.
        Cell = True if finding is near-duplicate in that doc, None otherwise.
        """
        # Collect unique claims (simple dedup by first 60 chars)
        seen_prefixes: List[str] = []
        claims: List[str] = []
        for card in cards:
            for f in card.key_findings:
                prefix = f[:60].lower()
                if prefix not in seen_prefixes:
                    seen_prefixes.append(prefix)
                    claims.append(f)

        rows: List[Dict[str, Any]] = []
        for card in cards:
            evidence: Dict[int, Optional[bool]] = {}
            card_prefixes = [f[:60].lower() for f in card.key_findings]
            for i, claim in enumerate(claims):
                claim_prefix = claim[:60].lower()
                # Check for overlap (simple substring / prefix match)
                found = any(
                    claim_prefix in cp or cp in claim_prefix
                    for cp in card_prefixes
                )
                evidence[i] = True if found else None
            rows.append({"source": card.source, "evidence": evidence})

        return EvidenceMatrix(claims=claims, rows=rows)

    def _build_metric_table(self, cards: Sequence[DocCard]) -> MetricTable:
        """Build numeric metric comparison table."""
        # Collect all metric names across cards
        all_names: List[str] = []
        for card in cards:
            for name in card.metrics:
                if name not in all_names:
                    all_names.append(name)
        all_names = sorted(all_names)

        rows: List[Dict[str, Any]] = []
        for card in cards:
            rows.append({"source": card.source, "metrics": dict(card.metrics)})

        return MetricTable(metric_names=all_names, rows=rows)

    def _synthesize(self, cards: Sequence[DocCard]) -> str:
        """Merge findings, methods, conclusions across all cards into synthesis."""
        all_findings: List[str] = []
        all_methods: List[str] = []
        all_conclusions: List[str] = []

        for card in cards:
            all_findings.extend(card.key_findings)
            all_methods.extend(card.methods)
            all_conclusions.extend(card.conclusions)

        lines = []
        if all_findings:
            lines.append("Findings:")
            for f in _dedup(all_findings)[:8]:
                lines.append(f"  • {f[:120]}")
        if all_methods:
            lines.append("Methods:")
            for m in _dedup(all_methods)[:6]:
                lines.append(f"  • {m[:120]}")
        if all_conclusions:
            lines.append("Conclusions:")
            for c in _dedup(all_conclusions)[:6]:
                lines.append(f"  • {c[:120]}")

        # Keyword union
        all_kw = list({kw for card in cards for kw in card.keywords})
        if all_kw:
            lines.append("Keywords: " + ", ".join(all_kw[:15]))

        return "\n".join(lines) if lines else "No synthesized content available."

    def _detect_conflicts(
        self,
        cards: Sequence[DocCard],
        agreement_maps: List[AgreementMap],
        metric_table: MetricTable,
    ) -> List[str]:
        """Surface specific disagreements across docs."""
        conflicts: List[str] = []

        # Schema-level conflicts
        for am in agreement_maps:
            if am.status == "conflict":
                vals_str = ", ".join(
                    f"{src}: {val}" for src, val in am.values[:4]
                )
                conflicts.append(f"Field '{am.field}' conflicts: {vals_str}")
            elif am.status == "partial":
                conflicts.append(
                    f"Field '{am.field}' partial agreement "
                    f"(ratio={am.agreement_ratio:.0%})"
                )

        # Metric-level conflicts (high CoV)
        div = metric_table.divergence()
        for m, cov in sorted(div.items(), key=lambda x: -x[1]):
            if cov > 0.3:
                vals = [
                    f"{row['source'][:12]}: {row['metrics'].get(m, '—')}"
                    for row in metric_table.rows
                    if m in row["metrics"]
                ]
                conflicts.append(
                    f"Metric '{m}' high divergence (CoV={cov:.2f}): "
                    + ", ".join(vals[:4])
                )

        return conflicts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dedup(items: List[str]) -> List[str]:
    """Deduplicate list preserving order (prefix-based)."""
    seen: List[str] = []
    result: List[str] = []
    for item in items:
        prefix = item[:50].lower()
        if prefix not in seen:
            seen.append(prefix)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def analyze_docs(
    docs: Sequence[Dict[str, Any]],
    mode: str = "side_by_side",
    converter: Optional[SchemaConverter] = None,
) -> ComparisonReport:
    """One-shot: normalize N docs and compare them.

    Args:
        docs: List of dicts with 'text' (required), 'source' (optional).
        mode: 'side_by_side', 'merged', or 'conflict'.
        converter: Optional custom SchemaConverter.

    Returns:
        ComparisonReport.
    """
    analyzer = CrossDocAnalyzer(converter=converter)
    cards = analyzer.normalize(docs)
    return analyzer.compare(cards, mode=mode)
