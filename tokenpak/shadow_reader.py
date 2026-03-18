# SPDX-License-Identifier: MIT
"""Shadow Reader — Compression Validation for TokenPak.

Before sending AGGRESSIVE-mode output to the LLM, validates that the
compressed text is still coherent. Heuristics only — no GPU, no LLM.
Tier 1 compatible (4GB RAM).

Auto-fallback: AGGRESSIVE → HYBRID when validation fails.
Results logged to .tokenpak/validation_log.json.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_VALIDATION_LOG = ".tokenpak/validation_log.json"

# Coverage ratio bounds
MIN_COVERAGE = 0.05  # Not over-compressed
MAX_COVERAGE = 0.95  # Something must have been removed

# Key term retention threshold
MIN_TERM_RETENTION = 0.50  # At least 50% of top terms must appear in compressed

# Sentence length bounds (in words)
MIN_AVG_SENTENCE_LEN = 5
MAX_AVG_SENTENCE_LEN = 80
MAX_SENTENCE_LEN = 120


# ---------------------------------------------------------------------------
# Stopword list (~100 common English stopwords)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "up",
    "about",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "out",
    "off",
    "over",
    "under",
    "again",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "s",
    "t",
    "can",
    "will",
    "just",
    "don",
    "should",
    "now",
    "i",
    "me",
    "my",
    "we",
    "our",
    "you",
    "your",
    "he",
    "him",
    "his",
    "she",
    "her",
    "they",
    "them",
    "their",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "doing",
    "would",
    "could",
    "should",
    "might",
    "may",
    "shall",
    "must",
    "need",
    "dare",
    "used",
    "also",
    "as",
    "if",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "while",
    "although",
    "because",
    "since",
    "unless",
    "until",
    "yet",
    "even",
    "though",
    "however",
    "therefore",
    "thus",
    "hence",
    "otherwise",
    "otherwise",
}

# Pattern to split into sentences (crude but fast)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Pattern to find code fences
_CODE_FENCE_OPEN = re.compile(r"^```", re.MULTILINE)
_CODE_FENCE_CLOSE = re.compile(r"^```\s*$", re.MULTILINE)

# Number detection (integers + decimals + $ amounts + percentages)
_NUMBER_PATTERN = re.compile(r"\$?\d+(?:[,_]\d{3})*(?:\.\d+)?%?|\d+\.\d+")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    passed: bool
    score: float  # 0.0–1.0 overall
    reason: str  # Summary of first failure (or "ok")
    checks_run: List[str] = field(default_factory=list)
    check_scores: dict = field(default_factory=dict)  # check_name → float


# ---------------------------------------------------------------------------
# TF-IDF term extractor (pure Python)
# ---------------------------------------------------------------------------


def top_terms(text: str, n: int = 10) -> List[str]:
    """
    Extract top-n TF-IDF terms from text.

    Tokenises on whitespace + strips punctuation. Filters stopwords.
    Uses within-document sentence-level IDF to approximate TF-IDF.
    """
    # Split into sentences (docs for IDF)
    sentences = _SENTENCE_SPLIT.split(text) if text else []
    if not sentences:
        return []

    # Tokenize each sentence
    def tokenize(s: str) -> List[str]:
        return [w.lower().strip("\"'()[]{}.,;:!?-_") for w in s.split() if len(w) >= 3]

    sent_tokens = [tokenize(s) for s in sentences]
    doc_count = len(sentences)

    # Document frequency: how many sentences contain each term
    df: dict = {}
    for tokens in sent_tokens:
        for tok in set(tokens):
            if tok not in _STOPWORDS:
                df[tok] = df.get(tok, 0) + 1

    if not df:
        return []

    # Term frequency: count across whole text
    tf: dict = {}
    all_tokens = [t for toks in sent_tokens for t in toks if t not in _STOPWORDS]
    total = max(len(all_tokens), 1)
    for tok in all_tokens:
        tf[tok] = tf.get(tok, 0) + 1

    # TF-IDF score
    scores = {}
    for tok, freq in tf.items():
        tf_score = freq / total
        idf_score = math.log((doc_count + 1) / (df.get(tok, 0) + 1)) + 1
        scores[tok] = tf_score * idf_score

    # Return top-n by score
    ranked = sorted(scores, key=lambda t: scores[t], reverse=True)
    return ranked[:n]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_coverage(original: str, compressed: str) -> tuple:
    """Return (passed, score, reason)."""
    if not original:
        return True, 1.0, "no original"
    ratio = len(compressed) / len(original)
    if ratio < MIN_COVERAGE:
        return False, ratio / MIN_COVERAGE, f"over-compressed (ratio {ratio:.3f} < {MIN_COVERAGE})"
    if ratio > MAX_COVERAGE:
        return (
            False,
            (1 - ratio) / (1 - MAX_COVERAGE),
            f"under-compressed (ratio {ratio:.3f} > {MAX_COVERAGE})",
        )
    # Score: 1.0 in the middle of the acceptable range
    mid = (MIN_COVERAGE + MAX_COVERAGE) / 2
    score = 1.0 - abs(ratio - mid) / (mid - MIN_COVERAGE)
    return True, max(0.0, min(1.0, score)), "ok"


def _check_sentence_coherence(compressed: str) -> tuple:
    """Return (passed, score, reason)."""
    if not compressed.strip():
        return True, 1.0, "empty"
    sentences = [s for s in _SENTENCE_SPLIT.split(compressed) if s.strip()]
    if not sentences:
        return True, 1.0, "no sentences"

    lens = [len(s.split()) for s in sentences]
    avg_len = sum(lens) / len(lens)
    max_len = max(lens)

    if max_len > MAX_SENTENCE_LEN:
        return False, 0.2, f"sentence too long ({max_len} words > {MAX_SENTENCE_LEN})"
    if avg_len < MIN_AVG_SENTENCE_LEN:
        return (
            False,
            avg_len / MIN_AVG_SENTENCE_LEN,
            f"avg sentence too short ({avg_len:.1f} < {MIN_AVG_SENTENCE_LEN})",
        )
    if avg_len > MAX_AVG_SENTENCE_LEN:
        return (
            False,
            MAX_AVG_SENTENCE_LEN / avg_len,
            f"avg sentence too long ({avg_len:.1f} > {MAX_AVG_SENTENCE_LEN})",
        )

    return True, 1.0, "ok"


def _check_key_terms(original: str, compressed: str) -> tuple:
    """Return (passed, score, reason)."""
    terms = top_terms(original, n=10)
    if not terms:
        return True, 1.0, "no key terms"
    comp_lower = compressed.lower()
    found = sum(1 for t in terms if re.search(r"\b" + re.escape(t) + r"\b", comp_lower))
    retention = found / len(terms)
    if retention < MIN_TERM_RETENTION:
        missing = [t for t in terms if not re.search(r"\b" + re.escape(t) + r"\b", comp_lower)]
        return (
            False,
            retention,
            f"key term retention {retention:.0%} < {MIN_TERM_RETENTION:.0%} "
            f"(missing: {', '.join(missing[:3])})",
        )
    return True, retention, "ok"


def _check_code_integrity(original: str, compressed: str) -> tuple:
    """Check code fences are properly closed and indentation not destroyed."""
    # Count fences: must be even (open = close)
    open_fences = len(_CODE_FENCE_OPEN.findall(compressed))
    if open_fences % 2 != 0:
        return False, 0.0, f"unclosed code fence ({open_fences} ``` markers)"

    # Indentation check: count lines with leading spaces in original
    orig_indented = sum(
        1 for l in original.splitlines() if l.startswith("    ") or l.startswith("\t")
    )
    comp_indented = sum(
        1 for l in compressed.splitlines() if l.startswith("    ") or l.startswith("\t")
    )
    if orig_indented > 0:
        preserved_ratio = comp_indented / orig_indented
        if preserved_ratio < 0.3:  # Lost >70% of indentation → likely destroyed
            return (
                False,
                preserved_ratio,
                f"indentation destroyed (retained {preserved_ratio:.0%} of indented lines)",
            )

    return True, 1.0, "ok"


def _check_numeric_preservation(original: str, compressed: str) -> tuple:
    """Verify numbers in compressed match numbers from original exactly."""
    orig_nums = set(_NUMBER_PATTERN.findall(original))
    comp_nums = set(_NUMBER_PATTERN.findall(compressed))

    # Numbers that appear in compressed must match their original form
    altered = []
    for num in comp_nums:
        if num not in orig_nums:
            # Check if this number appeared in original in any form
            altered.append(num)

    if altered:
        return (
            False,
            0.5,
            f"numeric alteration detected: {', '.join(altered[:3])}",
        )
    return True, 1.0, "ok"


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------


def validate(
    compressed_text: str,
    original_text: str,
    risk_class: str,
    checks_config: Optional[dict] = None,
) -> ValidationResult:
    """
    Validate compressed text against the original.

    Args:
        compressed_text: Output of AGGRESSIVE compression.
        original_text:   Original input text.
        risk_class:      Block risk class (CODE, NUMERIC, LEGAL, NARRATIVE, etc.)
        checks_config:   Optional dict to toggle individual checks.
                         Keys: coverage, coherence, key_terms, code_integrity,
                               numeric_preservation. Values: True/False.

    Returns:
        ValidationResult with passed, score, reason, checks_run, check_scores.
    """
    cfg = checks_config or {}
    risk_upper = risk_class.upper()

    all_scores: dict = {}
    checks_run: List[str] = []
    first_failure: str = ""
    failed = False

    def _run(name: str, check_fn, *args):
        nonlocal failed, first_failure
        if not cfg.get(name, True):
            return
        checks_run.append(name)
        passed, score, reason = check_fn(*args)
        all_scores[name] = score
        if not passed and not failed:
            failed = True
            first_failure = f"{name}: {reason}"

    # Always run
    _run("coverage", _check_coverage, original_text, compressed_text)
    _run("coherence", _check_sentence_coherence, compressed_text)
    _run("key_terms", _check_key_terms, original_text, compressed_text)

    # Risk-class conditional
    if risk_upper == "CODE":
        _run("code_integrity", _check_code_integrity, original_text, compressed_text)

    if risk_upper in ("NUMERIC", "LEGAL"):
        _run("numeric_preservation", _check_numeric_preservation, original_text, compressed_text)

    # Aggregate score: average of all check scores
    overall = sum(all_scores.values()) / max(len(all_scores), 1)

    return ValidationResult(
        passed=not failed,
        score=round(overall, 4),
        reason=first_failure if failed else "ok",
        checks_run=checks_run,
        check_scores=all_scores,
    )


# ---------------------------------------------------------------------------
# Auto-fallback + logging
# ---------------------------------------------------------------------------


def log_validation_result(
    result: ValidationResult,
    block_ref: str,
    action: str,
    log_path: str = DEFAULT_VALIDATION_LOG,
) -> None:
    """Append a validation result to the JSON log (non-destructive)."""
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing: list = []
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "block_ref": block_ref,
        "action": action,
        "passed": result.passed,
        "score": result.score,
        "reason": result.reason,
        "checks_run": result.checks_run,
    }
    existing.append(entry)
    p.write_text(json.dumps(existing, indent=2))


def apply_fallback(
    compressed_text: str,
    original_text: str,
    risk_class: str,
    block_ref: str = "unknown",
    log_path: str = DEFAULT_VALIDATION_LOG,
) -> tuple:
    """
    Validate compression. If failed, fall back to original (HYBRID behaviour).

    Args:
        compressed_text: AGGRESSIVE-mode compressed output.
        original_text:   Original source text.
        risk_class:      Block risk class.
        block_ref:       Block identifier for logging.
        log_path:        Path to validation log.

    Returns:
        (text, action) where:
          text   = compressed_text if validation passed, else original_text
          action = "kept" | "fallback_to_hybrid"
    """
    result = validate(compressed_text, original_text, risk_class)

    if result.passed:
        action = "kept"
        text = compressed_text
    else:
        action = "fallback_to_hybrid"
        text = original_text

    log_validation_result(result, block_ref, action, log_path)
    return text, action


def get_validation_stats(log_path: str = DEFAULT_VALIDATION_LOG) -> dict:
    """
    Return aggregate statistics from the validation log.

    Returns:
        Dict with total_checked, passed, failed, fallback_rate,
        avg_score, most_common_failure.
    """
    p = Path(log_path)
    if not p.exists():
        return {
            "total_checked": 0,
            "passed": 0,
            "failed": 0,
            "fallback_rate": 0.0,
            "avg_score": 0.0,
            "most_common_failure": None,
        }
    try:
        entries = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        entries = []

    total = len(entries)
    passed = sum(1 for e in entries if e.get("passed"))
    failed = total - passed
    avg_score = sum(e.get("score", 0) for e in entries) / max(total, 1)

    # Most common failure reason
    reasons = [e.get("reason", "") for e in entries if not e.get("passed")]
    most_common = None
    if reasons:
        freq: dict = {}
        for r in reasons:
            key = r.split(":")[0].strip()  # Check name only
            freq[key] = freq.get(key, 0) + 1
        most_common = max(freq, key=lambda k: freq[k])

    return {
        "total_checked": total,
        "passed": passed,
        "failed": failed,
        "fallback_rate": round(failed / max(total, 1), 4),
        "avg_score": round(avg_score, 4),
        "most_common_failure": most_common,
    }
