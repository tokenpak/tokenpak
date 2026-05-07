"""Tests for OSS compression recipes (YAML) and CompressionRecipeEngine.

Covers: loading, schema validation, category counts, file matching,
and per-category sample recipe content checks.
At least 2 tests per category (10 categories × 2+ = 20+ tests).
"""
from __future__ import annotations

# Allow running from project root
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.compression.recipes import (
    CompressionRecipe,
    CompressionRecipeEngine,
)

RECIPES_DIR = Path(__file__).parent.parent / "recipes" / "oss"


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine() -> CompressionRecipeEngine:
    eng = CompressionRecipeEngine()
    eng.load_from_dir(RECIPES_DIR)
    return eng


# ─── Schema / loading tests ──────────────────────────────────────────────────

def test_recipes_dir_exists():
    assert RECIPES_DIR.exists(), f"recipes/oss dir missing: {RECIPES_DIR}"


def test_fifty_yaml_files():
    files = list(RECIPES_DIR.glob("*.yaml")) + list(RECIPES_DIR.glob("*.yml"))
    assert len(files) == 50, f"Expected 50 recipe files, found {len(files)}"


def test_engine_loads_all_fifty(engine):
    assert len(engine.list_recipes()) == 50


def test_each_yaml_is_valid_schema():
    """Every YAML must parse into a valid CompressionRecipe without error."""
    errors = []
    for f in sorted(RECIPES_DIR.glob("*.yaml")):
        with f.open() as fh:
            data = yaml.safe_load(fh)
        try:
            CompressionRecipe.from_dict(data, source=str(f))
        except (ValueError, TypeError) as exc:
            errors.append(f"{f.name}: {exc}")
    assert not errors, "Schema errors:\n" + "\n".join(errors)


def test_no_duplicate_names(engine):
    names = engine.list_recipes()
    assert len(names) == len(set(names)), "Duplicate recipe names found"


def test_summary_total(engine):
    s = engine.summary()
    assert s["total"] == 50


# ─── Category count tests ─────────────────────────────────────────────────────

def test_category_general_count(engine):
    assert len(engine.by_category("general")) == 10


def test_category_python_count(engine):
    assert len(engine.by_category("python")) == 10


def test_category_javascript_count(engine):
    assert len(engine.by_category("javascript")) == 10


def test_category_markdown_count(engine):
    assert len(engine.by_category("markdown")) == 5


def test_category_config_count(engine):
    assert len(engine.by_category("config")) == 5


def test_category_common_patterns_count(engine):
    assert len(engine.by_category("common_patterns")) == 10


# ─── GENERAL recipes ─────────────────────────────────────────────────────────

def test_gen_whitespace_normalization_exists(engine):
    r = engine.get_recipe("gen-whitespace-normalization")
    assert r is not None
    assert r.match_mode == "any"


def test_gen_filler_phrase_removal_has_operation(engine):
    r = engine.get_recipe("gen-filler-phrase-removal")
    assert r is not None
    ops = r.operations
    assert any(op.get("type") == "regex_replace" for op in ops)


# ─── PYTHON recipes ──────────────────────────────────────────────────────────

def test_py_docstring_to_signature_matches_py(engine):
    r = engine.get_recipe("py-docstring-to-signature")
    assert r is not None
    assert r.matches(filename="models.py")
    assert not r.matches(filename="index.html")


def test_py_import_dedup_sort_has_operations(engine):
    r = engine.get_recipe("py-import-dedup-sort")
    assert r is not None
    assert len(r.operations) >= 1


def test_py_test_skeleton_matches_py(engine):
    r = engine.get_recipe("py-test-skeleton")
    assert r is not None
    assert r.matches(filename="test_models.py")


# ─── JAVASCRIPT recipes ───────────────────────────────────────────────────────

def test_js_import_dedup_matches_js_ts(engine):
    r = engine.get_recipe("js-import-dedup")
    assert r is not None
    assert r.matches(filename="app.js")
    assert r.matches(filename="app.ts")
    assert not r.matches(filename="app.py")


def test_js_package_json_matches_filename(engine):
    r = engine.get_recipe("js-package-json-trimming")
    assert r is not None
    assert r.matches(filename="/project/package.json")
    assert not r.matches(filename="requirements.txt")


def test_ts_interface_extraction_matches_ts(engine):
    r = engine.get_recipe("ts-interface-extraction")
    assert r is not None
    assert r.matches(filename="types.d.ts")
    assert r.matches(filename="api.ts")


