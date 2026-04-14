"""Unit tests for citation_tracker.py — LLM citation detection and utility scoring."""

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.telemetry.citation_tracker import (
    track_citations,
    update_utility,
    get_utility_score,
    get_utility_weight,
    _extract_identifiers,
    _load_utility,
    _save_utility,
    CITE_DELTA,
    DECAY_DELTA,
    SCORE_MAX,
    SCORE_MIN,
    MIN_MATCH_LEN,
    DEFAULT_UTILITY_PATH,
)


class TestExtractIdentifiers:
    """Tests for _extract_identifiers helper."""

    def test_extract_python_functions(self):
        """Extract Python function names."""
        code = "def authenticate(user): pass\ndef validate_token(token): pass"
        identifiers = _extract_identifiers(code)
        assert "authenticate" in identifiers
        assert "validate_token" in identifiers

    def test_extract_python_classes(self):
        """Extract Python class names."""
        code = "class UserManager: pass\nclass TokenCache: pass"
        identifiers = _extract_identifiers(code)
        assert "UserManager" in identifiers
        assert "TokenCache" in identifiers

    def test_extract_js_functions(self):
        """Extract JavaScript function names."""
        code = "function fetchData() {}\nconst processItem = () => {}"
        identifiers = _extract_identifiers(code)
        assert "fetchData" in identifiers
        assert "processItem" in identifiers

    def test_extract_file_paths(self):
        """Extract file path references."""
        code = "see src/auth.py for details\ncheck ./utils/helper.js"
        identifiers = _extract_identifiers(code)
        assert "src/auth.py" in identifiers
        assert "./utils/helper.js" in identifiers

    def test_extract_short_identifiers_filtered(self):
        """Identifiers < 3 chars should be filtered."""
        code = "def x(): pass\ndef abc(): pass\nclass AB: pass\nclass ABC: pass"
        identifiers = _extract_identifiers(code)
        assert "x" not in identifiers
        assert "AB" not in identifiers
        assert "abc" in identifiers
        assert "ABC" in identifiers

    def test_extract_empty_string(self):
        """Empty content should return empty list."""
        identifiers = _extract_identifiers("")
        assert identifiers == []


class TestTrackCitations:
    """Tests for track_citations function."""

    def test_exact_substring_match(self):
        """Citation detected via exact substring match."""
        response = "The algorithm uses def quicksort(arr): return sorted(arr)"
        context = [
            {
                "slice_id": "func_quicksort",
                "content": "def quicksort(arr): return sorted(arr)",
                "ref": "algorithms.py",
            }
        ]
        cited = track_citations(response, context)
        assert "func_quicksort" in cited

    def test_file_path_match(self):
        """Citation detected via file path mention."""
        response = "As shown in src/auth.py, the authentication flow is implemented"
        context = [
            {
                "slice_id": "auth_flow",
                "content": "authentication logic here",
                "ref": "src/auth.py",
            }
        ]
        cited = track_citations(response, context)
        assert "auth_flow" in cited

    def test_identifier_match(self):
        """Citation detected via function/class name mention."""
        response = "The UserManager class handles all user operations and validation"
        context = [
            {
                "slice_id": "class_usermgr",
                "content": "class UserManager:\n    def create(self): pass",
                "ref": "managers.py",
            }
        ]
        cited = track_citations(response, context)
        assert "class_usermgr" in cited

    def test_no_match_returns_empty(self):
        """No citations should return empty list."""
        response = "This response talks about something completely different"
        context = [
            {
                "slice_id": "unused",
                "content": "def quicksort(arr): pass",
                "ref": "algorithms.py",
            }
        ]
        cited = track_citations(response, context)
        assert cited == []

    def test_multiple_citations(self):
        """Multiple context slices can be cited."""
        response = "The UserManager class in src/auth.py uses def authenticate(user): pass"
        context = [
            {"slice_id": "mgr", "content": "class UserManager: pass", "ref": "managers.py"},
            {"slice_id": "auth", "content": "def authenticate(user): pass", "ref": "src/auth.py"},
        ]
        cited = track_citations(response, context)
        assert "mgr" in cited
        assert "auth" in cited

    def test_substring_must_meet_min_length(self):
        """Substring < MIN_MATCH_LEN should not trigger match."""
        short_content = "x = 1"  # Only 5 chars
        response = "The result is x = 1 and that's it"
        context = [
            {
                "slice_id": "short",
                "content": short_content,
                "ref": "data.py",
            }
        ]
        cited = track_citations(response, context)
        # Should not match because content < MIN_MATCH_LEN (20)
        assert "short" not in cited

    def test_missing_slice_id_skipped(self):
        """Context without slice_id should be skipped."""
        response = "mentions UserManager"
        context = [
            {"slice_id": "", "content": "class UserManager: pass", "ref": "mgr.py"},
            {
                "slice_id": "valid",
                "content": "class TokenValidator: pass",
                "ref": "valid.py",
            },
        ]
        cited = track_citations(response, context)
        assert "valid" not in cited  # TokenValidator not mentioned
        # First entry skipped (no slice_id)

    def test_word_boundary_identifier_match(self):
        """Identifier match should use word boundaries."""
        response = "The process method is defined"
        context = [
            {
                "slice_id": "method_process",
                "content": "def process(data): pass",
                "ref": "utils.py",
            }
        ]
        cited = track_citations(response, context)
        assert "method_process" in cited

    def test_empty_context_list(self):
        """Empty context list should return empty cited list."""
        response = "Any response"
        context = []
        cited = track_citations(response, context)
        assert cited == []


