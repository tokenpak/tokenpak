# SPDX-License-Identifier: Apache-2.0
"""CLI plumbing tests for `tokenpak preview`.

Scope is deliberately narrow: argument handling and output-mode wiring.
Value correctness lives in ``tests/test_preview_semantics.py`` — this file
previously asserted only that certain keys and labels were present, which is
why a fabricated implementation passed it for so long. Shape assertions belong
here, on shape; they must not stand in for semantic coverage.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


class TestPreviewCommand:
    """Argument plumbing and output modes."""

    def test_preview_with_text_input(self):
        """Positional text input renders the measured summary."""
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
        """--json emits the measured-result contract."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test data", "--json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["state"] == "measured"
        for key in (
            "input_tokens",
            "output_tokens",
            "saved_tokens",
            "compression_ratio",
            "duration_ms",
            "applied",
            "blocks",
            "provenance",
        ):
            assert key in data, f"missing contract field {key}"
        # The simulation's invented fields must not come back.
        assert "retained_blocks" not in data
        assert "removed_blocks" not in data
        assert "flags" not in data

    def test_preview_raw_output(self):
        """--raw prints the flat form with the applied verdict."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test data", "--raw"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Input:" in result.stdout
        assert "Output:" in result.stdout
        assert "Saved:" in result.stdout
        assert "Applied:" in result.stdout
        assert "Blocks:" in result.stdout

    def test_preview_with_file(self):
        """--file reads input from disk."""
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

    def test_preview_verbose_shows_measurement_provenance(self):
        """--verbose must disclose how the measurement was produced."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "test", "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Mode:" in result.stdout
        assert "Measurement provenance:" in result.stdout
        assert "Input SHA-256" in result.stdout
        assert "Tokenizer" in result.stdout
        assert "Stages run" in result.stdout

    def test_preview_no_input_error(self):
        """Empty input is reported, not filled in with a fabricated number."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview"],
            input="",
            capture_output=True,
            text=True,
        )
        # Each preview state maps to its own documented code, so a caller can
        # tell "nothing to measure" from "the compressor failed". This asserted
        # a bare 1, which was the code every non-measured state shared.
        from tokenpak.cli.exit_codes import EXIT_NO_DATA

        assert result.returncode == EXIT_NO_DATA
        combined = result.stdout + result.stderr
        assert "Nothing to preview" in combined
        assert "input was empty" in combined
