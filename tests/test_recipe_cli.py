"""CLI regression tests for OSS recipe discovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_RECIPES_DIR = REPO_ROOT / "tokenpak" / "recipes_oss"


def _run_tokenpak(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        # 60s (not 20s): the command completes in ~3s unloaded, but cold-start
        # import cost under host load can push a 20s budget over the edge.
        timeout=60,
    )


def test_recipe_list_lists_baked_in_catalog() -> None:
    result = _run_tokenpak("recipe", "list")

    assert result.returncode == 0, result.stderr
    assert "Baked-in Compression Recipes" in result.stdout
    assert "Total recipes: 57" in result.stdout
    assert "py-docstring-to-signature" in result.stdout


def test_recipe_list_filters_by_category() -> None:
    result = _run_tokenpak("recipe", "list", "--category", "python")

    assert result.returncode == 0, result.stderr
    assert "python (10)" in result.stdout
    assert "py-docstring-to-signature" in result.stdout
    assert "markdown (5)" not in result.stdout


def _shipped_recipe_facts() -> tuple[int, set[str]]:
    files = sorted(SHIPPED_RECIPES_DIR.glob("*.yaml"))
    categories = {yaml.safe_load(f.read_text(encoding="utf-8"))["category"] for f in files}
    return len(files), categories


def _flat_help(*args: str) -> str:
    result = _run_tokenpak(*args)
    assert result.returncode == 0, result.stderr
    # argparse wraps help text to the terminal width; compare on single spaces.
    return " ".join(result.stdout.split())


def test_demo_help_states_the_shipped_count_and_every_category() -> None:
    count, categories = _shipped_recipe_facts()
    text = _flat_help("demo", "--help")

    assert f"List the {count} baked-in recipes" in text
    for category in sorted(categories):
        assert category in text, f"demo --category help omits {category!r}"


def test_recipe_list_help_names_every_shipped_category() -> None:
    _, categories = _shipped_recipe_facts()
    text = _flat_help("recipe", "list", "--help")

    for category in sorted(categories):
        assert category in text, f"recipe list --category help omits {category!r}"
