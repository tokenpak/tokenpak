"""Unit tests for tokenpak/benchmark.py — deep coverage.

Covers:
- BUILTIN_SAMPLES structure validation
- run_compression_benchmark (multiple scenarios)
- benchmark_tokenization
- benchmark_processing
- benchmark_indexing_baseline
- benchmark_indexing_optimized
- benchmark_search
- run_benchmark integration
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tokenpak"))

from tokenpak.benchmark import (
    BUILTIN_SAMPLES,
    run_compression_benchmark,
    benchmark_tokenization,
    benchmark_processing,
    benchmark_indexing_baseline,
    benchmark_indexing_optimized,
    benchmark_search,
    run_benchmark,
)


# ---------------------------------------------------------------------------
# BUILTIN_SAMPLES Tests
# ---------------------------------------------------------------------------


class TestBuiltinSamples:
    """Tests for BUILTIN_SAMPLES data structure."""

    def test_builtin_samples_is_list(self):
        """BUILTIN_SAMPLES should be a list."""
        assert isinstance(BUILTIN_SAMPLES, list)

    def test_builtin_samples_not_empty(self):
        """BUILTIN_SAMPLES should contain at least one sample."""
        assert len(BUILTIN_SAMPLES) > 0

    def test_builtin_samples_have_required_keys(self):
        """Each sample should have name, filename, file_type, content."""
        required_keys = {"name", "filename", "file_type", "content"}
        for sample in BUILTIN_SAMPLES:
            assert required_keys.issubset(sample.keys()), f"Sample {sample.get('name', 'unknown')} missing keys"

    def test_builtin_samples_content_not_empty(self):
        """Each sample content should not be empty."""
        for sample in BUILTIN_SAMPLES:
            assert sample["content"].strip(), f"Sample {sample['name']} has empty content"

    def test_builtin_samples_file_types_valid(self):
        """File types should be recognized types."""
        valid_types = {"code", "text", "data"}
        for sample in BUILTIN_SAMPLES:
            assert sample["file_type"] in valid_types, f"Invalid file_type for {sample['name']}"

    def test_builtin_samples_unique_names(self):
        """Each sample should have a unique name."""
        names = [s["name"] for s in BUILTIN_SAMPLES]
        assert len(names) == len(set(names)), "Duplicate sample names found"

    def test_builtin_samples_count(self):
        """Should have multiple samples for comprehensive coverage."""
        assert len(BUILTIN_SAMPLES) >= 5, "Expected at least 5 built-in samples"

    def test_builtin_samples_filenames_have_extensions(self):
        """Each sample filename should have a file extension."""
        for sample in BUILTIN_SAMPLES:
            assert "." in sample["filename"], f"Sample {sample['name']} filename has no extension"

    def test_builtin_samples_content_reasonable_length(self):
        """Sample content should be of reasonable length."""
        for sample in BUILTIN_SAMPLES:
            content = sample["content"]
            assert len(content) > 10, f"Sample {sample['name']} content too short"
            assert len(content) < 50000, f"Sample {sample['name']} content too long"


# ---------------------------------------------------------------------------
# run_compression_benchmark Tests
# ---------------------------------------------------------------------------


class TestRunCompressionBenchmark:
    """Tests for run_compression_benchmark function."""

    def test_samples_mode_uses_builtin_samples(self, capsys):
        """use_samples=True should run on built-in samples."""
        with patch("tokenpak.benchmark._run_single_compression_test") as mock_test:
            mock_test.return_value = {
                "name": "test",
                "filename": "test.py",
                "file_type": "code",
                "tokens_before": 100,
                "tokens_after": 80,
                "tokens_saved": 20,
                "compression_ratio_pct": 20.0,
                "time_ms": 1.5,
                "recipe_hits": [],
            }

            run_compression_benchmark(use_samples=True)

            assert mock_test.call_count == len(BUILTIN_SAMPLES)

    def test_file_mode_single_file(self, tmp_path, capsys):
        """file argument should benchmark a specific file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        with patch("tokenpak.benchmark._run_single_compression_test") as mock_test:
            mock_test.return_value = {
                "name": "test.py",
                "filename": str(test_file),
                "file_type": "code",
                "tokens_before": 10,
                "tokens_after": 10,
                "tokens_saved": 0,
                "compression_ratio_pct": 0.0,
                "time_ms": 0.5,
                "recipe_hits": [],
            }

            run_compression_benchmark(file=str(test_file))

            mock_test.assert_called_once()

    def test_file_not_found_error(self, capsys):
        """Non-existent file should print error."""
        run_compression_benchmark(file="/nonexistent/path.py")
        captured = capsys.readouterr()
        assert "Error: file not found" in captured.out

    def test_empty_file_error(self, tmp_path, capsys):
        """Empty file should print error."""
        empty_file = tmp_path / "empty.py"
        empty_file.write_text("")

        run_compression_benchmark(file=str(empty_file))
        captured = capsys.readouterr()
        assert "Error: file is empty" in captured.out

    def test_json_output_format(self, capsys):
        """as_json=True should output valid JSON."""
        with patch("tokenpak.benchmark._run_single_compression_test") as mock_test:
            mock_test.return_value = {
                "name": "test",
                "filename": "test.py",
                "file_type": "code",
                "tokens_before": 100,
                "tokens_after": 80,
                "tokens_saved": 20,
                "compression_ratio_pct": 20.0,
                "time_ms": 1.0,
                "recipe_hits": ["python-imports"],
            }

            run_compression_benchmark(use_samples=True, as_json=True)

            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert "tests" in data
            assert "summary" in data
            assert data["summary"]["total_tests"] == len(BUILTIN_SAMPLES)

    def test_json_output_has_summary_fields(self, capsys):
        """JSON output should include all summary fields."""
        with patch("tokenpak.benchmark._run_single_compression_test") as mock_test:
            mock_test.return_value = {
                "name": "test",
                "filename": "test.py",
                "file_type": "code",
                "tokens_before": 100,
                "tokens_after": 50,
                "tokens_saved": 50,
                "compression_ratio_pct": 50.0,
                "time_ms": 2.0,
                "recipe_hits": [],
            }

            run_compression_benchmark(use_samples=True, as_json=True)

            captured = capsys.readouterr()
            data = json.loads(captured.out)
            summary = data["summary"]
            assert "tokens_before" in summary
            assert "tokens_after" in summary
            assert "tokens_saved" in summary
            assert "overall_compression_pct" in summary
            assert "avg_time_ms" in summary

    def test_human_readable_output_has_header(self, capsys):
        """Human-readable output should include header."""
        with patch("tokenpak.benchmark._run_single_compression_test") as mock_test:
            mock_test.return_value = {
                "name": "test",
                "filename": "test.py",
                "file_type": "code",
                "tokens_before": 100,
                "tokens_after": 80,
                "tokens_saved": 20,
                "compression_ratio_pct": 20.0,
                "time_ms": 1.0,
                "recipe_hits": [],
            }

            run_compression_benchmark(use_samples=True, as_json=False)

            captured = capsys.readouterr()
            assert "TokenPak Compression Benchmark" in captured.out
            assert "TEST" in captured.out
            assert "TOTAL" in captured.out


