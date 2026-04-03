"""TokenPak — Claim/Evidence Indexer

Extracts claims, evidence, metrics, and citations from documents.
Links claim→evidence pairs for denser retrieval results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class ClaimEvidence:
    """A claim linked with supporting evidence, metrics, and citations."""

    claim: str
    evidence: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    source_section: str = ""
    confidence: float = 0.5  # 0.0–1.0 based on evidence strength

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "claim": self.claim,
            "evidence": self.evidence,
            "metrics": self.metrics,
            "citations": self.citations,
            "source_section": self.source_section,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Heuristics for Detection
# ---------------------------------------------------------------------------

# Patterns for claim detection
CLAIM_PATTERNS = [
    r"(?:we found|we discovered|we identified|results show|results indicate|data shows|conclusion|our analysis|recommend|recommendation|we suggest|suggested)",
    r"(?:key finding|critical insight|important discovery|major breakthrough)",
    r"(?:the study shows|research indicates|evidence suggests|it appears that)",
]

# Patterns for evidence detection
EVIDENCE_PATTERNS = [
    r"\d+(?:\.\d+)?%",  # percentages
    r"\d+(?:\.\d+)?\s*(?:million|billion|thousand|m|b|k|usd|\$)",  # numbers with units
    r"(?:study|research|experiment|trial|analysis) (?:shows|indicates|reveals|demonstrates)",
    r"(?:according to|based on|data from|results from)",
    r"(?:quote|cited|stated|reported)",
]

# Patterns for metrics detection
METRIC_PATTERNS = [
    r"\d+(?:\.\d+)?(?:\s*%)?",  # standalone numbers
    r"(?:date|time|year|month|week):\s*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}",  # dates
    r"(?:q[1-4]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}",  # quarters/dates
]

# Patterns for citation detection
CITATION_PATTERNS = [
    r"\[(?:\d+|[A-Za-z0-9]+)\]",  # bracketed citations
    r"\((?:[A-Za-z]+ et al\.|[A-Za-z]+, \d{4})\)",  # author-year citations
    r"(?:ref|reference|see|cite)\.?\s*\d+",  # explicit references
    r"https?://[^\s]+",  # URLs
]


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences, handling abbreviations."""
    # Simple sentence splitter (split on . ! ? followed by space + uppercase)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _matches_pattern(text: str, patterns: list[str]) -> bool:
    """Check if text matches any pattern in the list."""
    text_lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def _extract_metrics_from_text(text: str) -> list[str]:
    """Extract numerical metrics from text."""
    metrics = []
    for pattern in METRIC_PATTERNS:
        matches = re.findall(pattern, text)
        metrics.extend(matches)
    return list(set(metrics))  # Deduplicate


def _extract_citations_from_text(text: str) -> list[str]:
    """Extract citation references from text."""
    citations = []
    for pattern in CITATION_PATTERNS:
        matches = re.findall(pattern, text)
        citations.extend(matches)
    return list(set(citations))  # Deduplicate


def _calculate_confidence(claim: str, evidence_items: list[str], metrics: list[str]) -> float:
    """Calculate confidence score based on evidence strength."""
    base_confidence = 0.5

    # More evidence → higher confidence
    if evidence_items:
        base_confidence += min(0.2, len(evidence_items) * 0.05)

    # More metrics → higher confidence
    if metrics:
        base_confidence += min(0.2, len(metrics) * 0.05)

    # Specific language patterns in claim → higher confidence
    if _matches_pattern(claim, CLAIM_PATTERNS):
        base_confidence += 0.1

    return min(1.0, base_confidence)


# ---------------------------------------------------------------------------
# Main Extraction Logic
# ---------------------------------------------------------------------------


def extract_claims_from_text(text: str, min_confidence: float = 0.4) -> list[ClaimEvidence]:
    """Extract claims and linked evidence from document text.

    Args:
        text: Document text to analyze
        min_confidence: Minimum confidence threshold (0.0–1.0)

    Returns:
        List of ClaimEvidence objects
    """
    sentences = _extract_sentences(text)
    claims = []

    for i, sentence in enumerate(sentences):
        if not _matches_pattern(sentence, CLAIM_PATTERNS):
            continue

        # Extract supporting evidence (nearby sentences)
        evidence = []
        metrics = []
        citations = []

        # Look at neighboring sentences for evidence
        for j in range(max(0, i - 2), min(len(sentences), i + 3)):
            if i == j:
                continue
            neighboring = sentences[j]

            if _matches_pattern(neighboring, EVIDENCE_PATTERNS):
                evidence.append(neighboring)

            # Always extract metrics and citations from neighboring sentences
            metrics.extend(_extract_metrics_from_text(neighboring))
            citations.extend(_extract_citations_from_text(neighboring))

        # Extract from claim sentence itself
        metrics.extend(_extract_metrics_from_text(sentence))
        citations.extend(_extract_citations_from_text(sentence))

        # Deduplicate
        evidence = list(set(evidence))
        metrics = list(set(metrics))
        citations = list(set(citations))

        # Calculate confidence
        confidence = _calculate_confidence(sentence, evidence, metrics)

        if confidence < min_confidence:
            continue

        # Create ClaimEvidence object
        claim_obj = ClaimEvidence(
            claim=sentence,
            evidence=evidence,
            metrics=metrics,
            citations=citations,
            source_section="",  # Would be filled in if section info was available
            confidence=confidence,
        )
        claims.append(claim_obj)

    return claims


def extract_claims_from_document(document: dict) -> list[ClaimEvidence]:
    """Extract claims from a structured document.

    Args:
        document: Dictionary with 'text', optional 'section', 'title', etc.

    Returns:
        List of ClaimEvidence objects
    """
    text = document.get("text", "")
    section = document.get("section", "")

    claims = extract_claims_from_text(text)

    # Update source_section if available
    for claim in claims:
        if section:
            claim.source_section = section

    return claims


def link_claims_by_proximity(
    claims: list[ClaimEvidence], distance: int = 1
) -> dict[str, list[ClaimEvidence]]:
    """Group related claims by proximity.

    Args:
        claims: List of ClaimEvidence objects
        distance: Number of sentences to consider as "related"

    Returns:
        Dictionary mapping claim text to related ClaimEvidence objects
    """
    groups = {}

    for i, claim in enumerate(claims):
        key = claim.claim
        if key not in groups:
            groups[key] = []

        # Find nearby claims
        for j in range(max(0, i - distance), min(len(claims), i + distance + 1)):
            if i != j:
                groups[key].append(claims[j])

    return groups


# ---------------------------------------------------------------------------
# Integration with Retrieval
# ---------------------------------------------------------------------------


def compact_for_retrieval(claims: list[ClaimEvidence], top_n: int = 3) -> list[dict]:
    """Prepare claims for retrieval output.

    Returns compact representation suitable for retrieval results.

    Args:
        claims: List of ClaimEvidence objects
        top_n: Maximum number of evidence items to include per claim

    Returns:
        List of dictionaries suitable for retrieval output
    """
    result = []

    for claim in claims:
        compact = {
            "claim": claim.claim,
            "evidence": claim.evidence[:top_n],
            "metrics": claim.metrics[:top_n],
            "citations": claim.citations[:top_n],
            "confidence": claim.confidence,
        }
        result.append(compact)

    return result
