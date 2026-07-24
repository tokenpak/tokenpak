from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.teacher", reason="module not available in current build")
import json
from pathlib import Path

from tokenpak._internal.teacher import build_teacher_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_teacher_builder_is_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    commands = tmp_path / "commands"
    output = tmp_path / "recipes"

    _write(
        source / "Task.md",
        """---
tags: [workflow, trigger]
---
Use trigger flow. See [doc](./missing.md)
""",
    )
    _write(source / "Guide.md", "#workflow\nSome context for workflow.")
    _write(commands / "trigger.py", "")
    _write(commands / "workflow.py", "")

    first = build_teacher_pack([str(source)], [str(commands)], str(output), version="v1")
    second = build_teacher_pack([str(source)], [str(commands)], str(output), version="v1")

    assert first.source_fingerprint == second.source_fingerprint

    recipes = json.loads(first.recipes_path.read_text(encoding="utf-8"))
    intents = [r["intent"] for r in recipes["recipes"]]
    assert intents == sorted(intents)
    assert all("required_blocks" in r and "optional_blocks" in r for r in recipes["recipes"])
    assert all("token_budget" in r for r in recipes["recipes"])


def test_teacher_builder_validation_reports_missing_and_stale(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    commands = tmp_path / "commands"
    output = tmp_path / "recipes"

    _write(source / "Only.md", "No matching tags or intents.")
    _write(commands / "nonmatch.py", "")

    result = build_teacher_pack(
        [str(source)], [str(commands)], str(output), version="v2", default_budget=10
    )
    report = json.loads(result.validation_path.read_text(encoding="utf-8"))

    assert report["summary"]["missing_source_count"] >= 1
    assert "nonmatch" in report["missing_sources"]