# ─── MARKDOWN recipes ─────────────────────────────────────────────────────────

def test_md_code_block_compression_matches_md(engine):
    r = engine.get_recipe("md-code-block-compression")
    assert r is not None
    assert r.matches(filename="README.md")
    assert r.matches(filename="docs/guide.markdown")


def test_md_table_compression_exists(engine):
    r = engine.get_recipe("md-table-compression")
    assert r is not None
    assert r.compression_hint > 0


# ─── CONFIG recipes ───────────────────────────────────────────────────────────

def test_cfg_json_matches_json(engine):
    r = engine.get_recipe("cfg-json-whitespace-normalization")
    assert r is not None
    assert r.matches(filename="config.json")
    assert not r.matches(filename="config.yaml")


def test_cfg_env_dedup_matches_dotenv(engine):
    r = engine.get_recipe("cfg-env-dedup")
    assert r is not None
    assert r.matches(filename=".env")
    assert r.matches(filename=".env.local")


def test_cfg_docker_matches_dockerfile(engine):
    r = engine.get_recipe("cfg-docker-trimming")
    assert r is not None
    assert r.matches(filename="Dockerfile")
    assert r.matches(filename="docker-compose.yml")


# ─── COMMON PATTERNS recipes ─────────────────────────────────────────────────

def test_cp_stack_trace_matches_content(engine):
    r = engine.get_recipe("cp-stack-trace-trimming")
    assert r is not None
    assert r.matches(content_sample="Traceback (most recent call last)\n  File")


def test_cp_log_output_matches_log_content(engine):
    r = engine.get_recipe("cp-log-output-compression")
    assert r is not None
    assert r.matches(content_sample="2024-01-01 INFO Starting service\n")


def test_cp_csv_sample_matches_csv(engine):
    r = engine.get_recipe("cp-csv-sample-only")
    assert r is not None
    assert r.matches(filename="data.csv")
    assert r.matches(filename="export.tsv")


def test_cp_html_to_text_matches_html(engine):
    r = engine.get_recipe("cp-html-to-text")
    assert r is not None
    assert r.matches(filename="index.html")
    assert r.matches(filename="page.htm")


# ─── recipes_for_file ordering ───────────────────────────────────────────────

def test_recipes_for_file_sorted_by_hint_desc(engine):
    recipes = engine.recipes_for_file("data.csv")
    if len(recipes) >= 2:
        hints = [r.compression_hint for r in recipes]
        assert hints == sorted(hints, reverse=True)


def test_recipes_for_file_python(engine):
    recipes = engine.recipes_for_file("utils.py")
    names = [r.name for r in recipes]
    assert "py-docstring-to-signature" in names
    assert "py-import-dedup-sort" in names


def test_recipes_for_file_html(engine):
    recipes = engine.recipes_for_file("index.html")
    names = [r.name for r in recipes]
    assert "cp-html-to-text" in names


# ─── No executable code in recipe files ──────────────────────────────────────

def test_no_executable_code_in_yamls():
    """Recipes must be purely declarative — no Python/JS snippets."""
    dangerous = ["exec(", "eval(", "import os", "subprocess", "__import__"]
    violations = []
    for f in RECIPES_DIR.glob("*.yaml"):
        text = f.read_text()
        for d in dangerous:
            if d in text:
                violations.append(f"{f.name}: contains '{d}'")
    assert not violations, "Executable code found:\n" + "\n".join(violations)


# ─── CompressionRecipe.from_dict validation ───────────────────────────────────

def test_from_dict_missing_field_raises():
    with pytest.raises(ValueError, match="missing field"):
        CompressionRecipe.from_dict(
            {"name": "x", "category": "general", "description": "d"},
            source="test",
        )


def test_from_dict_empty_name_raises():
    with pytest.raises(ValueError, match="empty name"):
        CompressionRecipe.from_dict(
            {"name": "  ", "category": "general", "description": "d",
             "pattern": {"match": "any"}, "action": {"operations": []}},
            source="test",
        )


def test_from_dict_valid():
    r = CompressionRecipe.from_dict(
        {
            "name": "my-recipe",
            "category": "general",
            "description": "A test recipe",
            "pattern": {"match": "any"},
            "action": {"operations": [{"type": "regex_replace", "pattern": r"\s+", "replacement": " "}]},
        },
        source="test",
    )
    assert r.name == "my-recipe"
    assert r.match_mode == "any"
    assert len(r.operations) == 1
