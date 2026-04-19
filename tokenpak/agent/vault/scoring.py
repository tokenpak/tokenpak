"""
TokenPak — Retrieval Scoring + Coverage Score
==============================================

Implements multi-signal scoring for retrieval results:
- BM25 relevance (0.45 weight)
- Semantic similarity (0.45 weight)
- Metadata signals (0.10 weight)
- Symbol/identifier boost (+0.15)
- Path relevance boost (+0.10)
- Recency boost (+0.05)
- Staleness penalty (-0.15)
- Noise penalty (-0.10)

Also computes coverage_score to determine retrieval quality:
- must_hit_factor: All required terms found?
- concentration_factor: Results focused in few files?
- mass_factor: Top results ranked highly?

Coverage >= 0.75: strong
Coverage 0.55-0.75: ok
Coverage < 0.55: weak (escalate)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ScoringSignals:
    """Multi-signal scoring inputs."""
    bm25_score: float
    semantic_score: float  # 0.0-1.0
    is_current_commit: bool = False
    is_latest_artifact: bool = False
    is_stale_artifact: bool = False
    is_boilerplate: bool = False
    unique_file_count: int = 1  # For concentration factor

@dataclass
class CoverageScoreResult:
    """Coverage score result."""
    score: float  # 0.0-1.0
    must_hit_found: bool
    concentration_factor: float
    mass_factor: float
    interpretation: str  # "strong" | "ok" | "weak"


# ---------------------------------------------------------------------------
# Must-hit term extraction
# ---------------------------------------------------------------------------

def extract_must_hit_terms(query: str) -> List[str]:
    """Extract identifiers from query for must-hit validation.
    
    Identifies: function names, class names, error codes, variable names.
    
    Args:
        query: The search query string.
        
    Returns:
        List of extracted terms (lowercase).
    """
    # Patterns for common identifiers
    patterns = [
        r'\b[A-Z][a-zA-Z]+(?:Error|Exception)\b',  # ErrorType, ValidationError
        r'\b[a-z_]+\s*\(',  # function_call()
        r'\bclass\s+[A-Z][a-zA-Z]+\b',  # class ClassName
        r'[A-Z_]{3,}',  # CONSTANT_VALUE (3+ chars all caps)
        r'\b[a-z_]{3,}\b',  # identifier
    ]
    
    terms = set()
    for pattern in patterns:
        matches = re.findall(pattern, query, re.IGNORECASE)
        terms.update(m.strip().lower() for m in matches if m.strip())
    
    return sorted(list(terms))


def check_must_hit_coverage(
    query: str,
    chunks: List[Dict[str, Any]],
) -> Tuple[bool, List[str], List[str]]:
    """Check if must-hit terms appear in retrieved chunks.
    
    Args:
        query: Original query string.
        chunks: List of (block_dict, score) tuples or just block dicts.
        
    Returns:
        Tuple of (all_found: bool, must_hit_terms: list, found_terms: list)
    """
    must_hit_terms = extract_must_hit_terms(query)
    if not must_hit_terms:
        return True, [], []  # No must-hit terms = trivially satisfied
    
    # Normalize chunks if they're tuples
    if chunks and isinstance(chunks[0], tuple):
        contents = " ".join(str(c[0].get("content", "")) for c in chunks)
    else:
        contents = " ".join(str(c.get("content", "")) for c in chunks)
    
    found = [term for term in must_hit_terms if re.search(r'\b' + re.escape(term) + r'\b', contents, re.IGNORECASE)]
    return len(found) == len(must_hit_terms), must_hit_terms, found


# ---------------------------------------------------------------------------
# Multi-signal scoring
# ---------------------------------------------------------------------------

def compute_final_score(
    signals: ScoringSignals,
    query: str = "",
) -> float:
    """Compute final relevance score from multiple signals.
    
    Formula:
        score = (
            0.45 * sem_norm +
            0.45 * bm25_norm +
            0.10 * meta_norm
            + symbol_boost
            + path_boost
            + recency_boost
            - stale_penalty
            - noise_penalty
        )
    
    Args:
        signals: ScoringSignals object with input metrics.
        query: Original query (for path matching).
        
    Returns:
        Final score (typically 0.0-1.5 range, can exceed 1.0 with boosts).
    """
    # Normalize components to 0.0-1.0
    bm25_norm = min(1.0, max(0.0, signals.bm25_score / 10.0))  # Assume BM25 in 0-10 range
    sem_norm = min(1.0, max(0.0, signals.semantic_score))
    meta_norm = 0.5 if not signals.is_boilerplate else 0.1  # Simple metadata signal
    
    # Base weighted sum
    score = (
        0.45 * sem_norm +
        0.45 * bm25_norm +
        0.10 * meta_norm
    )
    
    # Boosts (only if query is substantial)
    if query and len(query) > 3:
        # Symbol boost: +0.15 if query contains class/function patterns
        if re.search(r'[A-Z][a-zA-Z]+|[a-z_]+\(', query):
            score += 0.15
        
        # Path boost: +0.10 if query references file path patterns
        if re.search(r'\.py|\.js|/src/|/lib/|\.tsx?', query, re.IGNORECASE):
            score += 0.10
    
    # Recency boost
    if signals.is_current_commit or signals.is_latest_artifact:
        score += 0.05
    
    # Penalties
    if signals.is_stale_artifact:
        score -= 0.15
    if signals.is_boilerplate:
        score -= 0.10
    
    # Clamp to reasonable range
    return max(0.0, min(2.0, score))


# ---------------------------------------------------------------------------
# Coverage score
# ---------------------------------------------------------------------------

def compute_coverage_score(
    query: str,
    chunks: List[Dict[str, Any]],
    scores: List[float],
) -> CoverageScoreResult:
    """Compute overall retrieval coverage score.
    
    Coverage = must_hit_factor + concentration_factor + mass_factor
    
    Args:
        query: Search query.
        chunks: Retrieved chunks.
        scores: Final scores for each chunk (same order as chunks).
        
    Returns:
        CoverageScoreResult with score + interpretation.
    """
    if not chunks or not scores:
        return CoverageScoreResult(
            score=0.0,
            must_hit_found=False,
            concentration_factor=0.0,
            mass_factor=0.0,
            interpretation="weak"
        )
    
    # Factor 1: Must-hit terms (45% of max coverage)
    all_found, _, found = check_must_hit_coverage(query, chunks)
    must_hit_factor = 0.45 if all_found else 0.0
    
    # Factor 2: Concentration (file diversity; max 25%)
    # Fewer files = higher concentration = better coverage
    # But only up to 3 files for full credit
    unique_files = len(set(c.get("source_path", c.get("block_id", "unknown")) for c in chunks))
    if unique_files <= 1:
        concentration_factor = 0.25
    elif unique_files == 2:
        concentration_factor = 0.20
    elif unique_files == 3:
        concentration_factor = 0.15
    else:
        # More files = lower concentration credit
        concentration_factor = max(0.0, 0.15 - (unique_files - 3) * 0.05)
    
    # Factor 3: Mass (top-5 average score; max 30%)
    top_5_scores = sorted(scores, reverse=True)[:5]
    avg_top_5 = sum(top_5_scores) / len(top_5_scores) if top_5_scores else 0.0
    # Scale average to 30% max
    mass_factor = max(0.0, min(0.30, (avg_top_5 / 1.0) * 0.30))
    
    # Total coverage
    coverage_score = must_hit_factor + concentration_factor + mass_factor
    
    # Interpretation
    if coverage_score >= 0.75:
        interpretation = "strong"
    elif coverage_score >= 0.55:
        interpretation = "ok"
    else:
        interpretation = "weak"
    
    return CoverageScoreResult(
        score=coverage_score,
        must_hit_found=all_found,
        concentration_factor=concentration_factor,
        mass_factor=mass_factor,
        interpretation=interpretation
    )


def is_coverage_weak(coverage_score: CoverageScoreResult) -> bool:
    """Check if coverage score indicates escalation needed."""
    return coverage_score.score < 0.55
