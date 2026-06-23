"""CLI regression tests for OSS recipe discovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_tokenpak(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_recipe_list_lists_baked_in_catalog() -> None:
    result = _run_tokenpak("recipe", "list")

    assert result.returncode == 0, result.stderr
    assert "Baked-in Compression Recipes" in result.stdout
    assert "Total recipes: 50" in result.stdout
    assert "py-docstring-to-signature" in result.stdout


def test_recipe_list_filters_by_category() -> None:
    result = _run_tokenpak("recipe", "list", "--category", "python")

    assert result.returncode == 0, result.stderr
    assert "python (10)" in result.stdout
    assert "py-docstring-to-signature" in result.stdout
    assert "markdown (5)" not in result.stdout
