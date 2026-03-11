"""Tests for tokenpak preview command."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


class TestPreviewCommand:
    """Test preview command functionality."""

    def test_preview_with_text_input(self):
        """Test preview with direct text input."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "hello world"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Input:" in result.stdout
        assert "Compressed:" in result.stdout
        assert "Savings:" in result.stdout

    def test_preview_json_output(self):
        """Test preview with JSON output."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test data", "--json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "input_tokens" in data
        assert "output_tokens" in data
        assert "saved_tokens" in data
        assert "compression_ratio" in data
        assert "retained_blocks" in data
        assert "removed_blocks" in data

    def test_preview_raw_output(self):
        """Test preview with raw output format."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test data", "--raw"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Input:" in result.stdout
        assert "Output:" in result.stdout
        assert "Saved:" in result.stdout
        assert "Retained blocks:" in result.stdout
        assert "Removed blocks:" in result.stdout

    def test_preview_with_file(self):
        """Test preview reading from file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("This is test content for preview")
            temp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, "-m", "tokenpak", "preview", "--file", temp_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "Input:" in result.stdout
            assert "Compressed:" in result.stdout
        finally:
            Path(temp_path).unlink()

    def test_preview_verbose_output(self):
        """Test preview with verbose output."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test", "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Mode:" in result.stdout
        assert "Flags:" in result.stdout

    def test_preview_no_input_error(self):
        """Test preview with no input returns error."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview"],
            input="",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "No input provided" in result.stderr or "No input provided" in result.stdout