class TestUtilityStore:
    """Tests for utility store operations (_load_utility, _save_utility)."""

    def test_save_and_load_utility(self):
        """Save and load utility data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            data = {
                "slice1": {"score": 7.5, "hits": 2, "misses": 1},
                "slice2": {"score": 3.0, "hits": 0, "misses": 5},
            }
            _save_utility(data, utility_path)
            loaded = _load_utility(utility_path)
            assert loaded == data

    def test_load_missing_file(self):
        """Loading missing file should return empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "missing.json")
            loaded = _load_utility(utility_path)
            assert loaded == {}

    def test_load_malformed_json(self):
        """Loading malformed JSON should return empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "bad.json")
            Path(utility_path).write_text("not valid json {")
            loaded = _load_utility(utility_path)
            assert loaded == {}

    def test_save_creates_parent_dirs(self):
        """Saving should create parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "deep" / "nested" / "utility.json")
            data = {"slice1": {"score": 5.0}}
            _save_utility(data, utility_path)
            assert Path(utility_path).exists()


class TestUpdateUtility:
    """Tests for update_utility function."""

    def test_update_cited_block_increases_score(self):
        """Cited block should increase score by CITE_DELTA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            cited_ids = ["slice1"]
            all_ids = ["slice1", "slice2"]
            result = update_utility(cited_ids, all_ids, utility_path)
            assert result["slice1"]["score"] == 5.0 + CITE_DELTA
            assert result["slice1"]["hits"] == 1

    def test_update_uncited_block_decreases_score(self):
        """Uncited block should decrease score by DECAY_DELTA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            cited_ids = ["slice1"]
            all_ids = ["slice1", "slice2"]
            result = update_utility(cited_ids, all_ids, utility_path)
            assert result["slice2"]["score"] == 5.0 - DECAY_DELTA
            assert result["slice2"]["misses"] == 1

    def test_update_score_clamped_to_max(self):
        """Score should be clamped to SCORE_MAX."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            # Pre-load with high score
            data = {"slice1": {"score": SCORE_MAX - 0.1, "hits": 0, "misses": 0}}
            _save_utility(data, utility_path)
            # Cite it (would exceed max)
            result = update_utility(["slice1"], ["slice1"], utility_path)
            assert result["slice1"]["score"] == SCORE_MAX

    def test_update_score_clamped_to_min(self):
        """Score should be clamped to SCORE_MIN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            # Pre-load with low score
            data = {"slice1": {"score": 0.05, "hits": 0, "misses": 0}}
            _save_utility(data, utility_path)
            # Don't cite it (would go below min)
            result = update_utility([], ["slice1"], utility_path)
            assert result["slice1"]["score"] == SCORE_MIN

    def test_update_persists_to_file(self):
        """Updates should be persisted to utility_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            update_utility(["slice1"], ["slice1", "slice2"], utility_path)
            # Verify by loading from disk
            loaded = _load_utility(utility_path)
            assert "slice1" in loaded
            assert "slice2" in loaded

    def test_update_multiple_calls_accumulate(self):
        """Multiple updates should accumulate hits/misses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            # First update
            update_utility(["slice1"], ["slice1"], utility_path)
            # Second update
            result = update_utility(["slice1"], ["slice1"], utility_path)
            assert result["slice1"]["hits"] == 2


