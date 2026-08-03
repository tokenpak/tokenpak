"""Behavioral tests for the 2026-07 recipe additions.

Every recipe added in this wave must (a) declare only operations the SDK
executor implements — no inert vocabulary — and (b) show a measurable
reduction on a realistic sample while preserving the semantics it promises
to preserve.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tokenpak.compression.recipe_sdk import RECIPE_SCHEMA, _apply_operations

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes" / "oss"

NEW_RECIPES = [
    "cp-ansi-escape-stripping",
    "cfg-lockfile-trimming",
    "cp-notebook-output-stripping",
    "cfg-toml-comment-stripping",
    "rs-comment-density-reduction",
    "go-comment-density-reduction",
    "cp-shell-script-comment-stripping",
]


def _load(name: str) -> dict:
    return yaml.safe_load((RECIPES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _run(name: str, text: str) -> tuple[str, list[str]]:
    ops = _load(name)["action"]["operations"]
    return _apply_operations(text, ops)


@pytest.mark.parametrize("name", NEW_RECIPES)
def test_all_declared_ops_are_executable(name: str) -> None:
    """The gate rule: additions may not introduce inert operation vocabulary."""
    known = set(RECIPE_SCHEMA["known_operation_types"])
    ops = _load(name)["action"]["operations"]
    assert ops, f"{name}: no operations declared"
    unknown = [op["type"] for op in ops if op["type"] not in known]
    assert not unknown, f"{name}: declares non-executable ops {unknown}"


@pytest.mark.parametrize("name", NEW_RECIPES)
def test_category_is_known(name: str) -> None:
    data = _load(name)
    known = set(RECIPE_SCHEMA["known_categories"])
    # common_patterns predates this wave and is reconciled separately.
    if data["category"] == "common_patterns":
        pytest.skip("legacy category, reconciled in its own lane")
    assert data["category"] in known


def test_ansi_stripping() -> None:
    sample = (
        "\x1b[1;32mPASS\x1b[0m tests/test_ok.py\n"
        "\x1b]0;window-title\x07plain text stays\n"
        "progress 50%\r\nprogress 100%\n"
    )
    out, applied = _run("cp-ansi-escape-stripping", sample)
    assert "regex_replace" in applied
    assert "\x1b" not in out
    assert "PASS tests/test_ok.py" in out
    assert "plain text stays" in out
    assert len(out) < len(sample)


def test_lockfile_trimming_json() -> None:
    lock = json.dumps(
        {
            "name": "app",
            "packages": {
                "node_modules/left-pad": {
                    "version": "1.3.0",
                    "integrity": "sha512-" + "A" * 88,
                }
            },
        },
        indent=2,
    )
    out, applied = _run("cfg-lockfile-trimming", lock)
    assert "json_compact" in applied
    assert "A" * 24 not in out, "long integrity hash should be shortened"
    assert '"version"' in out
    assert len(out) < len(lock)


def test_lockfile_trimming_text() -> None:
    lock = (
        "# yarn lockfile v1\n\n\n"
        'left-pad@^1.3.0:\n  version "1.3.0"\n'
        "  resolved https://registry.example/left-pad#" + "a1b2c3d4" * 6 + "\n"
    )
    out, _ = _run("cfg-lockfile-trimming", lock)
    assert "# yarn lockfile" not in out
    assert "a1b2c3d4a1b2c3d4" not in out, "40+ char hex hash should be shortened"
    assert 'version "1.3.0"' in out


def test_notebook_output_stripping() -> None:
    nb = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": 7,
                    "source": ["print('hello')"],
                    "outputs": [{"data": {"image/png": "iVBORw0KGgo" + "B" * 900}}],
                }
            ],
            "nbformat": 4,
        },
        indent=1,
    )
    out, applied = _run("cp-notebook-output-stripping", nb)
    assert "json_compact" in applied
    assert "B" * 64 not in out, "base64 payload should be stripped"
    assert "print('hello')" in out, "cell source must survive"
    assert '"execution_count": null' in out or '"execution_count":null' in out
    assert len(out) < len(nb) / 2


def test_toml_comment_stripping() -> None:
    toml = (
        "# build configuration\n\n\n"
        "[project]\n"
        'name = "app"  # inline note\n'
        'requires-python = ">=3.10"\n'
    )
    out, applied = _run("cfg-toml-comment-stripping", toml)
    assert "strip_comments" in applied
    assert "# build configuration" not in out
    assert 'name = "app"' in out
    assert "\n\n\n" not in out


def test_rust_comment_reduction_keeps_doc_comments() -> None:
    src = (
        "/// Public API doc — keep.\n"
        "//! Module doc — keep.\n"
        "// implementation note — strip\n"
        "fn main() {\n"
        "    let x = 1; // trailing note — strip\n"
        "}\n"
    )
    out, _ = _run("rs-comment-density-reduction", src)
    assert "/// Public API doc" in out
    assert "//! Module doc" in out
    assert "implementation note" not in out
    assert "trailing note" not in out
    assert "let x = 1;" in out


def test_go_comment_reduction_keeps_full_line_comments() -> None:
    src = (
        "//go:build linux\n"
        "// Package demo does demo things.\n"
        "package demo\n"
        "var x = 1 // trailing — strip\n"
    )
    out, _ = _run("go-comment-density-reduction", src)
    assert "//go:build linux" in out, "build directives must survive"
    assert "// Package demo" in out, "doc comments must survive"
    assert "trailing" not in out
    assert "var x = 1" in out


def test_shell_comment_stripping_keeps_shebang() -> None:
    src = (
        "#!/usr/bin/env bash\n"
        "# helper script — strip this comment\n\n\n"
        "set -euo pipefail\n"
        "echo done\n"
    )
    out, applied = _run("cp-shell-script-comment-stripping", src)
    assert "regex_replace" in applied
    assert out.startswith("#!/usr/bin/env bash"), "shebang must survive"
    assert "helper script" not in out
    assert "set -euo pipefail" in out
    assert "\n\n\n" not in out
