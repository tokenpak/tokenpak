"""Tests for structural checks in the always-on trust baseline."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.trust_baseline import (
    REQUIRED_TOP_LEVEL,
    check_branch_name,
    check_hygiene,
    check_layout,
)


def _valid_tree(root: Path) -> None:
    for name in REQUIRED_TOP_LEVEL:
        path = root / name
        if "." in name and not name.startswith("."):
            path.write_text("ok\n", encoding="utf-8")
        elif name in {".gitignore", "LICENSE"}:
            path.write_text("ok\n", encoding="utf-8")
        else:
            path.mkdir()


def test_valid_public_layout_passes(tmp_path: Path) -> None:
    _valid_tree(tmp_path)
    assert check_layout(tmp_path) == []


def test_forbidden_and_missing_layout_entries_fail(tmp_path: Path) -> None:
    _valid_tree(tmp_path)
    (tmp_path / "portal").mkdir()
    (tmp_path / "README.md").unlink()

    errors = check_layout(tmp_path)

    assert "forbidden top-level path is present: portal" in errors
    assert "required top-level path is missing: README.md" in errors


def test_hygiene_detects_forbidden_filename(tmp_path: Path) -> None:
    path = tmp_path / "notes.bak"
    path.write_text("backup\n", encoding="utf-8")

    assert check_hygiene(tmp_path, ["notes.bak"]) == ["forbidden tracked filename: notes.bak"]


def test_hygiene_detects_apex_schema_url(tmp_path: Path) -> None:
    path = tmp_path / "docs.md"
    path.write_text("https://" + "tokenpak.ai" + "/schemas/example.json\n", encoding="utf-8")

    assert check_hygiene(tmp_path, ["docs.md"]) == ["apex-host schema URL is present: docs.md"]


def test_release_branch_name_is_public_safe() -> None:
    assert check_branch_name("release/v1.18.2") == []


def test_internal_identity_in_branch_is_rejected() -> None:
    branch = "feature/foo-" + "ca" + "li" + "-2026"
    assert check_branch_name(branch) == ["branch name contains an internal identity"]


def test_numeric_bug_branch_is_not_misclassified_as_internal() -> None:
    assert check_branch_name("fix/http-404") == []
