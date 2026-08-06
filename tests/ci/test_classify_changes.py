"""Deterministic fixtures for changed-surface CI classification."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts.ci.classify_changes import (
    JOB_NAMES,
    ChangedPath,
    categories_for_path,
    classify_changes,
    conservative_error_summary,
    github_outputs,
    main,
    parse_name_status,
)


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("tokenpak/proxy/server.py", "python_proxy"),
        ("pyproject.toml", "packaging_dependencies"),
        ("tokenpak/telemetry/storage/migrations/001.py", "migration_storage"),
        ("tokenpak/proxy/spend_guard/policy.py", "pricing_billing"),
        ("README.md", "docs_claims"),
        ("sdk/src/index.ts", "javascript_sdk"),
        ("packages/tokenpak-js/src/index.ts", "javascript_sdk"),
        (".github/workflows/ci.yml", "workflow_release"),
        ("scripts/ci/classify_changes.py", "workflow_release"),
        ("tests/ci/test_classify_changes.py", "workflow_release"),
        ("tests/telemetry/test_storage.py", "migration_storage"),
        ("tests/cost/test_spend.py", "pricing_billing"),
    ],
)
def test_each_required_category_is_classified(path: str, category: str) -> None:
    assert category in categories_for_path(path)


def test_isolated_docs_change_selects_only_docs_job() -> None:
    summary = classify_changes([ChangedPath("M", "docs/usage.md")])

    assert summary["categories"]["docs_claims"] is True
    assert summary["signals"]["full_conservative"] is False
    assert summary["selections"]["docs_claims"] is True
    assert sum(summary["selections"].values()) == 1


def test_isolated_tokenpak_js_change_selects_javascript_without_unknown() -> None:
    summary = classify_changes([ChangedPath("M", "packages/tokenpak-js/src/index.ts")])

    assert summary["categories"]["javascript_sdk"] is True
    assert summary["categories"]["unknown"] is False
    assert summary["selections"]["javascript_sdk"] is True
    assert sum(summary["selections"].values()) == 1


def test_shared_core_change_is_full_conservative() -> None:
    summary = classify_changes([ChangedPath("M", "tokenpak/proxy/server.py")])

    assert summary["signals"]["shared_core"] is True
    assert summary["signals"]["full_conservative"] is True
    assert all(summary["selections"].values())


def test_combined_categories_are_multi_surface_and_full() -> None:
    summary = classify_changes(
        [
            ChangedPath("M", "docs/usage.md"),
            ChangedPath("M", "sdk/src/index.ts"),
        ]
    )

    assert summary["signals"]["multi_surface"] is True
    assert summary["signals"]["full_conservative"] is True


def test_explicit_full_conservative_keeps_a_real_delta_contract() -> None:
    summary = classify_changes(
        [ChangedPath("M", "docs/usage.md")],
        base="base-sha",
        head="head-sha",
        force_full_conservative=True,
    )

    assert summary["base"] == "base-sha"
    assert summary["head"] == "head-sha"
    assert summary["changes"]
    assert summary["reasons"] == ["forced_full_conservative"]
    assert summary["signals"]["full_conservative"] is True
    assert all(summary["selections"].values())


def test_rename_classifies_old_and_new_surfaces() -> None:
    summary = classify_changes(
        [ChangedPath("R100", "tokenpak/helpers/new_name.py", "sdk/src/old_name.ts")]
    )

    assert summary["categories"]["javascript_sdk"] is True
    assert summary["categories"]["python_proxy"] is True
    assert summary["signals"]["multi_surface"] is True


def test_deleted_file_keeps_its_surface_selection() -> None:
    summary = classify_changes([ChangedPath("D", "docs/retired.md")])

    assert summary["categories"]["docs_claims"] is True


def test_empty_diff_fails_conservative() -> None:
    summary = classify_changes([])

    assert summary["categories"]["unknown"] is True
    assert summary["signals"]["full_conservative"] is True
    assert all(summary["selections"][name] for name in JOB_NAMES)
    assert "empty_diff" in summary["reasons"]


def test_unknown_path_fails_conservative() -> None:
    summary = classify_changes([ChangedPath("A", "assets/product.bin")])

    assert summary["categories"]["unknown"] is True
    assert summary["unmatched_paths"] == ["assets/product.bin"]
    assert all(summary["selections"].values())


def test_large_diff_threshold_selects_full_coverage() -> None:
    summary = classify_changes(
        [ChangedPath("M", "docs/usage.md")],
        additions=400,
        deletions=100,
        large_diff_lines=500,
    )

    assert summary["signals"]["large_diff"] is True
    assert summary["signals"]["full_conservative"] is True


def test_classifier_error_selects_every_category_and_job() -> None:
    summary = conservative_error_summary("synthetic failure")

    assert summary["classifier_ok"] is False
    assert all(summary["categories"].values())
    assert all(summary["selections"].values())
    assert summary["error"] == "synthetic failure"


def test_name_status_parser_handles_modify_delete_and_rename() -> None:
    parsed = parse_name_status(
        b"M\0docs/usage.md\0D\0docs/old.md\0R100\0sdk/src/old.ts\0sdk/src/new.ts\0"
    )

    assert parsed == [
        ChangedPath("M", "docs/usage.md"),
        ChangedPath("D", "docs/old.md"),
        ChangedPath("R100", "sdk/src/new.ts", "sdk/src/old.ts"),
    ]


def test_github_outputs_are_strict_lowercase_booleans() -> None:
    outputs = github_outputs(classify_changes([ChangedPath("M", "docs/usage.md")]))

    assert set(outputs.values()) <= {"true", "false"}
    assert outputs["classifier_ok"] == "true"
    assert outputs["run_docs_claims"] == "true"
    assert outputs["run_full_python"] == "false"


def _git(repo, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=TokenPak",
            "-c",
            "user.email=hello@tokenpak.ai",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_cli_classifies_an_exact_git_range(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    base = _commit(repo, "baseline")
    sdk = repo / "sdk" / "src"
    sdk.mkdir(parents=True)
    (sdk / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
    head = _commit(repo, "sdk change")
    output = tmp_path / "selection.json"
    github_output = tmp_path / "github-output.txt"
    monkeypatch.chdir(repo)

    assert (
        main(
            [
                "--base",
                base,
                "--head",
                head,
                "--output-json",
                str(output),
                "--github-output",
                str(github_output),
            ]
        )
        == 0
    )
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["base"] == base
    assert summary["head"] == head
    assert summary["categories"]["javascript_sdk"] is True
    assert "run_javascript_sdk=true" in github_output.read_text(encoding="utf-8")

    forced_output = tmp_path / "forced-selection.json"
    assert (
        main(
            [
                "--base",
                base,
                "--head",
                head,
                "--output-json",
                str(forced_output),
                "--force-full-conservative",
            ]
        )
        == 0
    )
    forced = json.loads(forced_output.read_text(encoding="utf-8"))
    assert forced["reasons"] == ["forced_full_conservative"]
    assert all(forced["selections"].values())


def test_cli_git_error_fails_visible_and_conservative(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    output = tmp_path / "selection.json"
    monkeypatch.chdir(repo)

    assert (
        main(["--base", "missing-base", "--head", "missing-head", "--output-json", str(output)])
        == 2
    )
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["classifier_ok"] is False
    assert summary["reasons"] == ["classifier_error"]
    assert all(summary["selections"].values())