# ---------------------------------------------------------------------------
# benchmark_tokenization Tests
# ---------------------------------------------------------------------------


class TestBenchmarkTokenization:
    """Tests for benchmark_tokenization function."""

    def test_empty_texts_returns_error(self):
        """Empty text list should return error dict."""
        result = benchmark_tokenization([])
        assert result == {"error": "no texts to benchmark"}

    def test_returns_expected_keys(self):
        """Result should have cold/warm cache metrics."""
        with patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.cache_info") as mock_info:
            mock_count.return_value = 10
            mock_info.return_value = "CacheInfo(hits=5, misses=3)"

            result = benchmark_tokenization(["text1", "text2"], iterations=1)

            assert "cold_cache_avg_ms" in result
            assert "warm_cache_avg_ms" in result
            assert "cache_speedup" in result
            assert "cache_info" in result

    def test_cache_speedup_calculated(self):
        """Cache speedup should be cold/warm ratio."""
        with patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.cache_info") as mock_info:
            mock_count.return_value = 10
            mock_info.return_value = "CacheInfo()"

            result = benchmark_tokenization(["text"], iterations=1)

            # Speedup should be > 0
            assert result["cache_speedup"] > 0

    def test_multiple_texts_processed(self):
        """Should process all texts in list."""
        with patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.cache_info") as mock_info:
            mock_count.return_value = 10
            mock_info.return_value = "CacheInfo()"
            texts = ["text1", "text2", "text3"]

            result = benchmark_tokenization(texts, iterations=1)

            # count_tokens called for each text twice (cold + warm)
            assert mock_count.call_count >= len(texts)


# ---------------------------------------------------------------------------
# benchmark_processing Tests
# ---------------------------------------------------------------------------


