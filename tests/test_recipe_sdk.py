"""Tests for the TokenPak Recipe SDK (create / validate / test / benchmark).

Covers:
  1. Scaffold creates a valid file
  2. Scaffold with domain example (legal)
  3. Scaffold with domain example (medical)
  4. Validate passes on well-formed recipe
  5. Validate fails on missing required field
  6. Validate fails on bad compression_hint range
  7. Validate warns on unknown category
  8. Test with explicit input text
  9. Test pattern matching check
  10. Test ops_applied list
  11. Benchmark runs and returns expected keys
  12. Benchmark compression_hint vs actual delta
  13. CLI recipe create (smoke)
  14. CLI recipe validate (smoke)
  15. CLI recipe test (smoke)
  16. CLI recipe benchmark (smoke)
  17. _apply_operations: regex_replace
  18. _apply_operations: deduplicate_lines
  19. _apply_operations: collapse_whitespace
  20. _apply_operations: json_compact
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest
import yaml

# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_recipe(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return p


MINIMAL_VALID = {
    "name": "test-recipe",
    "category": "python",
    "description": "A test recipe.",
    "pattern": {"match": "extension", "extensions": [".py"]},
    "action": {
        "compression_hint": 0.2,
        "operations": [{"type": "strip_comments"}],
    },
}


# ── 1. Scaffold creates a valid file ─────────────────────────────────────────

def test_create_generates_file(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    out = sdk.create("my-test-recipe", output_dir=str(tmp_path))
    assert out.exists()
    assert out.suffix == ".yaml"
    assert "my-test-recipe" in out.name


# ── 2. Scaffold with domain example: legal ───────────────────────────────────

def test_create_domain_legal(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    out = sdk.create("legal-recipe", output_dir=str(tmp_path), domain_example="legal")
    content = out.read_text()
    assert "legal" in content.lower()
    assert "WHEREAS" in content or "boilerplate" in content.lower()


# ── 3. Scaffold with domain example: medical ─────────────────────────────────

def test_create_domain_medical(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    out = sdk.create("medical-recipe", output_dir=str(tmp_path), domain_example="medical")
    content = out.read_text()
    assert "medical" in content.lower() or "CONFIDENTIALITY" in content


# ── 4. Validate passes on well-formed recipe ──────────────────────────────────

def test_validate_passes_valid(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    warnings = sdk.validate(path)
    assert isinstance(warnings, list)


# ── 5. Validate fails on missing required field ───────────────────────────────

def test_validate_fails_missing_field(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK, RecipeValidationError
    sdk = RecipeSDK()
    bad = {k: v for k, v in MINIMAL_VALID.items() if k != "name"}
    path = _write_recipe(tmp_path, bad)
    with pytest.raises(RecipeValidationError, match="Missing required field"):
        sdk.validate(path)


# ── 6. Validate fails on bad compression_hint ────────────────────────────────

def test_validate_fails_bad_compression_hint(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK, RecipeValidationError
    sdk = RecipeSDK()
    bad = {**MINIMAL_VALID, "action": {**MINIMAL_VALID["action"], "compression_hint": 1.5}}
    path = _write_recipe(tmp_path, bad)
    with pytest.raises(RecipeValidationError, match="compression_hint"):
        sdk.validate(path)


# ── 7. Validate warns on unknown category ─────────────────────────────────────

def test_validate_warns_unknown_category(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    recipe = {**MINIMAL_VALID, "category": "financial"}
    path = _write_recipe(tmp_path, recipe)
    warnings = sdk.validate(path)
    assert any("Unknown category" in w for w in warnings)


# ── 8. Test with explicit input text ──────────────────────────────────────────

def test_test_with_input_text(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    result = sdk.test(path, input_text="# This is a comment\nreal_code = True\n")
    assert result["valid"] is True
    assert result["input_chars"] > 0
    assert result["output_chars"] <= result["input_chars"]
    assert "compression_ratio" in result
    assert "output_preview" in result


# ── 9. Pattern matching check ─────────────────────────────────────────────────

def test_test_pattern_match_extension(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    # Should match .py
    result = sdk.test(path, input_text="x = 1", filename_hint="script.py")
    assert result["pattern_match"] is True
    # Should NOT match .js
    result2 = sdk.test(path, input_text="x = 1", filename_hint="script.js")
    assert result2["pattern_match"] is False


# ── 10. Ops applied list ───────────────────────────────────────────────────────

def test_test_ops_applied(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    recipe = {
        **MINIMAL_VALID,
        "action": {
            "compression_hint": 0.1,
            "operations": [
                {"type": "strip_comments"},
                {"type": "collapse_whitespace"},
            ],
        },
    }
    path = _write_recipe(tmp_path, recipe)
    result = sdk.test(path, input_text="# comment\ncode = 1\n")
    assert "strip_comments" in result["ops_applied"]
    assert "collapse_whitespace" in result["ops_applied"]


# ── 11. Benchmark returns expected keys ───────────────────────────────────────

def test_benchmark_keys(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    result = sdk.benchmark(path, samples=["x = 1  # comment\n"], runs=2)
    assert "recipe" in result
    assert "compression" in result
    assert "timing_ms" in result
    assert "mean" in result["compression"]
    assert "mean" in result["timing_ms"]
    assert result["samples_tested"] == 1
    assert result["runs_per_sample"] == 2


# ── 12. Benchmark hint_vs_actual delta ────────────────────────────────────────

def test_benchmark_hint_vs_actual(tmp_path):
    from tokenpak.recipe_sdk import RecipeSDK
    sdk = RecipeSDK()
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    result = sdk.benchmark(path, samples=["# comment\n# more\ncode = 1"], runs=1)
    assert result["hint_vs_actual"]["hint"] == pytest.approx(0.2, abs=0.01)
    assert 0.0 <= result["hint_vs_actual"]["actual_mean"] <= 1.0


# ── 13–16. CLI smoke tests ─────────────────────────────────────────────────────

def _run_cli(args: list[str], *, input_text: str = "") -> tuple[int, str]:
    """Run CLI via tokenpak.cli.main() and capture stdout."""
    from tokenpak.cli import main
    old_argv = sys.argv
    old_stdout = sys.stdout
    sys.argv = ["tokenpak"] + args
    sys.stdout = buf = StringIO()
    try:
        main()
        rc = 0
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
    return rc, buf.getvalue()


def test_cli_recipe_create(tmp_path):
    rc, out = _run_cli(["recipe", "create", "cli-test-recipe", "--output-dir", str(tmp_path)])
    assert rc == 0
    assert "cli-test-recipe" in out or (tmp_path / "cli-test-recipe.yaml").exists()


def test_cli_recipe_validate(tmp_path):
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    rc, out = _run_cli(["recipe", "validate", str(path)])
    assert rc == 0
    assert "valid" in out.lower() or "✅" in out


def test_cli_recipe_test(tmp_path):
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    rc, out = _run_cli(["recipe", "test", str(path), "--input-text", "# comment\nx=1"])
    assert rc == 0
    assert "compression" in out.lower() or "Input" in out


def test_cli_recipe_benchmark(tmp_path):
    path = _write_recipe(tmp_path, MINIMAL_VALID)
    rc, out = _run_cli(["recipe", "benchmark", str(path), "--runs", "2"])
    assert rc == 0
    assert "compression" in out.lower() or "Timing" in out


# ── 17. _apply_operations: regex_replace ──────────────────────────────────────

def test_apply_regex_replace():
    from tokenpak.recipe_sdk import _apply_operations
    text = "foo bar baz"
    ops = [{"type": "regex_replace", "pattern": r"\bbar\b", "replacement": "REPLACED"}]
    result, applied = _apply_operations(text, ops)
    assert "REPLACED" in result
    assert "regex_replace" in applied


# ── 18. _apply_operations: deduplicate_lines ─────────────────────────────────

def test_apply_deduplicate_lines():
    from tokenpak.recipe_sdk import _apply_operations
    text = "line1\nline2\nline1\nline3\nline2"
    result, applied = _apply_operations(text, [{"type": "deduplicate_lines"}])
    lines = result.strip().splitlines()
    assert len(lines) == 3
    assert "deduplicate_lines" in applied


# ── 19. _apply_operations: collapse_whitespace ───────────────────────────────

def test_apply_collapse_whitespace():
    from tokenpak.recipe_sdk import _apply_operations
    text = "too   many   spaces\n\n\n\nmany blank lines"
    result, applied = _apply_operations(text, [{"type": "collapse_whitespace"}])
    assert "   " not in result
    assert "\n\n\n" not in result
    assert "collapse_whitespace" in applied


# ── 20. _apply_operations: json_compact ──────────────────────────────────────

def test_apply_json_compact():
    from tokenpak.recipe_sdk import _apply_operations
    text = json.dumps({"a": 1, "b": [1, 2, 3]}, indent=4)
    result, applied = _apply_operations(text, [{"type": "json_compact"}])
    assert " " not in result  # compacted
    assert "json_compact" in applied
    assert json.loads(result) == {"a": 1, "b": [1, 2, 3]}
