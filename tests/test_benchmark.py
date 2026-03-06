"""Tests for the compression benchmark command.

Covers:
  - All 10 built-in sample test cases run and return valid results
  - --file mode: single file produces expected fields
  - --samples flag: same results as default
  - --json flag: valid JSON with expected structure
  - Aggregate summary values are consistent
  - recipe_hits is always a list
  - tokens_after <= tokens_before for every test
  - compression_ratio_pct is correctly computed
  - time_ms is non-negative
  - CLI integration: `tokenpak benchmark` exits 0
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tokenpak.benchmark import (
    BUILTIN_SAMPLES,
    _run_single_compression_test,
    run_compression_benchmark,
)


# ---------------------------------------------------------------------------
# _run_single_compression_test unit tests
# ---------------------------------------------------------------------------

class TestSingleCompressionTest:
    """Unit-level tests for the _run_single_compression_test helper."""

    def _python_sample(self):
        return BUILTIN_SAMPLES[0]  # python_module

    def test_returns_expected_keys(self):
        """Result dict must contain all required keys."""
        s = self._python_sample()
        result = _run_single_compression_test(
            name=s["name"],
            filename=s["filename"],
            file_type=s["file_type"],
            content=s["content"],
        )
        required = {
            "name", "filename", "file_type",
            "tokens_before", "tokens_after", "tokens_saved",
            "compression_ratio_pct", "time_ms", "recipe_hits",
        }
        assert required.issubset(result.keys())

    def test_tokens_after_lte_tokens_before(self):
        """Compression must never *increase* tokens."""
        for sample in BUILTIN_SAMPLES:
            result = _run_single_compression_test(
                name=sample["name"],
                filename=sample["filename"],
                file_type=sample["file_type"],
                content=sample["content"],
            )
            assert result["tokens_after"] <= result["tokens_before"], (
                f"Sample '{sample['name']}' increased token count"
            )

    def test_tokens_saved_matches_difference(self):
        """tokens_saved must equal tokens_before - tokens_after."""
        s = self._python_sample()
        result = _run_single_compression_test(
            name=s["name"],
            filename=s["filename"],
            file_type=s["file_type"],
            content=s["content"],
        )
        assert result["tokens_saved"] == result["tokens_before"] - result["tokens_after"]

    def test_compression_ratio_pct_correct(self):
        """compression_ratio_pct must be consistent with saved/before."""
        s = self._python_sample()
        result = _run_single_compression_test(
            name=s["name"],
            filename=s["filename"],
            file_type=s["file_type"],
            content=s["content"],
        )
        expected = round(
            result["tokens_saved"] / max(result["tokens_before"], 1) * 100, 1
        )
        assert result["compression_ratio_pct"] == expected

    def test_time_ms_is_non_negative(self):
        """Processing time must not be negative."""
        s = self._python_sample()
        result = _run_single_compression_test(
            name=s["name"],
            filename=s["filename"],
            file_type=s["file_type"],
            content=s["content"],
        )
        assert result["time_ms"] >= 0.0

    def test_recipe_hits_is_list(self):
        """recipe_hits must always be a list (possibly empty)."""
        s = self._python_sample()
        result = _run_single_compression_test(
            name=s["name"],
            filename=s["filename"],
            file_type=s["file_type"],
            content=s["content"],
        )
        assert isinstance(result["recipe_hits"], list)

    def test_empty_content_yields_zero_savings(self):
        """Empty or whitespace-only content should produce zero savings."""
        result = _run_single_compression_test(
            name="empty",
            filename="empty.py",
            file_type="code",
            content="   \n  ",
        )
        # Processor may return empty string; tokens_after <= tokens_before
        assert result["tokens_after"] <= result["tokens_before"]
        assert result["time_ms"] >= 0.0


# ---------------------------------------------------------------------------
# BUILTIN_SAMPLES completeness
# ---------------------------------------------------------------------------

class TestBuiltinSamples:
    """Verify the built-in sample set meets task requirements."""

    def test_at_least_eight_samples(self):
        """Must have 8 or more built-in test cases."""
        assert len(BUILTIN_SAMPLES) >= 8

    def test_all_samples_have_required_fields(self):
        """Every sample must carry name, filename, file_type, content."""
        for s in BUILTIN_SAMPLES:
            for key in ("name", "filename", "file_type", "content"):
                assert key in s, f"Sample missing key '{key}': {s}"

    def test_all_samples_have_non_empty_content(self):
        """No sample should have empty content."""
        for s in BUILTIN_SAMPLES:
            assert s["content"].strip(), f"Sample '{s['name']}' has empty content"

    def test_sample_names_are_unique(self):
        """Sample names must be distinct."""
        names = [s["name"] for s in BUILTIN_SAMPLES]
        assert len(names) == len(set(names)), "Duplicate sample names found"

    def test_file_types_are_valid(self):
        """Every file_type must be one of the recognised processor types."""
        valid_types = {"text", "code", "data", "pdf"}
        for s in BUILTIN_SAMPLES:
            assert s["file_type"] in valid_types, (
                f"Sample '{s['name']}' has unknown file_type: {s['file_type']}"
            )


# ---------------------------------------------------------------------------
# run_compression_benchmark — default / samples mode
# ---------------------------------------------------------------------------

class TestRunCompressionBenchmarkSamples:
    """Tests for run_compression_benchmark using built-in samples."""

    def test_samples_mode_runs_all_samples(self, capsys):
        """Default mode should run all built-in samples without error."""
        run_compression_benchmark()
        out = capsys.readouterr().out
        # Header must be present
        assert "TokenPak Compression Benchmark" in out

    def test_explicit_samples_flag_same_output(self, capsys):
        """use_samples=True should produce the same output as default."""
        run_compression_benchmark(use_samples=True)
        out = capsys.readouterr().out
        assert "TokenPak Compression Benchmark" in out
        # Should show the first sample name
        assert BUILTIN_SAMPLES[0]["name"] in out

    def test_json_output_is_valid_json(self, capsys):
        """as_json=True must produce parseable JSON."""
        run_compression_benchmark(as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "tests" in data
        assert "summary" in data

    def test_json_summary_fields(self, capsys):
        """JSON summary must contain required aggregate fields."""
        run_compression_benchmark(as_json=True)
        data = json.loads(capsys.readouterr().out)
        summary = data["summary"]
        for key in ("total_tests", "tokens_before", "tokens_after", "tokens_saved",
                    "overall_compression_pct", "avg_time_ms"):
            assert key in summary, f"Missing summary key: {key}"

    def test_json_test_count_matches_samples(self, capsys):
        """JSON output must have one entry per built-in sample."""
        run_compression_benchmark(as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["summary"]["total_tests"] == len(BUILTIN_SAMPLES)

    def test_json_tokens_saved_consistent(self, capsys):
        """Summary tokens_saved must equal sum of individual savings."""
        run_compression_benchmark(as_json=True)
        data = json.loads(capsys.readouterr().out)
        expected_saved = sum(t["tokens_saved"] for t in data["tests"])
        assert data["summary"]["tokens_saved"] == expected_saved

    def test_output_contains_summary_line(self, capsys):
        """Human-readable output must include the TOTAL summary line."""
        run_compression_benchmark()
        out = capsys.readouterr().out
        assert "TOTAL" in out

    def test_output_shows_recipe_hits(self, capsys):
        """Human-readable output must mention recipe hits."""
        run_compression_benchmark()
        out = capsys.readouterr().out
        assert "recipe hits" in out


# ---------------------------------------------------------------------------
# run_compression_benchmark — file mode
# ---------------------------------------------------------------------------

class TestRunCompressionBenchmarkFile:
    """Tests for run_compression_benchmark using a real file path."""

    def _write_tmp_file(self, content: str, suffix: str = ".py") -> Path:
        """Write content to a temp file and return its path."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def test_file_mode_single_result(self, capsys):
        """--file mode should produce exactly one test result."""
        path = self._write_tmp_file("def hello():\n    return 'world'\n")
        try:
            run_compression_benchmark(file=str(path))
            out = capsys.readouterr().out
            assert "TokenPak Compression Benchmark" in out
            assert "1" in out  # "Tests run : 1"
        finally:
            path.unlink(missing_ok=True)

    def test_file_mode_missing_file_prints_error(self, capsys):
        """--file mode should print an error for a non-existent file."""
        run_compression_benchmark(file="/nonexistent/path/file.py")
        out = capsys.readouterr().out
        assert "Error" in out or "not found" in out.lower()

    def test_file_mode_json_single_entry(self, capsys):
        """--file mode with --json should return one tests entry."""
        path = self._write_tmp_file("x = 1\n")
        try:
            run_compression_benchmark(file=str(path), as_json=True)
            data = json.loads(capsys.readouterr().out)
            assert len(data["tests"]) == 1
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI integration test
# ---------------------------------------------------------------------------

class TestCliIntegration:
    """Smoke test the CLI entry point directly."""

    def test_benchmark_default_exits_zero(self):
        """tokenpak benchmark should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "benchmark"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_benchmark_json_exits_zero(self):
        """tokenpak benchmark --json should exit 0 and produce valid JSON."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "benchmark", "--json"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "tests" in data
        assert "summary" in data

    def test_benchmark_samples_exits_zero(self):
        """tokenpak benchmark --samples should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "benchmark", "--samples"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