class TestBenchmarkProcessing:
    """Tests for benchmark_processing function."""

    def test_groups_files_by_type(self, tmp_path):
        """Files should be grouped and processed by type."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os")
        md_file = tmp_path / "readme.md"
        md_file.write_text("# Hello")

        files = [
            (str(py_file), "code", 10),
            (str(md_file), "text", 7),
        ]

        with patch("tokenpak.benchmark.get_processor") as mock_proc:
            mock_processor = MagicMock()
            mock_processor.process.return_value = "processed"
            mock_proc.return_value = mock_processor

            result = benchmark_processing(files, iterations=1)

            # Should have entries for each type
            assert "code" in result or "text" in result

    def test_no_processor_skips_type(self, tmp_path):
        """Types without processors should be skipped."""
        test_file = tmp_path / "test.xyz"
        test_file.write_text("data")

        files = [(str(test_file), "unknown_type", 4)]

        with patch("tokenpak.benchmark.get_processor") as mock_proc:
            mock_proc.return_value = None

            result = benchmark_processing(files, iterations=1)

            assert "unknown_type" not in result

    def test_returns_timing_metrics(self, tmp_path):
        """Result should include per-file timing metrics."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        files = [(str(test_file), "code", 5)]

        with patch("tokenpak.benchmark.get_processor") as mock_proc:
            mock_processor = MagicMock()
            mock_processor.process.return_value = "x = 1"
            mock_proc.return_value = mock_processor

            result = benchmark_processing(files, iterations=1)

            if "code" in result:
                assert "total_ms" in result["code"]
                assert "per_file_ms" in result["code"]
                assert "files" in result["code"]


# ---------------------------------------------------------------------------
# benchmark_indexing_baseline Tests
# ---------------------------------------------------------------------------


