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


class TestPreviewInputTruth:
    """Input handling must never report a confident number for input it did not read.

    Each case here produced a plausible-looking result before: a file path was
    compressed as prose, a missing file raised a traceback, and malformed JSON
    was silently measured as text. A wrong number that looks right is worse
    than an error, because nothing prompts the caller to look again.
    """

    def test_positional_file_path_is_refused_not_compressed(self):
        """A path passed positionally must be refused, not measured as text."""
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "conversation.json"
            target.write_text(json.dumps([{"role": "user", "content": "hello " * 200}]))
            result = subprocess.run(
                [sys.executable, "-m", "tokenpak", "preview", str(target)],
                capture_output=True,
                text=True,
            )
        assert result.returncode != 0
        assert "--file" in result.stdout
        # The bug: the path string itself became the measured input.
        assert "Savings:" not in result.stdout

    def test_missing_file_reports_cleanly(self):
        """A missing --file target gets a message, not a traceback."""
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "preview", "--file", "/nonexistent/x.json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stdout + result.stderr
        assert "No such file" in result.stdout

    def test_malformed_json_is_refused_not_measured_as_text(self):
        """A .json file that does not parse must not be silently compressed."""
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text('{"broken": [')
            result = subprocess.run(
                [sys.executable, "-m", "tokenpak", "preview", "--file", str(bad)],
                capture_output=True,
                text=True,
            )
        assert result.returncode != 0
        assert "not valid JSON" in result.stdout
        assert "Savings:" not in result.stdout

    def test_valid_file_still_measures(self):
        """The corrections must not disturb the working path."""
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "conv.json"
            ctx = "namespace: production-east\ncluster: eks-prod-1\n" * 4
            good.write_text(
                json.dumps([{"role": "user", "content": f"{ctx}\nturn {i}"} for i in range(6)])
            )
            result = subprocess.run(
                [sys.executable, "-m", "tokenpak", "preview", "--file", str(good)],
                capture_output=True,
                text=True,
            )
        assert result.returncode == 0
        assert "Savings:" in result.stdout
