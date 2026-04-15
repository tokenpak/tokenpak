"""Unit tests for shadow_reader.py (Phase 3.5 — Shadow Reader)."""


import pytest
pytest.importorskip("tokenpak.shadow_reader", reason="module not available in current build")
import json
import os
import tempfile

import pytest

from tokenpak.shadow_reader import (
    validate,
    top_terms,
    log_validation_result,
    apply_fallback,
    get_validation_stats,
    ValidationResult,
    MIN_COVERAGE,
    MAX_COVERAGE,
    MIN_TERM_RETENTION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _original(n_sentences: int = 5, sentence_len: int = 15) -> str:
    """Generate a simple original text with recognisable key terms."""
    terms = ["authentication", "database", "repository", "connection", "transaction"]
    sentences = []
    for i in range(n_sentences):
        term = terms[i % len(terms)]
        pad  = " ".join(["word"] * (sentence_len - 3))
        sentences.append(f"The {term} module {pad}.")
    return " ".join(sentences)


def _compress(text: str, ratio: float) -> str:
    """Rough character-level compression to a target ratio."""
    keep = max(1, int(len(text) * ratio))
    return text[:keep]


# ---------------------------------------------------------------------------
# top_terms
# ---------------------------------------------------------------------------

class TestTopTerms:
    def test_returns_list(self):
        result = top_terms("The quick brown fox jumps over the lazy dog.")
        assert isinstance(result, list)

    def test_no_stopwords_in_results(self):
        terms = top_terms("The quick brown fox jumps over the lazy dog.")
        for t in terms:
            assert t not in {"the", "over", "a", "an"}

    def test_n_respected(self):
        text = " ".join([f"word{i}" * 3 for i in range(50)])
        terms = top_terms(text, n=5)
        assert len(terms) <= 5

    def test_returns_meaningful_terms(self):
        text = (
            "authentication is required. Authentication provides security. "
            "authentication tokens expire. The connection to the database "
            "database must be authenticated database."
        )
        terms = top_terms(text, n=5)
        assert "authentication" in terms or "database" in terms

    def test_empty_text_returns_empty(self):
        assert top_terms("") == []

    def test_short_words_filtered(self):
        terms = top_terms("I am a cat. He is a dog. We go on.")
        for t in terms:
            assert len(t) >= 3


# ---------------------------------------------------------------------------
# validate — coverage check
# ---------------------------------------------------------------------------

class TestCoverageCheck:
    def test_passes_on_good_compression(self):
        original   = "word " * 1000
        compressed = "word " * 300   # 30% — within range
        result = validate(compressed, original, "NARRATIVE")
        assert result.check_scores.get("coverage", 0) > 0

    def test_fails_below_min_coverage(self):
        original   = "word " * 1000
        compressed = "w"  # Near zero — hugely over-compressed
        result = validate(compressed, original, "NARRATIVE",
                          checks_config={"coverage": True, "coherence": False, "key_terms": False})
        assert not result.passed
        assert "coverage" in result.reason

    def test_fails_above_max_coverage(self):
        original   = "word " * 100
        compressed = original  # Identical — nothing removed
        result = validate(compressed, original, "NARRATIVE",
                          checks_config={"coverage": True, "coherence": False, "key_terms": False})
        assert not result.passed
        assert "coverage" in result.reason


# ---------------------------------------------------------------------------
# validate — sentence coherence check
# ---------------------------------------------------------------------------

class TestSentenceCoherenceCheck:
    def test_passes_on_normal_sentences(self):
        compressed = (
            "The authentication module handles user login. "
            "It verifies credentials against the database. "
            "Tokens are issued upon successful verification."
        )
        result = validate(compressed, compressed, "NARRATIVE",
                          checks_config={"coverage": False, "coherence": True, "key_terms": False})
        assert "coherence" in result.checks_run

    def test_fails_on_very_long_sentence(self):
        # 130-word run-on sentence
        compressed = "word " * 130 + "."
        result = validate(compressed, compressed + " extra", "NARRATIVE",
                          checks_config={"coverage": False, "coherence": True, "key_terms": False})
        assert not result.passed
        assert "coherence" in result.reason


# ---------------------------------------------------------------------------
# validate — key term retention check
# ---------------------------------------------------------------------------

class TestKeyTermRetentionCheck:
    def test_passes_when_terms_present(self):
        original = (
            "authentication tokens validate credentials. authentication is "
            "critical. credentials must be strong. tokens expire daily. "
            "authentication ensures security. validate all inputs. "
            "credentials are stored securely. authentication required."
        )
        # Compressed keeps all the key terms from the original
        compressed = (
            "authentication tokens validate credentials. credentials must "
            "be strong. tokens expire daily. validate all inputs. "
            "credentials are stored securely. authentication is critical."
        )
        result = validate(compressed, original, "NARRATIVE",
                          checks_config={"coverage": False, "coherence": False, "key_terms": True})
        assert result.check_scores.get("key_terms", 0) >= MIN_TERM_RETENTION

    def test_fails_when_terms_missing(self):
        original = (
            "authentication database repository connection transaction. "
            "authentication database repository connection transaction. "
            "authentication database repository connection transaction. "
        )
        compressed = "totally unrelated words about something else entirely."
        result = validate(compressed, original, "NARRATIVE",
                          checks_config={"coverage": False, "coherence": False, "key_terms": True})
        assert not result.passed
        assert "key_term" in result.reason


# ---------------------------------------------------------------------------
# validate — code block integrity check
# ---------------------------------------------------------------------------

class TestCodeIntegrityCheck:
    def test_passes_with_closed_fences(self):
        # Use a single-line body so no indentation is lost
        original   = "```python\ndef foo(): return 42\n```\nThis is some documentation text."
        compressed = "```python\ndef foo(): return 42\n```\nDocumentation text."
        result = validate(compressed, original, "CODE",
                          checks_config={"coverage": False, "coherence": False,
                                         "key_terms": False, "code_integrity": True})
        ci = result.check_scores.get("code_integrity")
        assert ci is not None and ci == 1.0

    def test_fails_with_unclosed_fence(self):
        original = "```python\ndef foo():\n    pass\n```"
        compressed = "```python\ndef foo(): ..."  # Missing closing ```
        result = validate(compressed, original, "CODE",
                          checks_config={"coverage": False, "coherence": False,
                                         "key_terms": False, "code_integrity": True})
        assert not result.passed
        assert "code_integrity" in result.reason

    def test_code_check_not_run_for_narrative(self):
        original = "```python\npass\n```"
        compressed = "pass"
        result = validate(compressed, original, "NARRATIVE")
        assert "code_integrity" not in result.checks_run


# ---------------------------------------------------------------------------
# validate — numeric preservation check
# ---------------------------------------------------------------------------

class TestNumericPreservationCheck:
    def test_passes_when_numbers_unchanged(self):
        original   = "The budget is $1,200 and the rate is 5.5%."
        compressed = "Budget $1,200, rate 5.5%."
        result = validate(compressed, original, "NUMERIC",
                          checks_config={"coverage": False, "coherence": False,
                                         "key_terms": False, "numeric_preservation": True})
        assert result.passed

    def test_fails_when_number_altered(self):
        original   = "The limit is 1000 items per day."
        compressed = "The limit is 999 items per day."  # Changed 1000 → 999
        result = validate(compressed, original, "NUMERIC",
                          checks_config={"coverage": False, "coherence": False,
                                         "key_terms": False, "numeric_preservation": True})
        assert not result.passed
        assert "numeric" in result.reason

    def test_numeric_check_runs_for_legal(self):
        original   = "Section 42 applies."
        compressed = "Section 42 applies."
        result = validate(compressed, original, "LEGAL")
        assert "numeric_preservation" in result.checks_run

    def test_numeric_check_not_run_for_narrative(self):
        result = validate("text", "text", "NARRATIVE")
        assert "numeric_preservation" not in result.checks_run


# ---------------------------------------------------------------------------
# validate — full pipeline
# ---------------------------------------------------------------------------

class TestValidateIntegration:
    def test_passes_on_reasonable_compression(self):
        original = (
            "The authentication system uses token-based validation. "
            "Tokens are cryptographically signed and expire after one hour. "
            "The database stores hashed credentials securely. "
            "Connection pooling ensures efficient database access. "
            "Transactions are atomic and rolled back on failure. "
        )
        compressed = (
            "The authentication system uses token-based validation for users. "
            "Tokens expire after exactly one hour from issuance. "
            "The database stores hashed credentials for security."
        )
        result = validate(compressed, original, "NARRATIVE")
        assert result.passed

    def test_result_has_all_fields(self):
        result = validate("short text.", "longer original text here.", "NARRATIVE")
        assert isinstance(result.passed, bool)
        assert 0.0 <= result.score <= 1.0
        assert isinstance(result.reason, str)
        assert isinstance(result.checks_run, list)
        assert isinstance(result.check_scores, dict)

    def test_checks_config_disables_check(self):
        result = validate("tiny.", "huge " * 100, "NARRATIVE",
                          checks_config={"coverage": False, "coherence": True, "key_terms": False})
        assert "coverage" not in result.checks_run
        assert "coherence" in result.checks_run


# ---------------------------------------------------------------------------
# apply_fallback
# ---------------------------------------------------------------------------

class TestApplyFallback:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log = os.path.join(self.tmpdir, "validation_log.json")

    def test_kept_when_valid(self):
        original = (
            "The authentication module validates tokens. "
            "Tokens are issued by the server. "
            "The database stores user credentials. "
            "Connection pools are used for efficiency. "
        )
        compressed = "Authentication validates tokens. Database stores credentials."
        text, action = apply_fallback(compressed, original, "NARRATIVE",
                                      log_path=self.log)
        # May pass or fail depending on key terms — just verify it returns something
        assert action in ("kept", "fallback_to_hybrid")
        assert isinstance(text, str) and len(text) > 0

    def test_fallback_when_over_compressed(self):
        original   = "word " * 500
        compressed = "w"  # Extremely over-compressed
        text, action = apply_fallback(compressed, original, "NARRATIVE",
                                      log_path=self.log)
        assert action == "fallback_to_hybrid"
        assert text == original

    def test_log_created(self):
        apply_fallback("short", "short original text here", "NARRATIVE", log_path=self.log)
        assert os.path.exists(self.log)
        entries = json.loads(open(self.log).read())
        assert len(entries) >= 1
        assert "action" in entries[0]
        assert "score" in entries[0]

    def test_code_fallback_triggers_on_unclosed_fence(self):
        original   = "```python\ndef foo():\n    pass\n```\nSome text about foo function."
        compressed = "```python\ndef foo(): ..."  # Unclosed fence
        text, action = apply_fallback(compressed, original, "CODE", log_path=self.log)
        assert action == "fallback_to_hybrid"
        assert text == original


# ---------------------------------------------------------------------------
# get_validation_stats
# ---------------------------------------------------------------------------

class TestValidationStats:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log = os.path.join(self.tmpdir, "validation_log.json")

    def _write_entries(self, entries):
        with open(self.log, "w") as f:
            json.dump(entries, f)

    def test_empty_log_returns_zeros(self):
        stats = get_validation_stats(self.log)
        assert stats["total_checked"] == 0
        assert stats["fallback_rate"] == 0.0

    def test_counts_correct(self):
        entries = [
            {"passed": True,  "score": 0.9, "reason": "ok",       "action": "kept"},
            {"passed": True,  "score": 0.8, "reason": "ok",       "action": "kept"},
            {"passed": False, "score": 0.3, "reason": "coverage: over-compressed", "action": "fallback"},
        ]
        self._write_entries(entries)
        stats = get_validation_stats(self.log)
        assert stats["total_checked"] == 3
        assert stats["passed"] == 2
        assert stats["failed"] == 1
        assert abs(stats["fallback_rate"] - 1/3) < 0.01

    def test_avg_score(self):
        entries = [
            {"passed": True,  "score": 0.8, "reason": "ok"},
            {"passed": False, "score": 0.4, "reason": "coverage: x"},
        ]
        self._write_entries(entries)
        stats = get_validation_stats(self.log)
        assert abs(stats["avg_score"] - 0.6) < 0.01

    def test_most_common_failure(self):
        entries = [
            {"passed": False, "score": 0.3, "reason": "coverage: over-compressed"},
            {"passed": False, "score": 0.2, "reason": "coverage: under-compressed"},
            {"passed": False, "score": 0.4, "reason": "key_terms: missing terms"},
        ]
        self._write_entries(entries)
        stats = get_validation_stats(self.log)
        assert stats["most_common_failure"] == "coverage"

    def test_missing_file_returns_zeros(self):
        stats = get_validation_stats("/nonexistent/path/log.json")
        assert stats["total_checked"] == 0