class TestBenchmarkIndexingBaseline:
    """Tests for benchmark_indexing_baseline function."""

    def test_returns_expected_keys(self, tmp_path):
        """Result should have file/time metrics."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens_uncached") as mock_count:
            mock_walk.return_value = [(str(test_file), "code", 5)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "x = 1"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 5

            result = benchmark_indexing_baseline(str(tmp_path), iterations=1)

            assert "total_files" in result
            assert "total_ms" in result
            assert "per_file_ms" in result
            assert "files_per_second" in result

    def test_handles_empty_directory(self, tmp_path):
        """Should handle directories with no processable files."""
        with patch("tokenpak.benchmark.walk_directory") as mock_walk:
            mock_walk.return_value = []

            result = benchmark_indexing_baseline(str(tmp_path), iterations=1)

            assert result["total_files"] == 0


# ---------------------------------------------------------------------------
# benchmark_indexing_optimized Tests
# ---------------------------------------------------------------------------


class TestBenchmarkIndexingOptimized:
    """Tests for benchmark_indexing_optimized function."""

    def test_returns_expected_keys(self, tmp_path):
        """Result should have file/time metrics."""
        test_file = tmp_path / "test.py"
        test_file.write_text("y = 2")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.BlockRegistry") as mock_registry_cls:
            mock_walk.return_value = [(str(test_file), "code", 5)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "y = 2"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 5

            mock_registry = MagicMock()
            mock_registry.batch_transaction.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_registry.batch_transaction.return_value.__exit__ = MagicMock(return_value=False)
            mock_registry_cls.return_value = mock_registry

            result = benchmark_indexing_optimized(str(tmp_path), iterations=1)

            assert "total_files" in result
            assert "total_ms" in result
            assert "per_file_ms" in result
            assert "files_per_second" in result

    def test_uses_batch_transaction(self, tmp_path):
        """Should use batch transaction for efficiency."""
        test_file = tmp_path / "test.py"
        test_file.write_text("z = 3")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.BlockRegistry") as mock_registry_cls:
            mock_walk.return_value = [(str(test_file), "code", 5)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "z = 3"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 5

            mock_registry = MagicMock()
            mock_registry.batch_transaction.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_registry.batch_transaction.return_value.__exit__ = MagicMock(return_value=False)
            mock_registry_cls.return_value = mock_registry

            benchmark_indexing_optimized(str(tmp_path), iterations=1)

            mock_registry.batch_transaction.assert_called()


# ---------------------------------------------------------------------------
# benchmark_search Tests
# ---------------------------------------------------------------------------


class TestBenchmarkSearch:
    """Tests for benchmark_search function."""

    def test_empty_queries_returns_error(self):
        """Empty query list should return error."""
        mock_registry = MagicMock()
        result = benchmark_search(mock_registry, [])
        assert result == {"error": "no queries"}

    def test_returns_expected_keys(self):
        """Result should have query metrics."""
        mock_registry = MagicMock()
        mock_registry.search.return_value = []

        result = benchmark_search(mock_registry, ["test", "query"], iterations=1)

        assert "queries" in result
        assert "total_ms" in result
        assert "per_query_ms" in result
        assert result["queries"] == 2

    def test_calls_search_for_each_query(self):
        """Should call registry.search for each query."""
        mock_registry = MagicMock()
        mock_registry.search.return_value = []
        queries = ["import", "class", "def"]

        benchmark_search(mock_registry, queries, iterations=1)

        assert mock_registry.search.call_count == len(queries)

    def test_multiple_iterations(self):
        """Should run multiple iterations when specified."""
        mock_registry = MagicMock()
        mock_registry.search.return_value = []
        queries = ["test"]
        iterations = 3

        benchmark_search(mock_registry, queries, iterations=iterations)

        # Should be called once per query per iteration
        assert mock_registry.search.call_count == len(queries) * iterations


# ---------------------------------------------------------------------------
# run_benchmark Integration Tests
# ---------------------------------------------------------------------------


class TestRunBenchmark:
    """Integration tests for run_benchmark function."""

    def test_prints_summary_output(self, tmp_path, capsys):
        """Should print benchmark summary."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nprint('hello')")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.count_tokens_uncached") as mock_count_uncached, \
             patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.cache_info") as mock_info, \
             patch("tokenpak.benchmark.BlockRegistry") as mock_registry_cls:

            mock_walk.return_value = [(str(test_file), "code", 20)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "import os"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 10
            mock_count_uncached.return_value = 10
            mock_info.return_value = "CacheInfo()"

            mock_registry = MagicMock()
            mock_registry.batch_transaction.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_registry.batch_transaction.return_value.__exit__ = MagicMock(return_value=False)
            mock_registry.search.return_value = []
            mock_registry_cls.return_value = mock_registry

            run_benchmark(str(tmp_path), iterations=1, compare=False)

            captured = capsys.readouterr()
            assert "TokenPak Latency Benchmark" in captured.out
            assert "SUMMARY" in captured.out

    def test_compare_mode_shows_speedup(self, tmp_path, capsys):
        """compare=True should show baseline vs optimized."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.count_tokens_uncached") as mock_count_uncached, \
             patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.cache_info") as mock_info, \
             patch("tokenpak.benchmark.BlockRegistry") as mock_registry_cls:

            mock_walk.return_value = [(str(test_file), "code", 5)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "x = 1"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 5
            mock_count_uncached.return_value = 5
            mock_info.return_value = "CacheInfo()"

            mock_registry = MagicMock()
            mock_registry.batch_transaction.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_registry.batch_transaction.return_value.__exit__ = MagicMock(return_value=False)
            mock_registry.search.return_value = []
            mock_registry_cls.return_value = mock_registry

            run_benchmark(str(tmp_path), iterations=1, compare=True)

            captured = capsys.readouterr()
            assert "baseline" in captured.out.lower()
            assert "SPEEDUP" in captured.out or "speedup" in captured.out.lower()

    def test_shows_found_files_count(self, tmp_path, capsys):
        """Should show number of files found."""
        test_file = tmp_path / "test.py"
        test_file.write_text("a = 1")

        with patch("tokenpak.benchmark.walk_directory") as mock_walk, \
             patch("tokenpak.benchmark.get_processor") as mock_proc, \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.count_tokens_uncached") as mock_count_uncached, \
             patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.cache_info") as mock_info, \
             patch("tokenpak.benchmark.BlockRegistry") as mock_registry_cls:

            mock_walk.return_value = [(str(test_file), "code", 5)]
            mock_processor = MagicMock()
            mock_processor.process.return_value = "a = 1"
            mock_proc.return_value = mock_processor
            mock_count.return_value = 5
            mock_count_uncached.return_value = 5
            mock_info.return_value = "CacheInfo()"

            mock_registry = MagicMock()
            mock_registry.batch_transaction.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_registry.batch_transaction.return_value.__exit__ = MagicMock(return_value=False)
            mock_registry.search.return_value = []
            mock_registry_cls.return_value = mock_registry

            run_benchmark(str(tmp_path), iterations=1, compare=False)

            captured = capsys.readouterr()
            assert "Found 1 files" in captured.out


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests."""

    def test_benchmark_tokenization_single_text(self):
        """Should handle single text input."""
        with patch("tokenpak.benchmark.clear_cache"), \
             patch("tokenpak.benchmark.count_tokens") as mock_count, \
             patch("tokenpak.benchmark.cache_info") as mock_info:
            mock_count.return_value = 5
            mock_info.return_value = "CacheInfo()"

            result = benchmark_tokenization(["single"], iterations=1)

            assert "cold_cache_avg_ms" in result
            assert "warm_cache_avg_ms" in result

    def test_benchmark_search_single_query(self):
        """Should handle single query."""
        mock_registry = MagicMock()
        mock_registry.search.return_value = []

        result = benchmark_search(mock_registry, ["single"], iterations=1)

        assert result["queries"] == 1
        assert "per_query_ms" in result

    def test_file_whitespace_only_error(self, tmp_path, capsys):
        """File with only whitespace should print error."""
        ws_file = tmp_path / "whitespace.py"
        ws_file.write_text("   \n\t\n   ")

        run_compression_benchmark(file=str(ws_file))
        captured = capsys.readouterr()
        assert "Error: file is empty" in captured.out