class TestGetUtilityScore:
    """Tests for get_utility_score function."""

    def test_get_existing_score(self):
        """Get score for existing slice_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            data = {"slice1": {"score": 7.5}}
            _save_utility(data, utility_path)
            score = get_utility_score("slice1", utility_path)
            assert score == 7.5

    def test_get_missing_slice_returns_neutral(self):
        """Get score for non-existent slice_id returns 5.0 (neutral)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            score = get_utility_score("nonexistent", utility_path)
            assert score == 5.0

    def test_get_from_missing_file_returns_neutral(self):
        """Get score from missing file returns 5.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "missing.json")
            score = get_utility_score("any_id", utility_path)
            assert score == 5.0


class TestGetUtilityWeight:
    """Tests for get_utility_weight function."""

    def test_neutral_score_weight(self):
        """score=5.0 should yield weight=1.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            data = {"slice1": {"score": 5.0}}
            _save_utility(data, utility_path)
            weight = get_utility_weight("slice1", utility_path)
            assert weight == 1.0

    def test_max_score_weight(self):
        """score=10.0 should yield weight=2.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            data = {"slice1": {"score": 10.0}}
            _save_utility(data, utility_path)
            weight = get_utility_weight("slice1", utility_path)
            assert weight == 2.0

    def test_min_score_weight(self):
        """score=0.0 should yield weight=0.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            data = {"slice1": {"score": 0.0}}
            _save_utility(data, utility_path)
            weight = get_utility_weight("slice1", utility_path)
            assert weight == 0.0

    def test_missing_slice_weight(self):
        """Missing slice_id should return weight=1.0 (neutral)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")
            weight = get_utility_weight("nonexistent", utility_path)
            assert weight == 1.0


class TestIntegration:
    """Integration tests combining citation tracking and utility scoring."""

    def test_full_workflow(self):
        """Track citations, update utility, and verify scores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            utility_path = str(Path(tmpdir) / "utility.json")

            # Set up context
            context = [
                {
                    "slice_id": "auth_func",
                    "content": "def authenticate(user): pass\n    returns boolean",
                    "ref": "auth.py",
                },
                {
                    "slice_id": "cache_class",
                    "content": "class TokenCache: pass",
                    "ref": "cache.py",
                },
            ]

            # LLM response cites auth_func
            response = "The authenticate function in auth.py validates credentials"
            cited = track_citations(response, context)
            assert "auth_func" in cited
            assert "cache_class" not in cited

            # Update utility
            all_ids = ["auth_func", "cache_class"]
            result = update_utility(cited, all_ids, utility_path)

            # auth_func should be boosted
            assert result["auth_func"]["score"] > 5.0
            # cache_class should be decayed
            assert result["cache_class"]["score"] < 5.0

            # Get weights
            auth_weight = get_utility_weight("auth_func", utility_path)
            cache_weight = get_utility_weight("cache_class", utility_path)
            assert auth_weight > 1.0
            assert cache_weight < 1.0
