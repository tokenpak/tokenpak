"""
Unit tests for compression/canon.py — canonical form normalization / validation.

Covers:
  - ValidationResult dataclass
  - top_terms() TF-IDF keyword extraction
  - Private check functions: coverage, coherence, key_terms, code_integrity, numeric_preservation
  - validate() — core entry point, all risk classes
  - log_validation_result() — file I/O with tmp_path
  - apply_fallback() — text selection and action labels
  - get_validation_stats() — aggregated statistics
  - Edge cases: empty input, unicode, large input
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from tokenpak.compression.canon import (
    DEFAULT_VALIDATION_LOG,
    MAX_AVG_SENTENCE_LEN,
    MAX_COVERAGE,
    MAX_SENTENCE_LEN,
    MIN_AVG_SENTENCE_LEN,
    MIN_COVERAGE,
    MIN_TERM_RETENTION,
    ValidationResult,
    _check_code_integrity,
    _check_coverage,
    _check_key_terms,
    _check_numeric_preservation,
    _check_sentence_coherence,
    apply_fallback,
    get_validation_stats,
    log_validation_result,
    top_terms,
    validate,
)


# ── Constants sanity checks ───────────────────────────────────────────────────


class TestConstants:
    def test_min_coverage_less_than_max(self):
        assert MIN_COVERAGE < MAX_COVERAGE

    def test_coverage_bounds_in_range(self):
        assert 0.0 < MIN_COVERAGE < 1.0
        assert 0.0 < MAX_COVERAGE < 1.0

    def test_sentence_length_bounds(self):
        assert MIN_AVG_SENTENCE_LEN < MAX_AVG_SENTENCE_LEN
        assert MAX_AVG_SENTENCE_LEN < MAX_SENTENCE_LEN

    def test_term_retention_in_range(self):
        assert 0.0 < MIN_TERM_RETENTION < 1.0


# ── ValidationResult dataclass ────────────────────────────────────────────────


class TestValidationResult:
    def test_passed_true(self):
        vr = ValidationResult(passed=True, score=0.9, reason="ok")
        assert vr.passed is True

    def test_passed_false(self):
        vr = ValidationResult(passed=False, score=0.1, reason="coverage: low")
        assert vr.passed is False

    def test_default_checks_run_empty(self):
        vr = ValidationResult(passed=True, score=1.0, reason="ok")
        assert vr.checks_run == []

    def test_default_check_scores_empty(self):
        vr = ValidationResult(passed=True, score=1.0, reason="ok")
        assert vr.check_scores == {}

    def test_custom_checks(self):
        vr = ValidationResult(
            passed=True,
            score=0.8,
            reason="ok",
            checks_run=["coverage", "coherence"],
            check_scores={"coverage": 0.8, "coherence": 0.9},
        )
        assert "coverage" in vr.checks_run
        assert vr.check_scores["coherence"] == 0.9


# ── top_terms() ───────────────────────────────────────────────────────────────


class TestTopTerms:
    def test_returns_list(self):
        result = top_terms("The quick brown fox jumps over the lazy dog.")
        assert isinstance(result, list)

    def test_empty_string(self):
        assert top_terms("") == []

    def test_returns_at_most_n(self):
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        result = top_terms(text, n=5)
        assert len(result) <= 5

    def test_stopwords_excluded(self):
        # "the" and "a" are stopwords
        result = top_terms("The the the a a a fox jumps quickly.")
        assert "the" not in result
        assert "a" not in result

    def test_meaningful_terms_returned(self):
        text = "Python programming language variables functions classes modules."
        result = top_terms(text, n=5)
        # At least some of these non-stopword words should surface
        assert len(result) > 0

    def test_unicode_text(self):
        # Should not raise
        result = top_terms("日本語テキスト処理テスト。")
        assert isinstance(result, list)

    def test_single_sentence(self):
        result = top_terms("Dogs bark loudly outside.", n=3)
        assert len(result) <= 3

    def test_large_input(self):
        text = "machine learning algorithms optimize parameters weights training " * 100
        result = top_terms(text, n=10)
        assert len(result) <= 10


# ── _check_coverage() ─────────────────────────────────────────────────────────


class TestCheckCoverage:
    def test_empty_original_passes(self):
        passed, score, reason = _check_coverage("", "anything")
        assert passed is True

    def test_acceptable_compression_passes(self):
        original = "x" * 1000
        compressed = "x" * 300  # 30% — within MIN_COVERAGE and MAX_COVERAGE
        passed, score, reason = _check_coverage(original, compressed)
        assert passed is True
        assert 0.0 <= score <= 1.0

    def test_over_compressed_fails(self):
        original = "x" * 1000
        compressed = "x"  # near 0% — below MIN_COVERAGE
        passed, score, reason = _check_coverage(original, compressed)
        assert passed is False
        assert "over-compressed" in reason

    def test_under_compressed_fails(self):
        original = "x" * 1000
        compressed = "x" * 990  # 99% — above MAX_COVERAGE
        passed, score, reason = _check_coverage(original, compressed)
        assert passed is False
        assert "under-compressed" in reason

    def test_score_in_range(self):
        original = "a" * 500
        compressed = "a" * 200
        _, score, _ = _check_coverage(original, compressed)
        assert 0.0 <= score <= 1.0


# ── _check_sentence_coherence() ──────────────────────────────────────────────


class TestCheckSentenceCoherence:
    def test_empty_string_passes(self):
        passed, score, reason = _check_sentence_coherence("")
        assert passed is True

    def test_normal_text_passes(self):
        # Average sentence length must be >= MIN_AVG_SENTENCE_LEN (5 words)
        text = "This is a normal and reasonable sentence. It has proper length and structure. Should pass easily enough."
        passed, score, reason = _check_sentence_coherence(text)
        assert passed is True

    def test_very_long_sentence_fails(self):
        long_sent = ("word " * (MAX_SENTENCE_LEN + 10)).strip() + "."
        passed, score, reason = _check_sentence_coherence(long_sent)
        assert passed is False

    def test_very_short_avg_fails(self):
        # Single word sentences
        text = "Hi. Ok. Sure. Yes. No. Go. Do."
        passed, score, reason = _check_sentence_coherence(text)
        # avg sentence length = 1 word, below MIN_AVG_SENTENCE_LEN
        assert passed is False

    def test_whitespace_only_passes(self):
        passed, score, reason = _check_sentence_coherence("   ")
        assert passed is True


# ── _check_key_terms() ────────────────────────────────────────────────────────


class TestCheckKeyTerms:
    def test_no_key_terms_in_empty_original_passes(self):
        passed, score, reason = _check_key_terms("", "any compressed text")
        assert passed is True

    def test_good_retention_passes(self):
        original = "Python machine learning algorithms deep neural networks training optimization."
        compressed = "Python machine learning algorithms neural networks training optimization."
        passed, score, reason = _check_key_terms(original, compressed)
        assert passed is True

    def test_poor_retention_fails(self):
        original = "Python machine learning algorithms deep neural networks training."
        compressed = "Short unrelated text about cooking recipes."
        passed, score, reason = _check_key_terms(original, compressed)
        assert passed is False

    def test_score_reflects_retention(self):
        original = "unique term beta unique term beta unique term beta testing."
        compressed = "unique term beta present in compressed."
        _, score, _ = _check_key_terms(original, compressed)
        assert 0.0 <= score <= 1.0


# ── _check_code_integrity() ───────────────────────────────────────────────────


class TestCheckCodeIntegrity:
    def test_no_fences_passes(self):
        original = "plain text no code"
        compressed = "plain text"
        passed, score, reason = _check_code_integrity(original, compressed)
        assert passed is True

    def test_balanced_fences_passes(self):
        compressed = "```python\nprint('hi')\n```"
        passed, score, reason = _check_code_integrity("orig", compressed)
        assert passed is True

    def test_unbalanced_fences_fails(self):
        compressed = "```python\nprint('hi')"  # missing closing
        passed, score, reason = _check_code_integrity("orig", compressed)
        assert passed is False
        assert "fence" in reason

    def test_indentation_destroyed_fails(self):
        # Original has many indented lines, compressed has none
        original = "\n".join(["    line"] * 20)
        compressed = "no indentation at all"
        passed, score, reason = _check_code_integrity(original, compressed)
        assert passed is False

    def test_indentation_preserved_passes(self):
        original = "\n".join(["    line"] * 10)
        compressed = "\n".join(["    line"] * 8)
        passed, score, reason = _check_code_integrity(original, compressed)
        assert passed is True


# ── _check_numeric_preservation() ────────────────────────────────────────────


class TestCheckNumericPreservation:
    def test_no_numbers_passes(self):
        passed, score, reason = _check_numeric_preservation("text", "text")
        assert passed is True

    def test_same_numbers_passes(self):
        passed, score, reason = _check_numeric_preservation("cost is $100", "cost is $100")
        assert passed is True

    def test_new_number_in_compressed_fails(self):
        original = "cost is $100"
        compressed = "cost is $200"  # changed number
        passed, score, reason = _check_numeric_preservation(original, compressed)
        assert passed is False

    def test_number_removed_from_compressed_passes(self):
        # Numbers missing from compressed are not flagged (only new/altered are)
        original = "values: 100, 200, 300"
        compressed = "values mentioned"
        passed, score, reason = _check_numeric_preservation(original, compressed)
        assert passed is True


# ── validate() ───────────────────────────────────────────────────────────────


class TestValidate:
    def test_returns_validation_result(self):
        original = "This is an original paragraph of reasonable length for testing purposes."
        compressed = "Original paragraph reasonable length testing purposes."
        result = validate(compressed, original, "NARRATIVE")
        assert isinstance(result, ValidationResult)

    def test_good_compression_passes(self):
        original = (
            "Python is a high-level programming language. "
            "It emphasizes code readability and simplicity. "
            "Many developers use Python for data science and automation. "
            "The language has extensive standard library support. "
            "Community contributions expand its capabilities greatly."
        )
        # Compress to ~40% — within allowed range
        compressed = (
            "Python high-level language emphasizes readability simplicity. "
            "Developers use Python data science automation. "
            "Extensive library support community contributions."
        )
        result = validate(compressed, original, "NARRATIVE")
        assert result.passed is True

    def test_over_compressed_fails(self):
        original = "x" * 500
        compressed = "x"
        result = validate(compressed, original, "NARRATIVE")
        assert result.passed is False

    def test_checks_run_populated(self):
        original = "some text here for testing purposes"
        compressed = "text testing purposes"
        result = validate(compressed, original, "NARRATIVE")
        assert len(result.checks_run) > 0

    def test_check_scores_populated(self):
        original = "some text for testing validation"
        compressed = "text testing validation"
        result = validate(compressed, original, "NARRATIVE")
        assert len(result.check_scores) > 0

    def test_code_risk_class_adds_code_integrity_check(self):
        original = "```python\nprint('hello')\n```"
        compressed = "```python\nprint('hello')\n```"
        result = validate(compressed, original, "CODE")
        assert "code_integrity" in result.checks_run

    def test_numeric_risk_class_adds_numeric_check(self):
        original = "The value is 42."
        compressed = "The value is 42."
        result = validate(compressed, original, "NUMERIC")
        assert "numeric_preservation" in result.checks_run

    def test_legal_risk_class_adds_numeric_check(self):
        original = "Clause 3.1 applies to all parties."
        compressed = "Clause 3.1 applies."
        result = validate(compressed, original, "LEGAL")
        assert "numeric_preservation" in result.checks_run

    def test_narrative_risk_class_no_code_check(self):
        original = "A narrative story about adventures."
        compressed = "Story about adventures."
        result = validate(compressed, original, "NARRATIVE")
        assert "code_integrity" not in result.checks_run

    def test_checks_config_disables_check(self):
        original = "some text"
        compressed = "x"  # would normally fail coverage
        result = validate(compressed, original, "NARRATIVE", checks_config={"coverage": False})
        assert "coverage" not in result.checks_run

    def test_score_between_0_and_1(self):
        original = "reasonable text content for validation testing"
        compressed = "reasonable text validation"
        result = validate(compressed, original, "NARRATIVE")
        assert 0.0 <= result.score <= 1.0

    def test_empty_original_no_crash(self):
        result = validate("compressed", "", "NARRATIVE")
        assert isinstance(result, ValidationResult)

    def test_empty_compressed_no_crash(self):
        result = validate("", "original text that exists", "NARRATIVE")
        assert isinstance(result, ValidationResult)

    def test_unicode_inputs(self):
        original = "日本語のテキスト処理テスト。機械学習アルゴリズム。"
        compressed = "日本語テキスト機械学習。"
        result = validate(compressed, original, "NARRATIVE")
        assert isinstance(result, ValidationResult)

    def test_reason_ok_on_pass(self):
        original = (
            "Machine learning systems require careful training data curation. "
            "Model evaluation metrics guide improvement strategies. "
            "Production deployment requires monitoring and alerting."
        )
        compressed = (
            "Machine learning training data curation. "
            "Evaluation metrics guide improvement. "
            "Deployment requires monitoring."
        )
        result = validate(compressed, original, "NARRATIVE")
        if result.passed:
            assert result.reason == "ok"

    def test_reason_set_on_failure(self):
        result = validate("x", "x" * 1000, "NARRATIVE")
        if not result.passed:
            assert result.reason != "ok"


# ── log_validation_result() ──────────────────────────────────────────────────


class TestLogValidationResult:
    def test_creates_log_file(self, tmp_path):
        log_path = str(tmp_path / "validation_log.json")
        vr = ValidationResult(passed=True, score=0.9, reason="ok", checks_run=["coverage"])
        log_validation_result(vr, "block-1", "kept", log_path=log_path)
        assert Path(log_path).exists()

    def test_log_entry_structure(self, tmp_path):
        log_path = str(tmp_path / "validation_log.json")
        vr = ValidationResult(passed=True, score=0.85, reason="ok", checks_run=["coverage"])
        log_validation_result(vr, "block-ref", "kept", log_path=log_path)
        entries = json.loads(Path(log_path).read_text())
        assert len(entries) == 1
        entry = entries[0]
        assert entry["block_ref"] == "block-ref"
        assert entry["action"] == "kept"
        assert entry["passed"] is True
        assert entry["score"] == 0.85
        assert "timestamp" in entry

    def test_appends_multiple_entries(self, tmp_path):
        log_path = str(tmp_path / "validation_log.json")
        vr = ValidationResult(passed=True, score=0.9, reason="ok")
        log_validation_result(vr, "b1", "kept", log_path=log_path)
        log_validation_result(vr, "b2", "kept", log_path=log_path)
        entries = json.loads(Path(log_path).read_text())
        assert len(entries) == 2

    def test_creates_parent_dirs(self, tmp_path):
        log_path = str(tmp_path / "deep" / "nested" / "log.json")
        vr = ValidationResult(passed=False, score=0.1, reason="coverage: low")
        log_validation_result(vr, "ref", "fallback_to_hybrid", log_path=log_path)
        assert Path(log_path).exists()

    def test_corrupted_log_overwritten(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        Path(log_path).write_text("NOT VALID JSON")
        vr = ValidationResult(passed=True, score=1.0, reason="ok")
        log_validation_result(vr, "ref", "kept", log_path=log_path)
        entries = json.loads(Path(log_path).read_text())
        assert len(entries) == 1


# ── apply_fallback() ──────────────────────────────────────────────────────────


class TestApplyFallback:
    def test_returns_tuple(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        original = "original text"
        compressed = "original text"
        text, action = apply_fallback(compressed, original, "NARRATIVE", log_path=log_path)
        assert isinstance(text, str)
        assert action in ("kept", "fallback_to_hybrid")

    def test_kept_action_returns_compressed(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        original = (
            "Python is a high-level programming language widely used in data science. "
            "Its simple syntax makes it accessible to beginners and experts alike. "
            "Many libraries are available for machine learning and automation tasks. "
            "The ecosystem continues to grow with active community contributions. "
            "Python runs on all major operating systems and platforms."
        )
        compressed = (
            "Python high-level language used data science. "
            "Simple syntax accessible beginners experts. "
            "Libraries available machine learning automation."
        )
        text, action = apply_fallback(compressed, original, "NARRATIVE", log_path=log_path)
        if action == "kept":
            assert text == compressed

    def test_fallback_action_returns_original(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        original = "x" * 500
        compressed = "x"  # over-compressed — validation will fail
        text, action = apply_fallback(compressed, original, "NARRATIVE", log_path=log_path)
        if action == "fallback_to_hybrid":
            assert text == original

    def test_writes_to_log(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        apply_fallback("short", "x" * 500, "NARRATIVE", log_path=log_path)
        entries = json.loads(Path(log_path).read_text())
        assert len(entries) >= 1


# ── get_validation_stats() ───────────────────────────────────────────────────


class TestGetValidationStats:
    def test_no_log_file_returns_zeros(self, tmp_path):
        log_path = str(tmp_path / "nonexistent.json")
        stats = get_validation_stats(log_path=log_path)
        assert stats["total_checked"] == 0
        assert stats["passed"] == 0
        assert stats["failed"] == 0
        assert stats["fallback_rate"] == 0.0
        assert stats["avg_score"] == 0.0
        assert stats["most_common_failure"] is None

    def test_stats_from_logged_results(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        vr_pass = ValidationResult(passed=True, score=0.9, reason="ok", checks_run=["coverage"])
        vr_fail = ValidationResult(
            passed=False, score=0.2, reason="coverage: over-compressed", checks_run=["coverage"]
        )
        log_validation_result(vr_pass, "b1", "kept", log_path=log_path)
        log_validation_result(vr_fail, "b2", "fallback_to_hybrid", log_path=log_path)
        stats = get_validation_stats(log_path=log_path)
        assert stats["total_checked"] == 2
        assert stats["passed"] == 1
        assert stats["failed"] == 1

    def test_fallback_rate_calculated(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        vr_pass = ValidationResult(passed=True, score=0.9, reason="ok")
        vr_fail = ValidationResult(passed=False, score=0.1, reason="coverage: low")
        log_validation_result(vr_pass, "b1", "kept", log_path=log_path)
        log_validation_result(vr_fail, "b2", "fallback_to_hybrid", log_path=log_path)
        stats = get_validation_stats(log_path=log_path)
        assert stats["fallback_rate"] == 0.5

    def test_avg_score_calculated(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        vr1 = ValidationResult(passed=True, score=0.8, reason="ok")
        vr2 = ValidationResult(passed=True, score=0.6, reason="ok")
        log_validation_result(vr1, "b1", "kept", log_path=log_path)
        log_validation_result(vr2, "b2", "kept", log_path=log_path)
        stats = get_validation_stats(log_path=log_path)
        assert abs(stats["avg_score"] - 0.7) < 0.01

    def test_most_common_failure_identified(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        for _ in range(3):
            vr = ValidationResult(passed=False, score=0.1, reason="coverage: too low")
            log_validation_result(vr, "b", "fallback_to_hybrid", log_path=log_path)
        vr2 = ValidationResult(passed=False, score=0.2, reason="coherence: short")
        log_validation_result(vr2, "b2", "fallback_to_hybrid", log_path=log_path)
        stats = get_validation_stats(log_path=log_path)
        assert stats["most_common_failure"] == "coverage"

    def test_corrupted_log_returns_zeros(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        Path(log_path).write_text("INVALID JSON")
        stats = get_validation_stats(log_path=log_path)
        assert stats["total_checked"] == 0

    def test_all_pass_no_most_common_failure(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        for _ in range(3):
            vr = ValidationResult(passed=True, score=0.9, reason="ok")
            log_validation_result(vr, "b", "kept", log_path=log_path)
        stats = get_validation_stats(log_path=log_path)
        assert stats["most_common_failure"] is None
